"""Recount saved paper evidence without re-running the corpus conversion.

Run from the project root: python scripts/audit_evidence.py
The JSON distinguishes summed worker elapsed time from CPU and wall-clock time.
"""

import csv
import hashlib
import importlib.metadata
import json
import platform
import statistics
import zipfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    source = ROOT / "out/fusion_full.csv"
    rows = list(csv.DictReader(source.open(encoding="utf-8")))
    ok = [row for row in rows if row["status"] == "ok"]

    def values(key):
        return [float(row[key]) for row in ok]

    occurrences = values("occurrences")
    elapsed = sum(values("t_extract_s")) + sum(values("t_convert_s"))
    packages = {}
    for path in sorted((ROOT / "out").glob("as1_table5_*.aasx")):
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            xml = archive.getinfo("aasx/data.xml")
            packages[path.name] = {
                "bytes": path.stat().st_size,
                "data_xml_uncompressed_bytes": xml.file_size,
                "data_xml_compressed_bytes": xml.compress_size,
                "supplementary_counts": {
                    suffix: sum(i.filename.endswith(suffix) for i in infos)
                    for suffix in (".stp", ".png", ".json")
                },
            }
    report = {
        "evidence": str(source.relative_to(ROOT)),
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "rows": len(rows),
        "unique_ids": len({r["id"] for r in rows}),
        "status_counts": dict(Counter(r["status"] for r in rows)),
        "successful_row_totals": {
            key: int(sum(values(key)))
            for key in ("n_aas", "n_submodels", "occurrences", "unique_parts")
        },
        "occurrences_median": statistics.median(occurrences),
        "occurrences_max": max(occurrences),
        "reused_prototype_designs": sum(v > 1 for v in values("dedup_ratio")),
        "single_occurrence_designs": sum(v == 1 for v in occurrences),
        "summed_worker_elapsed_hours": elapsed / 3600,
        "mean_worker_elapsed_seconds": elapsed / len(ok),
        "timing_scope": "perf_counter extraction + writing; excludes round-trip reading",
        "historical_wall_clock_verified_from_csv": False,
        "as1_packages": packages,
        "audit_environment_not_historical_run": {
            "python": platform.python_version(),
            "packages": {
                key: importlib.metadata.version(key)
                for key in ("basyx-python-sdk", "lxml", "pytest", "ruff")
            },
        },
    }
    out = ROOT / "out/review-2026-09-06/evidence-audit.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
