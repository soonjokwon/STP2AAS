"""M4 evaluation: mapping coverage, AASX size, convert time, D2 Node-n vs BulkCount.

    python scripts/evaluate.py [inputs...] -o out/evaluation.csv

Without inputs, looks under tests/fixtures and tests/ for .stp/.step/.stpx.
Geometry-less IR can still be evaluated via --synthetic (no pythonocc).
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))


def _iter_inputs(explicit: list[str]) -> list[Path]:
    if explicit:
        return [Path(p) for p in explicit]
    found: list[Path] = []
    for folder in (ROOT / "tests" / "fixtures", ROOT / "tests"):
        if not folder.is_dir():
            continue
        for p in folder.rglob("*"):
            if p.suffix.lower() in {".stp", ".step", ".stpx", ".xml"} and p.is_file():
                found.append(p)
    return found


def _synthetic_doc():
    from step2aas.model import P21Header, PartNode, StepDocument

    a = PartNode(name="A", product_id="a", ref_key="a")
    a2 = PartNode(name="A2", product_id="a", ref_key="a")
    b = PartNode(name="B", product_id="b", ref_key="b")
    root = PartNode(name="Asm", product_id="asm", ref_key="asm", children=[a, a2, b])
    return StepDocument(
        root=root,
        header=P21Header(schema="AP242", organization="Eval"),
        source_path="",
        file_hash="0" * 64,
    )


def _count_nodes(root) -> tuple[int, int]:
    occ = uniq = 0
    seen: set[str] = set()

    def walk(n):
        nonlocal occ, uniq
        if n.is_assembly:
            for c in n.children:
                walk(c)
        else:
            occ += 1
            if n.ref_key not in seen:
                seen.add(n.ref_key)
                uniq += 1

    walk(root)
    return occ, uniq


def _coverage(doc) -> dict[str, str]:
    root = doc.root
    occ, uniq = _count_nodes(root)
    leaf = next((n for n in _leaves(root)), root)
    return {
        "S1_tree": "ok" if root.children or not root.is_assembly else "empty",
        "S3_name": "ok" if leaf.name else "missing",
        "S7_volume": "ok" if leaf.props.volume_mm3 is not None else "missing",
        "S8_material": "ok" if leaf.props.material else "missing",
        "S11_schema": "ok" if doc.header.schema else "missing",
        "S12_bbox": "ok" if leaf.bbox_mm else "missing",
        "S14_pmi": "yes" if any(n.has_pmi for n in _leaves(root)) else "no",
        "D2_occurrences": str(occ),
        "D2_unique_parts": str(uniq),
    }


def _leaves(node):
    if node.is_assembly:
        for c in node.children:
            yield from _leaves(c)
    else:
        yield node


def _write_two_strategies(doc, tmp: Path) -> tuple[int, int]:
    from step2aas.aasx_writer import write_aasx

    # Need a readable source_path for whole-model embed.
    src = tmp / "src.stp"
    if not doc.source_path:
        src.write_bytes(b"ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n")
        doc.source_path = str(src)
    occ_path = tmp / "occ.aasx"
    bulk_path = tmp / "bulk.aasx"
    write_aasx(doc, str(occ_path), bom_strategy="occurrence")
    write_aasx(doc, str(bulk_path), bom_strategy="bulk")
    return occ_path.stat().st_size, bulk_path.stat().st_size


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="step2aas evaluation CSV (M4)")
    p.add_argument("inputs", nargs="*", help="STEP / AP242 files")
    p.add_argument("-o", "--output", default="out/evaluation.csv")
    p.add_argument("--synthetic", action="store_true", help="include a geometry-less synthetic row")
    args = p.parse_args(argv)

    rows: list[dict] = []
    inputs = _iter_inputs(args.inputs)
    if args.synthetic or not inputs:
        args.synthetic = True

    from step2aas.extract import extract_document

    jobs: list[tuple[str, object]] = []
    if args.synthetic:
        jobs.append(("synthetic", _synthetic_doc()))
    for path in inputs:
        t0 = time.perf_counter()
        try:
            doc = extract_document(str(path))
            extract_s = time.perf_counter() - t0
            jobs.append((str(path), doc, extract_s))  # type: ignore[arg-type]
        except Exception as exc:  # noqa: BLE001
            rows.append({"input": str(path), "error": str(exc)})

    import tempfile

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)

    normalised: list[tuple[str, object, float]] = []
    for job in jobs:
        if len(job) == 2:
            normalised.append((job[0], job[1], 0.0))
        else:
            normalised.append(job)  # type: ignore[arg-type]

    for name, doc, extract_s in normalised:
        with tempfile.TemporaryDirectory() as td:
            t0 = time.perf_counter()
            occ_sz, bulk_sz = _write_two_strategies(doc, Path(td))
            write_s = time.perf_counter() - t0
        cov = _coverage(doc)
        occ, uniq = _count_nodes(doc.root)
        rows.append(
            {
                "input": name,
                "extract_s": f"{extract_s:.3f}",
                "write_both_s": f"{write_s:.3f}",
                "aasx_occurrence_bytes": occ_sz,
                "aasx_bulk_bytes": bulk_sz,
                "d2_ratio": f"{(occ_sz / bulk_sz):.3f}" if bulk_sz else "",
                "occurrences": occ,
                "unique_parts": uniq,
                **cov,
                "error": "",
            }
        )

    fieldnames = sorted({k for r in rows for k in r})
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {out} ({len(rows)} rows)")
    buf = io.StringIO()
    w2 = csv.DictWriter(buf, fieldnames=fieldnames)
    w2.writeheader()
    w2.writerows(rows)
    print(buf.getvalue())
    return 0


if __name__ == "__main__":
    sys.exit(main())
