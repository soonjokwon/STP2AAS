"""Predict hierarchical STEP payload from the graph and per-part file sizes.

Hierarchical packages embed one re-exported STEP file per shelled sub-assembly.
Its size is approximated by the part files of all leaf occurrences under that
sub-assembly. Prediction: sum(part files) + root file + sum over shelled
sub-assemblies s != root of sum over leaves under s of size(part file).
Part file sizes come from the single-mode package (scene record -> file).
"""
from __future__ import annotations

import json
import pathlib
import sys
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import reconstruct_aasx as rx  # noqa: E402


def part_file_sizes(single_zip: str) -> tuple[dict, int]:
    pkg = rx.load(single_zip)
    files = {}
    for sc in pkg.scenes.values():
        for i in sc["instances"]:
            if i.get("file"):
                files[i["asset"]] = i["file"].lstrip("/")
    sizes = pkg.sizes
    by_asset = {a: sizes.get(f, 0) for a, f in files.items()}
    step = {k: v for k, v in sizes.items() if k.lower().endswith((".stp", ".step"))}
    root_bytes = sum(step.values()) - sum(sizes.get(f, 0) for f in set(files.values()))
    return by_asset, root_bytes


def predict(h_zip: str, single_zip: str) -> dict:
    by_asset, root_bytes = part_file_sizes(single_zip)
    tree = rx.expand(rx.load(h_zip))
    seen_sub = set()
    extra = 0

    def leaves_bytes(o):
        if not o.children:
            return by_asset.get(o.gid, 0)
        return sum(leaves_bytes(c) for c in o.children)

    def walk(o):
        nonlocal extra
        for c in o.children:
            if c.children and c.gid not in seen_sub:
                seen_sub.add(c.gid)
                extra += leaves_bytes(c)
            walk(c)

    walk(tree)
    parts = sum(by_asset.values())
    sizes = rx.load(h_zip).sizes
    observed = sum(v for k, v in sizes.items() if k.lower().endswith((".stp", ".step")))
    return {"predicted": parts + root_bytes + extra, "observed": observed,
            "flat_like": parts + root_bytes, "sub_assemblies": len(seen_sub)}


if __name__ == "__main__":
    print(predict(sys.argv[1], sys.argv[2]))
