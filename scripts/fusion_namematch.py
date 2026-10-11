"""Name-based variant of the Fusion 360 cross-check.

Occurrences are paired by component name (Fusion component name = STEP product
name = AAS displayName). Volume and area are then compared without using any
geometry for the pairing. Among occurrences of the same component, which are
copies of one design, the order is fixed by pose; centroid and orientation
errors therefore still use pose only to tell identical copies apart.

Usage: python fusion_namematch.py <flat_zip_dir> <fusion_dir> <eval_json> <out.json>
"""
from __future__ import annotations

import collections
import json
import math
import pathlib
import re
import sys
import unicodedata
import zipfile

import numpy as np
from lxml import etree
from scipy.optimize import linear_sum_assignment

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fusion_crosscheck as fc  # noqa: E402
import reconstruct_aasx as rx  # noqa: E402


def norm(s: str | None) -> str:
    """Component name without Fusion's occurrence suffix (":1", ":2", ...)."""
    s = unicodedata.normalize("NFC", (s or "")).strip()
    return re.sub(r":\d+$", "", s).strip().casefold()


def fusion_named_leaves(j):
    leaves = fc.fusion_leaves(j)
    # fusion_leaves walks the tree in the same order; recover names alongside
    occ, comps = j["occurrences"], j["components"]
    names = []

    def walk(d):
        for k, ch in d.items():
            o = occ[k]
            if not o.get("is_visible", True):
                continue
            nb = len(o.get("bodies") or {})
            if not ch or nb:
                names.append(comps.get(o["component"], {}).get("name", o.get("name")))
            walk(ch)
    walk(j["tree"]["root"])
    if j["root"].get("bodies"):
        names.append(j["root"].get("name"))
    for f, n in zip(leaves, names):
        f["name"] = n
    return leaves


def display_names(xml: bytes) -> dict:
    root = etree.fromstring(xml)
    ns = {"a": etree.QName(root).namespace}
    out = {}
    for aas in root.findall("a:assetAdministrationShells/a:assetAdministrationShell", ns):
        gid = aas.findtext("a:assetInformation/a:globalAssetId", namespaces=ns)
        dn = aas.findtext("a:displayName/a:langStringNameType/a:text", namespaces=ns)
        out[gid] = dn or aas.findtext("a:idShort", namespaces=ns)
    return out


def compare(zip_path: str, json_path: str) -> dict:
    j = json.load(open(json_path))
    pkg = rx.load(zip_path)
    with zipfile.ZipFile(zip_path) as z:
        xml = z.read(next(n for n in z.namelist() if n.endswith("data.xml")))
    parts, names = fc.package_parts(xml), display_names(xml)
    scene = max(pkg.scenes.values(), key=lambda x: len(x["instances"])) if pkg.scenes else {"instances": []}
    P = []
    for i in scene["instances"]:
        w = np.array(i["world"]).reshape(4, 4)
        v = parts.get(i["asset"], {})
        c = np.array([v.get("AxisX", np.nan), v.get("AxisY", np.nan), v.get("AxisZ", np.nan), 1.0])
        P.append(dict(world=w, vol=v.get("Volume"), area=v.get("SurfaceArea"), com=(w @ c)[:3],
                      name=norm(names.get(i["asset"]))))
    F = [f for f in fusion_named_leaves(j) if f["vol"] is not None and f["com"] is not None]
    for f in F:
        f["name"] = norm(f["name"])
    res = {"pkg": len(P), "fusion": len(F)}
    if not P or not F:
        return res
    diag = max(1e-9, float(np.linalg.norm(np.ptp(np.array([p["com"] for p in P if np.all(np.isfinite(p["com"]))] or [[0, 0, 0]]), axis=0))) or 1.0)
    big = 1e6
    C = np.full((len(P), len(F)), big)
    for a, p in enumerate(P):
        for b, f in enumerate(F):
            if p["name"] and p["name"] == f["name"]:
                dt = np.linalg.norm(p["world"][:3, 3] - f["world"][:3, 3]) / diag
                R = p["world"][:3, :3].T @ f["world"][:3, :3]
                C[a, b] = dt + math.acos(max(-1.0, min(1.0, (np.trace(R) - 1) / 2)))
    r, c = linear_sum_assignment(C)
    ok = [(a, b) for a, b in zip(r, c) if C[a, b] < big]
    rv, ra, rc, rr = [], [], [], []
    for a, b in ok:
        p, f = P[a], F[b]
        if p["vol"] and f["vol"]:
            rv.append(abs(p["vol"] - f["vol"]) / f["vol"])
        if p["area"] and f["area"]:
            ra.append(abs(p["area"] - f["area"]) / f["area"])
        if np.all(np.isfinite(p["com"])):
            rc.append(float(np.linalg.norm(p["com"] - f["com"]) / diag))
        R = p["world"][:3, :3].T @ f["world"][:3, :3]
        rr.append(float(np.degrees(np.arccos(np.clip((np.trace(R) - 1) / 2, -1, 1)))))
    res.update(name_matched=len(ok), vol_err=rv, area_err=ra, com_err=rc, rot_err_deg=rr)
    return res


def main(zip_dir, fusion_dir, eval_json, out):
    ev = [x for x in json.load(open(eval_json)) if x["mode"] == "flat"]
    agree = [x for x in ev if x["leaf_occurrences"] == x["f_leaves"] and x.get("fusion", {}).get("matched")]
    tot = collections.Counter(); series = collections.defaultdict(list); per = []
    for x in agree:
        r = compare(f"{zip_dir}/{x['id']}-flat.zip", f"{fusion_dir}/{x['id']}/assembly.json")
        tot["pkg"] += r.get("pkg", 0); tot["matched"] += r.get("name_matched", 0)
        for k in ("vol_err", "area_err", "com_err", "rot_err_deg"):
            series[k] += r.get(k, [])
        per.append({"id": x["id"], "pkg": r.get("pkg"), "name_matched": r.get("name_matched")})
    def share(v, t): v = np.array(v); return float(np.mean(v <= t)) if len(v) else None
    summ = {"assemblies": len(agree), "occurrences": tot["pkg"], "name_matched": tot["matched"],
            "vol_within_1pct": share(series["vol_err"], 0.01), "area_within_1pct": share(series["area_err"], 0.01),
            "vol_within_1e-6": share(series["vol_err"], 1e-6),
            "com_within_0.1pct": share(series["com_err"], 1e-3), "rot_within_0.01deg": share(series["rot_err_deg"], 0.01),
            "n_vol": len(series["vol_err"]), "n_area": len(series["area_err"]),
            "vol_out_1pct": int(np.sum(np.array(series["vol_err"]) > 0.01)),
            "unmatched": tot["pkg"] - tot["matched"]}
    json.dump({"summary": summ, "series": series, "per_assembly": per}, open(out, "w"))
    print(json.dumps(summ, indent=1))


if __name__ == "__main__":
    main(*sys.argv[1:5])
