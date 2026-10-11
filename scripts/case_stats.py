"""Case-study record from the three packages of one model (Table 4 / cases.json).

Usage: python case_stats.py <dir> <key> <label>
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import evaluate_compositions as ev  # noqa: E402
import reconstruct_aasx as rx  # noqa: E402
import size_law  # noqa: E402


def record(d: str, key: str, label: str) -> dict:
    modes = ["hierarchical", "flat", "single"]
    modes += [m for m in ("lean-hierarchical", "lean-flat") if pathlib.Path(f"{d}/{key}-{m}.zip").exists()]
    pk = {m: rx.load(f"{d}/{key}-{m}.zip") for m in modes}
    st = {m: rx.stats(p) for m, p in pk.items()}
    pr = ev.predictions(pk["hierarchical"])
    parts = pr["p_unique_prototypes"] - pr["p_unique_asm"]
    asm = pr["p_unique_asm"]
    rep_p, rep_a = pr["p_repeated_parts"], pr["p_repeated_asm"]
    sz = size_law.predict(f"{d}/{key}-hierarchical.zip", f"{d}/{key}-single.zip")
    fsz = rx.load(f"{d}/{key}-flat.zip").sizes
    step_f = sum(v for k, v in fsz.items() if k.lower().endswith((".stp", ".step")))
    r = {"id": key, "label": label, "leaves": pr["p_leaves"], "parts": parts, "asm": asm,
         "rep": pr["p_repeated"], "rep_parts": rep_p, "rep_asm": rep_a, "C": pr["p_leaves_in_C"],
         "aas_h": st["hierarchical"]["shells"], "aas_f": st["flat"]["shells"], "aas_s": st["single"]["shells"],
         "aas_star": 1 + pr["p_repeated"], "aas_sf": 1 + rep_p,
         "step_h": sz["observed"], "step_f": step_f,
         "shared": {m: st[m]["shared_gids"] for m in st},
         "own": {m: st[m]["unique_node_leaf_occ"] for m in st}}
    exp_shared = {"hierarchical": pr["p_repeated"], "flat": rep_p, "single": 0,
                  "lean-hierarchical": pr["p_repeated"], "lean-flat": rep_p}
    exp_own = {"hierarchical": pr["p_leaves"] - pr["p_leaves_in_C"], "flat": pr["p_leaves"], "single": pr["p_leaves"],
               "lean-hierarchical": pr["p_leaves"] - pr["p_leaves_in_C"], "lean-flat": pr["p_leaves"]}
    exp_shared = {m: exp_shared[m] for m in st}
    exp_own = {m: exp_own[m] for m in st}
    # Lean AAS counts: generated packages when present (checked against 1 + |R|, 1 + |R ∩ P|).
    for m, k, e in (("lean-hierarchical", "aas_star", 1 + pr["p_repeated"]), ("lean-flat", "aas_sf", 1 + rep_p)):
        if m in st:
            r[k] = st[m]["shells"]
            r[k + "_generated"] = True
            r[k + "_ok"] = st[m]["shells"] == e
    if "lean-hierarchical" in pk:
        r["step_lh"] = sum(v for k, v in pk["lean-hierarchical"].sizes.items() if k.lower().endswith((".stp", ".step")))
    r["canon_equal"] = len({st[m]["canon"] for m in st}) == 1
    r["pred_ok"] = r["shared"] == exp_shared and r["own"] == exp_own
    return r


if __name__ == "__main__":
    print(json.dumps(record(*sys.argv[1:4]), indent=1))
