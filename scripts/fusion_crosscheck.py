"""Compare reconstructed AASX content with Fusion 360 Gallery native values.

Reference: assembly.json written by Fusion 360 from its native model (Autodesk
kernel). The same Fusion export produced assembly.step, so this checks the
STEP->AAS chain (OCCT reading + mapping + packaging), not exporter fidelity.
Fusion lengths are cm; package values are mm.
"""
from __future__ import annotations

import json
import math
import pathlib
import sys
import zipfile

import numpy as np
from lxml import etree
from scipy.optimize import linear_sum_assignment

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import reconstruct_aasx as rx  # noqa: E402


def fusion_leaves(j):
    """Leaf occurrences with own bodies: (world 4x4 [mm], volume mm3, area mm2, com mm)."""
    occ = j["occurrences"]
    out = []

    def mat(tr):
        m = np.eye(4)
        for c, ax in enumerate(("x_axis", "y_axis", "z_axis")):
            m[:3, c] = [tr[ax][a] for a in "xyz"]
        m[:3, 3] = [tr["origin"][a] * 10.0 for a in "xyz"]
        return m

    def walk(d, parent):
        for k, ch in d.items():
            o = occ[k]
            if not o.get("is_visible", True):
                continue  # hidden occurrences are not exported to STEP
            w = parent @ mat(o["transform"])
            pp = o.get("physical_properties") or {}
            nb = len(o.get("bodies") or {})
            if not ch or nb:
                if nb or not ch:
                    out.append(dict(world=w, raw=mat(o["transform"]), bodies=nb, children=bool(ch),
                                    vol=pp.get("volume", 0) * 1000.0, area=pp.get("area", 0) * 100.0,
                                    com=np.array([pp["center_of_mass"][a] * 10.0 for a in "xyz"]) if pp.get("center_of_mass") else None))
            walk(ch, w)
    walk(j["tree"]["root"], np.eye(4))
    if j["root"].get("bodies"):
        # root bodies form one leaf without its own physical properties in the JSON
        out.append(dict(world=np.eye(4), raw=np.eye(4), bodies=len(j["root"]["bodies"]), children=True,
                        vol=None, area=None, com=None))
    return out


def package_parts(xml_bytes):
    root = etree.fromstring(xml_bytes)
    ns = {"a": etree.QName(root).namespace}
    props = {}
    for aas in root.findall("a:assetAdministrationShells/a:assetAdministrationShell", ns):
        gid = aas.findtext("a:assetInformation/a:globalAssetId", namespaces=ns)
        props[gid] = {"sm": [r.findtext("a:keys/a:key/a:value", namespaces=ns)
                             for r in aas.findall("a:submodels/a:reference", ns)]}
    vals = {}
    for sm in root.findall("a:submodels/a:submodel", ns):
        sid = sm.findtext("a:id", namespaces=ns)
        d = {}
        for p in sm.iter(f"{{{ns['a']}}}property"):
            n = p.findtext("a:idShort", namespaces=ns)
            if n in ("Volume", "SurfaceArea", "AxisX", "AxisY", "AxisZ"):
                try:
                    d[n] = float(p.findtext("a:value", namespaces=ns))
                except (TypeError, ValueError):
                    pass
        if d:
            vals[sid] = d
    out = {}
    for gid, p in props.items():
        d = {}
        for sid in p["sm"]:
            d.update(vals.get(sid, {}))
        out[gid] = d
    return out


def compare(zip_path: str, json_path: str) -> dict:
    j = json.load(open(json_path))
    pkg = rx.load(zip_path)
    s = rx.stats(pkg)
    with zipfile.ZipFile(zip_path) as z:
        xml = z.read(next(n for n in z.namelist() if n.endswith("data.xml")))
    parts = package_parts(xml)
    fl = fusion_leaves(j)
    res = {"pkg_leaves": s["leaf_occurrences"], "fusion_leaves": len(fl),
           "fusion_props_volume": j["properties"].get("volume", 0) * 1000.0,
           "fusion_props_area": j["properties"].get("area", 0) * 100.0}
    root_vals = parts.get(pkg.root_gid, {})
    res["pkg_root_volume"] = root_vals.get("Volume")
    res["pkg_root_area"] = root_vals.get("SurfaceArea")
    scene = max(pkg.scenes.values(), key=lambda x: len(x["instances"])) if pkg.scenes else {"instances": []}
    inst = scene["instances"]
    if not inst and pkg.root_gid in parts:  # single part: identity placement
        inst = [{"asset": pkg.root_gid, "world": list(np.eye(4).ravel())}]
    P = []
    for i in inst:
        w = np.array(i["world"]).reshape(4, 4)
        v = parts.get(i["asset"], {})
        c = np.array([v.get("AxisX", np.nan), v.get("AxisY", np.nan), v.get("AxisZ", np.nan), 1.0])
        P.append(dict(world=w, vol=v.get("Volume"), area=v.get("SurfaceArea"), com=(w @ c)[:3]))
    F = [f for f in fl if f["vol"] is not None and f["com"] is not None]
    res["matched"] = 0
    if P and F:
        diag = max(1e-9, float(np.linalg.norm(np.ptp(np.array([p["com"] for p in P if np.all(np.isfinite(p["com"]))] or [[0, 0, 0]]), axis=0))) or 1.0)
        # Match on pose (translation relative to the assembly size, rotation
        # angle) and world centroid; volume and area do not enter the matching.
        C = np.zeros((len(P), len(F)))
        for a, p in enumerate(P):
            for b, f in enumerate(F):
                dt = np.linalg.norm(p["world"][:3, 3] - f["world"][:3, 3]) / diag
                R = p["world"][:3, :3].T @ f["world"][:3, :3]
                ang = math.acos(max(-1.0, min(1.0, (np.trace(R) - 1) / 2)))
                dc = (np.linalg.norm(p["com"] - f["com"]) / diag) if np.all(np.isfinite(p["com"])) else 5.0
                C[a, b] = dt + ang + dc
        r, c = linear_sum_assignment(C)
        rv, ra, rc, rr = [], [], [], []
        for a, b in zip(r, c):
            p, f = P[a], F[b]
            if p["vol"] and f["vol"]:
                rv.append(abs(p["vol"] - f["vol"]) / f["vol"])
            if p["area"] and f["area"]:
                ra.append(abs(p["area"] - f["area"]) / f["area"])
            if np.all(np.isfinite(p["com"])):
                rc.append(float(np.linalg.norm(p["com"] - f["com"]) / diag))
            R = p["world"][:3, :3].T @ f["world"][:3, :3]
            rr.append(float(np.degrees(np.arccos(np.clip((np.trace(R) - 1) / 2, -1, 1)))))
        res.update(matched=len(r), vol_err=rv, area_err=ra, com_err=rc, rot_err_deg=rr, diag_mm=diag)
    return res


if __name__ == "__main__":
    print(json.dumps(compare(sys.argv[1], sys.argv[2]), default=float)[:2000])
