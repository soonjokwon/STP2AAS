"""Evaluate generated packages against the composition model and Fusion values.

Inputs: reduced package archives (data.xml, JSON files, file-size list) named
<id>-<mode>.zip, and the Fusion 360 Gallery assembly.json for each <id>.
Outputs one JSON record per (id, mode). Uses only the package-only reader and
Fusion data; no converter imports.
"""
from __future__ import annotations

import collections
import json
import pathlib
import sys
import zipfile

from lxml import etree

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fusion_crosscheck as fx  # noqa: E402
import reconstruct_aasx as rx  # noqa: E402

MODES = ("hierarchical", "flat", "single")


def fusion_structure(j: dict) -> dict:
    """Multiplicities and the conflict set from the native Fusion tree."""
    occ = j["occurrences"]
    mult: collections.Counter = collections.Counter()
    is_asm: set = set()
    leaves = []  # (path components, has_bodies)

    def walk(d, path):
        for k, ch in d.items():
            if not occ[k].get("is_visible", True):
                continue  # hidden occurrences are not exported to STEP
            c = occ[k]["component"]
            mult[c] += 1
            p = path + (c,)
            if ch:
                is_asm.add(c)
                if occ[k].get("bodies"):
                    leaves.append(p)
                walk(ch, p)
            else:
                leaves.append(p)

    walk(j["tree"]["root"], ())
    if j["root"].get("bodies"):
        leaves.append(("__root__",))
    repeated = {c for c, m in mult.items() if m >= 2}
    in_c = sum(1 for p in leaves if any(c in repeated for c in p[:-1]))
    return {
        "leaves": len(leaves),
        "leaves_in_C": in_c,
        "repeated": len(repeated),
        "repeated_parts": len(repeated - is_asm),
        "repeated_asm": len(repeated & is_asm),
        "depth": max((len(p) for p in leaves), default=0),
        "occurrences": len(occ),
    }


def predictions(h_pkg) -> dict:
    """Model predictions (Prop. 1, Prop. 2) from the hierarchical package, where
    every prototype has a shell and therefore an identifier."""
    tree = rx.expand(h_pkg)
    mult: collections.Counter = collections.Counter()
    leaves = []

    def walk(o, anc):
        for c in o.children:
            mult[c.gid] += 1
            if c.children:
                walk(c, anc + (c.gid,))
            else:
                leaves.append((anc, c.gid))
    walk(tree, ())
    rep = {g for g, m in mult.items() if m >= 2}
    asm = set()

    def walk2(o):
        for c in o.children:
            if c.children:
                asm.add(c.gid)
                walk2(c)
    walk2(tree)
    in_c = sum(1 for anc, _g in leaves if any(a in rep for a in anc))
    if not tree.children:
        leaves = [((), tree.gid)]
    return {"p_leaves": len(leaves), "p_repeated": len(rep), "p_repeated_parts": len(rep - asm),
            "p_repeated_asm": len(rep & asm), "p_leaves_in_C": in_c,
            "p_unique_prototypes": len(mult) + 1, "p_unique_asm": len(asm) + 1}


def xsd_ok(zip_path: str, schema: etree.XMLSchema) -> bool:
    with zipfile.ZipFile(zip_path) as z:
        root = etree.fromstring(z.read(next(n for n in z.namelist() if n.endswith("data.xml"))))
    return bool(schema.validate(root))


def main(zip_dir: str, fusion_dir: str, xsd: str, out: str) -> None:
    schema = etree.XMLSchema(etree.parse(xsd))
    zdir, fdir = pathlib.Path(zip_dir), pathlib.Path(fusion_dir)
    ids = sorted({p.name.rsplit("-", 1)[0] for p in zdir.glob("*.zip")})
    rows = []
    for i in ids:
        zips = {m: zdir / f"{i}-{m}.zip" for m in MODES}
        if not all(z.exists() for z in zips.values()):
            continue
        j = json.load(open(fdir / i / "assembly.json"))
        fs = fusion_structure(j)
        canon = {}
        pred = predictions(rx.load(str(zips["hierarchical"])))
        for m, z in zips.items():
            pkg = rx.load(str(z))
            st = rx.stats(pkg)
            canon[m] = st.pop("canon")
            sizes = pkg.sizes
            rec = {"id": i, "mode": m, **{f"f_{k}": v for k, v in fs.items()}, **pred, **st,
                   "xsd": xsd_ok(str(z), schema),
                   "bytes_total": sum(sizes.values()),
                   "bytes_step": sum(v for k, v in sizes.items() if k.lower().endswith((".stp", ".step")))}
            if m == "flat":
                try:
                    rec["fusion"] = fx.compare(str(z), str(fdir / i / "assembly.json"))
                except Exception as exc:  # noqa: BLE001
                    rec["fusion"] = {"error": str(exc)[:200]}
            rows.append(rec)
        same = len(set(canon.values())) == 1
        for r in rows[-3:]:
            r["canon_equal_across_modes"] = same
    json.dump(rows, open(out, "w"), default=float)
    print(len(rows), "records")


if __name__ == "__main__":
    main(*sys.argv[1:5])
