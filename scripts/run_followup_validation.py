"""Reproduce the bounded follow-up experiment separately from the saved full run."""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import json
import platform
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from validate_package import validate_package

from step2aas.aasx_writer import write_aasx
from step2aas.extract import extract_document


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("out/followup-2026-09-07"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    rows = list(csv.DictReader((root / "out/fusion_full.csv").open(encoding="utf-8-sig")))
    rows.sort(key=lambda row: (int(row["occurrences"]), row["id"]))
    selected = [rows[round(q * (len(rows) - 1))] for q in (0, .25, .5, .75, .9, .99, 1)]
    cases = [("as1", root / "tests/as1/as1_root.stp")]
    folders = sorted((root / "tests/fusion360").glob("a*"))
    for row in selected:
        matches = [folder / row["id"] / "assembly.step" for folder in folders
                   if (folder / row["id"] / "assembly.step").is_file()]
        if len(matches) != 1:
            raise ValueError(f"Expected one source for {row['id']}: {matches}")
        cases.append((row["id"], matches[0]))
    environment = {
        "started_utc": datetime.now(UTC).isoformat(),
        "python": sys.version, "platform": platform.platform(),
        "processor": platform.processor(),
        "conda_packages": [{k: item[k] for k in ("name", "version", "build")}
                           for meta in sorted((Path(sys.prefix) / "conda-meta").glob("*.json"))
                           for item in [json.loads(meta.read_text(encoding="utf-8"))]],
        "dependencies": {d.metadata["Name"]: d.version for d in importlib.metadata.distributions()},
        "selection": "Seven order statistics (0,25,50,75,90,99,100 percent) by "
                     "(saved occurrence count, id), plus AS1; not a random sample.",
        "sources": [{"id": name, "path": str(path.relative_to(root)),
                     "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                    for name, path in cases],
        "source_code": {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
                        for path in sorted((root / "src/step2aas").rglob("*.py"))},
        "mapping": {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in sorted((root / "mapping").glob("*.yaml"))},
    }
    (out / "environment.json").write_text(json.dumps(environment, indent=2), encoding="utf-8")
    reports = []
    for name, source in cases:
        started = time.perf_counter()
        doc = extract_document(str(source))
        extract_s = time.perf_counter() - started
        for mode in ("hierarchical", "flat", "single"):
            target = out / "packages" / f"{name}-{mode}.aasx"
            started = time.perf_counter()
            write_aasx(doc, str(target), assembly_structure=mode)
            write_s = time.perf_counter() - started
            report = validate_package(target, out / "schema/AAS.xsd")
            report.update({"case": name, "mode": mode, "extract_s": extract_s,
                           "write_s": write_s})
            reports.append(report)
            (out / "validation-results.json").write_text(
                json.dumps(reports, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"{name} {mode}: {report['status']} "
                  f"{report['counts']['aas']} AAS, {write_s:.2f}s", flush=True)
            if report["status"] != "pass":
                raise RuntimeError(report["errors"])
    print(f"Validated {len(reports)} packages", flush=True)


if __name__ == "__main__":
    main()
