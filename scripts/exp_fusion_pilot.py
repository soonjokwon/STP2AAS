"""Pilot: convert a sample of Fusion 360 Gallery assemblies and collect metrics.

Usage (from repo root, env python):
    python scripts/exp_fusion_pilot.py <extracted_dataset_dir> [N=40] [out.csv]

Per assembly: part/occurrence counts (from assembly.json and from our IR),
AAS/submodel counts, package size, conversion+mesh timing, failure reasons.
This is the dry run for the paper's §6 Tier-2 harness.
"""

from __future__ import annotations

import csv
import json
import pathlib
import sys
import time
import traceback

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))


def assembly_dirs(dataset_dir: pathlib.Path):
    """Yield assembly folders (contain assembly.step)."""
    for d in sorted(dataset_dir.iterdir()):
        if d.is_dir() and (d / "assembly.step").is_file():
            yield d


def json_stats(folder: pathlib.Path) -> dict:
    try:
        data = json.loads((folder / "assembly.json").read_text(encoding="utf-8"))
        return {
            "json_bodies": len(data.get("bodies", {})),
            "json_occurrences": len(data.get("occurrences", {})),
            "json_components": len(data.get("components", {})),
        }
    except Exception:  # noqa: BLE001
        return {"json_bodies": None, "json_occurrences": None, "json_components": None}


def convert_one(folder: pathlib.Path, out_dir: pathlib.Path) -> dict:
    from basyx.aas import model as bm
    from basyx.aas.adapter.aasx import AASXReader, DictSupplementaryFileContainer

    from stp2aas.aasx_writer import _iter_leaves, _unique_leaf_prototypes, write_aasx
    from stp2aas.extract.xde import extract

    row: dict = {"id": folder.name, **json_stats(folder)}
    stp = folder / "assembly.step"
    row["step_bytes"] = stp.stat().st_size

    t0 = time.perf_counter()
    doc = extract(str(stp))
    row["t_extract_s"] = round(time.perf_counter() - t0, 2)
    leaves = list(_iter_leaves(doc.root))
    row["occurrences"] = len(leaves)
    row["unique_parts"] = len(_unique_leaf_prototypes(doc.root))
    row["dedup_ratio"] = (
        round(row["occurrences"] / row["unique_parts"], 3) if row["unique_parts"] else None
    )
    row["is_assembly"] = doc.root.is_assembly

    out = out_dir / f"{folder.name}.aasx"
    t0 = time.perf_counter()
    write_aasx(doc, str(out))  # hierarchical (default)
    row["t_convert_s"] = round(time.perf_counter() - t0, 2)
    row["aasx_bytes"] = out.stat().st_size

    # roundtrip: count AAS / submodels (Tier 1 conformance probe)
    store = bm.DictObjectStore()
    files = DictSupplementaryFileContainer()
    with AASXReader(str(out)) as reader:
        reader.read_into(store, files)
    row["n_aas"] = sum(1 for o in store if isinstance(o, bm.AssetAdministrationShell))
    row["n_submodels"] = sum(1 for o in store if isinstance(o, bm.Submodel))
    row["n_suppl"] = sum(1 for _ in files)
    row["status"] = "ok"
    out.unlink()  # pilot: don't accumulate gigabytes
    return row


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    dataset_dir = pathlib.Path(sys.argv[1])
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 40
    out_csv = pathlib.Path(sys.argv[3]) if len(sys.argv) > 3 else ROOT / "out" / "fusion_pilot.csv"
    out_dir = out_csv.parent / "fusion_pilot_tmp"
    out_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    failures = 0
    for i, folder in enumerate(assembly_dirs(dataset_dir)):
        if i >= n:
            break
        print(f"[{i + 1}/{n}] {folder.name} ...", flush=True)
        try:
            rows.append(convert_one(folder, out_dir))
        except Exception as exc:  # noqa: BLE001
            failures += 1
            rows.append(
                {"id": folder.name, "status": f"FAIL: {type(exc).__name__}: {exc}"}
            )
            traceback.print_exc()

    fields: list[str] = []
    for r in rows:
        for k in r:
            if k not in fields:
                fields.append(k)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with open(out_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    ok = [r for r in rows if r.get("status") == "ok"]
    print(f"\n=== pilot summary: {len(ok)} ok / {failures} failed → {out_csv}")
    if ok:
        tot_occ = sum(r["occurrences"] for r in ok)
        tot_uni = sum(r["unique_parts"] for r in ok)
        print(f"  occurrences={tot_occ} unique={tot_uni} overall-dedup={tot_occ / max(tot_uni, 1):.2f}")
        print(f"  median t_convert={sorted(r['t_convert_s'] for r in ok)[len(ok) // 2]}s")
        biggest = max(ok, key=lambda r: r["occurrences"])
        print(f"  largest: {biggest['id']} ({biggest['occurrences']} occ → {biggest['n_aas']} AAS)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
