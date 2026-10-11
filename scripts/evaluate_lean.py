"""Check the two lean packaging options (D9) with the package-only reader.

Usage: python evaluate_lean.py <zip_dir> <AAS.xsd> <out.json>

For each design with hierarchical and lean packages in <zip_dir>, the predictions
come from the hierarchical package (evaluate_compositions.predictions) and are
compared with what the lean packages show:
  lean-hierarchical: 1 + |R| AAS, |R| designs visible, |uses| - |C| own entries;
  lean-flat:         1 + |R ∩ P| AAS, |R ∩ P| visible, all uses own entries;
and the occurrence tree must equal the hierarchical one.
"""
from __future__ import annotations

import json
import pathlib
import sys

from lxml import etree

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import evaluate_compositions as ev  # noqa: E402
import reconstruct_aasx as rx  # noqa: E402

LEAN = ("lean-hierarchical", "lean-flat")


def expected(p: dict, mode: str) -> tuple[int, int, int]:
    if mode == "lean-hierarchical":
        return 1 + p["p_repeated"], p["p_repeated"], p["p_leaves"] - p["p_leaves_in_C"]
    return 1 + p["p_repeated_parts"], p["p_repeated_parts"], p["p_leaves"]


def check(zdir: pathlib.Path, key: str, schema) -> list[dict]:
    hpkg = rx.load(str(zdir / f"{key}-hierarchical.zip"))
    pred = ev.predictions(hpkg)
    hcanon = rx.stats(hpkg)["canon"]
    hsizes = hpkg.sizes
    rows = []
    for m in LEAN:
        z = zdir / f"{key}-{m}.zip"
        if not z.exists():
            continue
        pkg = rx.load(str(z))
        st = rx.stats(pkg)
        got = (st["shells"], st["shared_gids"], st["unique_node_leaf_occ"])
        step = sum(v for k, v in pkg.sizes.items() if k.lower().endswith((".stp", ".step")))
        rows.append({
            "id": key, "mode": m, **pred,
            "shells": st["shells"], "shared": st["shared_gids"], "own": st["unique_node_leaf_occ"],
            "pred_ok": got == expected(pred, m),
            "canon_equal": st["canon"] == hcanon,
            "scene_ok": st["leaf_occurrences"] <= 1
            or (st["scene_keyed"] and st["scene_instances"] == st["leaf_occurrences"]),
            "xsd": ev.xsd_ok(str(z), schema),
            "bytes_step": step,
            "bytes_step_h": sum(v for k, v in hsizes.items() if k.lower().endswith((".stp", ".step"))),
            "shells_h": len(hpkg.shells),
        })
    return rows


def main(zip_dir: str, xsd: str, out: str) -> None:
    schema = etree.XMLSchema(etree.parse(xsd))
    zdir = pathlib.Path(zip_dir)
    keys = sorted({p.name.rsplit("-lean-", 1)[0] for p in zdir.glob("*-lean-*.zip")})
    rows = [r for k in keys if (zdir / f"{k}-hierarchical.zip").exists() for r in check(zdir, k, schema)]
    json.dump(rows, open(out, "w"), indent=1)
    by = {m: [r for r in rows if r["mode"] == m] for m in LEAN}
    print(json.dumps({m: {"packages": len(v), "pred_ok": sum(r["pred_ok"] for r in v),
                          "canon_equal": sum(r["canon_equal"] for r in v),
                          "scene_ok": sum(r["scene_ok"] for r in v), "xsd": sum(r["xsd"] for r in v),
                          "aas": sum(r["shells"] for r in v), "aas_h": sum(r["shells_h"] for r in v)}
                      for m, v in by.items()}, indent=1))


if __name__ == "__main__":
    main(*sys.argv[1:4])
