"""Collect every number reported in the evaluation section into one JSON file."""
from __future__ import annotations

import csv
import json
import statistics
import sys

import numpy as np


def main(eval_json, prev_jsons, size_json, runs_csvs, out):
    ev = json.load(open(eval_json))
    prev = [r for p in prev_jsons.split(",") for r in json.load(open(p))]
    size = json.load(open(size_json))
    st = {}
    n = len(prev)
    st["prev"] = {
        "n": n,
        "multi_leaf": sum(x["leaves"] >= 2 for x in prev),
        "rep_part": sum(x["repeated_parts"] > 0 for x in prev),
        "nested": sum(x["depth"] >= 2 for x in prev),
        "rep_asm": sum(x["repeated_asm"] > 0 for x in prev),
        "leaves": sum(x["leaves"] for x in prev),
        "leaves_in_C": sum(x["leaves_in_C"] for x in prev),
        "median_share_C_affected": statistics.median(
            [x["leaves_in_C"] / x["leaves"] for x in prev if x["leaves_in_C"]]),
        "median_occurrences": statistics.median([x["occurrences"] for x in prev]),
    }
    modes = ("hierarchical", "flat", "single")
    by = {m: [x for x in ev if x["mode"] == m] for m in modes}
    exp = {"hierarchical": lambda x: (x["p_repeated"], x["p_leaves"] - x["p_leaves_in_C"]),
           "flat": lambda x: (x["p_repeated_parts"], x["p_leaves"]),
           "single": lambda x: (0, x["p_leaves"])}
    conf = {"assemblies": len(by["flat"]), "packages": len(ev),
            "xsd_pass": sum(x["xsd"] for x in ev),
            "canon_equal": sum(x["canon_equal_across_modes"] for x in by["flat"]),
            "scene_keyed": sum(x["scene_keyed"] or x["leaf_occurrences"] <= 1 for x in ev),
            "scene_count_ok": sum(x["scene_instances"] == x["leaf_occurrences"] or x["leaf_occurrences"] <= 1 for x in ev)}
    for m in modes:
        conf[f"pred_ok_{m}"] = sum(exp[m](x) == (x["shared_gids"], x["unique_node_leaf_occ"]) for x in by[m])
    flat = by["flat"]
    conf["leaf_total"] = sum(x["leaf_occurrences"] for x in flat)
    conf["leaf_in_C_pkg"] = sum(x["p_leaves_in_C"] for x in flat)
    conf["with_C_pkg"] = sum(x["p_leaves_in_C"] > 0 for x in flat)
    conf["flat_lost_asm_sharing"] = sum(x["p_repeated_asm"] > 0 for x in flat)
    conf["single_lost_sharing"] = sum(x["p_repeated"] > 0 for x in flat)
    conf["fusion_leaf_equal"] = sum(x["leaf_occurrences"] == x["f_leaves"] for x in flat)
    conf["pkg_more"] = sum(x["leaf_occurrences"] > x["f_leaves"] for x in flat)
    conf["pkg_fewer"] = sum(x["leaf_occurrences"] < x["f_leaves"] for x in flat)
    byid = {(x["id"], x["mode"]): x for x in ev}
    ok = 0
    for x in flat:
        if x["leaf_occurrences"] != x["f_leaves"]:
            continue
        h = byid[(x["id"], "hierarchical")]
        ok += (h["shared_gids"] == x["f_repeated"] and x["shared_gids"] == x["f_repeated_parts"]
               and h["unique_node_leaf_occ"] == x["f_leaves"] - x["f_leaves_in_C"])
    conf["fusion_pred_ok"] = ok
    conf["multi_leaf_pkg"] = sum(x["leaf_occurrences"] >= 2 for x in flat)
    conf["root_volume_missing"] = sum(1 for x in flat if x.get("fusion", {}).get("pkg_root_volume") is None)
    st["conf"] = conf
    # faithfulness
    agree = [x for x in flat if x["leaf_occurrences"] == x["f_leaves"] and x.get("fusion", {}).get("matched")]
    vol, area, com, rot, mism = [], [], [], [], 0
    for x in agree:
        f = x["fusion"]
        vol += f["vol_err"]; area += f["area_err"]; com += f["com_err"]; rot += f["rot_err_deg"]
        mism += sum(1 for v, c in zip(f["vol_err"], f["com_err"]) if v > 1e-2 and c > 1e-2)
    v = np.array(vol); a = np.array(area); c = np.array(com); r = np.array(rot)
    st["faith"] = {"assemblies": len(agree), "pairs": len(v),
                   "vol_1pct": float(np.mean(v <= 1e-2)), "area_1pct": float(np.mean(a <= 1e-2)),
                   "com_1e3": float(np.mean(c <= 1e-3)), "rot_001": float(np.mean(r <= 1e-2)),
                   "vol_1e6": float(np.mean(v <= 1e-6)), "vol_median": float(np.median(v)),
                   "vol_out": int(np.sum(v > 1e-2)), "vol_out_with_centroid_off": mism,
                   "out_assemblies": sum(1 for x in agree if any(e > 1e-2 for e in x["fusion"]["vol_err"]))}
    # size
    sz = [d for d in size if d["sub_assemblies"] > 0 and d["observed"] > 0]
    err = [abs(d["observed"] - d["predicted"]) / d["predicted"] for d in sz]
    ratio = [d["observed"] / d["flat_like"] for d in sz if d["flat_like"]]
    st["size"] = {"within3": sum(e <= 0.03 for e in err), "n": len(sz), "median_err": statistics.median(err), "max_err": max(err),
                  "ratio_median": statistics.median(ratio), "ratio_max": max(ratio)}
    # conversion outcomes
    fails, total = 0, 0
    for p in runs_csvs.split(","):
        for row in csv.DictReader(open(p)):
            total += 1
            fails += row["rc"] != "0"
    st["runs"] = {"records": total, "nonzero": fails}
    json.dump(st, open(out, "w"), indent=1, default=float)
    print(json.dumps(st, indent=1, default=float))


if __name__ == "__main__":
    main(*sys.argv[1:6])
