"""Full-corpus experiment: extract all Fusion 360 assembly chunks and convert
every assembly in parallel, collecting the §6 Tier-2 metrics.

Usage (from repo root):
    python scripts/exp_fusion_full.py <dir_with_7z_archives> [workers=6] [out.csv]

Phase 1: selective extraction (assembly.step + assembly.json only; skips chunks
already extracted). Phase 2: parallel conversion via exp_fusion_pilot.convert_one.
"""

from __future__ import annotations

import csv
import pathlib
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))


def extract_chunk(archive: pathlib.Path, out_dir: pathlib.Path) -> int:
    import py7zr

    if out_dir.is_dir() and any(out_dir.iterdir()):
        return sum(1 for d in out_dir.iterdir() if (d / "assembly.step").is_file())
    with py7zr.SevenZipFile(archive) as z:
        want = [n for n in z.getnames() if n.endswith(("assembly.step", "assembly.json"))]
        z.extract(path=out_dir, targets=want)
    return sum(1 for d in out_dir.iterdir() if (d / "assembly.step").is_file())


def _convert(args: tuple[str, str]) -> dict:
    """Worker: convert one assembly folder (spawn-safe)."""
    folder, tmp = args
    from exp_fusion_pilot import convert_one

    try:
        return convert_one(pathlib.Path(folder), pathlib.Path(tmp))
    except Exception as exc:  # noqa: BLE001
        return {"id": pathlib.Path(folder).name, "status": f"FAIL: {type(exc).__name__}: {exc}"}


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    arch_dir = pathlib.Path(sys.argv[1])
    workers = int(sys.argv[2]) if len(sys.argv) > 2 else 6
    out_csv = pathlib.Path(sys.argv[3]) if len(sys.argv) > 3 else ROOT / "out" / "fusion_full.csv"
    tmp_dir = out_csv.parent / "fusion_full_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    # Phase 1 — extraction
    folders: list[pathlib.Path] = []
    for i, archive in enumerate(sorted(arch_dir.glob("*.7z"))):
        chunk_dir = arch_dir / f"a{i}"
        t0 = time.time()
        # A still-downloading (truncated) archive must not abort a multi-hour sweep:
        # log it and convert whatever the other chunks provide.
        try:
            n = extract_chunk(archive, chunk_dir)
        except Exception as exc:  # noqa: BLE001
            print(f"[extract] SKIP {archive.name}: {type(exc).__name__}: {exc}", flush=True)
            continue
        print(
            f"[extract] {archive.name} -> {chunk_dir.name}: {n} assemblies "
            f"({time.time() - t0:.0f}s)",
            flush=True,
        )
        if not chunk_dir.is_dir():
            continue
        folders += [d for d in sorted(chunk_dir.iterdir()) if (d / "assembly.step").is_file()]
    print(f"[extract] total assemblies: {len(folders)}", flush=True)

    # Phase 2 — parallel conversion.
    # Robustness: OCC can hard-crash a worker on pathological geometry, which breaks
    # the whole pool (BrokenProcessPool) and would lose everything. Therefore:
    #  * rows are APPENDED to the CSV as they complete (crash loses nothing, and a
    #    re-run resumes by skipping ids already present);
    #  * on a pool break, the next few unfinished items are probed one-per-pool to
    #    identify the crasher (marked FAIL:native-crash), then parallel resumes.
    from concurrent.futures.process import BrokenProcessPool

    fields = [
        "id",
        "json_bodies",
        "json_occurrences",
        "json_components",
        "step_bytes",
        "t_extract_s",
        "occurrences",
        "unique_parts",
        "dedup_ratio",
        "is_assembly",
        "t_convert_s",
        "aasx_bytes",
        "n_aas",
        "n_submodels",
        "n_suppl",
        "status",
    ]
    done_ids: set[str] = set()
    if out_csv.is_file():
        with open(out_csv, encoding="utf-8") as fh:
            done_ids = {r["id"] for r in csv.DictReader(fh)}
        print(f"[resume] {len(done_ids)} already in {out_csv.name}", flush=True)
    else:
        with open(out_csv, "w", newline="", encoding="utf-8") as fh:
            csv.DictWriter(fh, fieldnames=fields).writeheader()

    remaining = [f for f in folders if f.name not in done_ids]
    total = len(folders)
    t0 = time.time()

    def record(row: dict) -> None:
        with open(out_csv, "a", newline="", encoding="utf-8") as fh:
            csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore").writerow(row)
        done_ids.add(row["id"])
        k = len(done_ids)
        if k % 100 == 0:
            el = time.time() - t0
            print(f"[convert] {k}/{total}  ({el / 60:.0f} min)", flush=True)

    def probe_solo(folder: pathlib.Path) -> None:
        """Run one item in its own single-worker pool to isolate native crashes."""
        try:
            with ProcessPoolExecutor(max_workers=1) as solo:
                record(solo.submit(_convert, (str(folder), str(tmp_dir))).result())
        except BrokenProcessPool:
            print(f"[crash] {folder.name}: worker died (native crash)", flush=True)
            record({"id": folder.name, "status": "FAIL: native crash (worker died)"})

    while remaining:
        try:
            with ProcessPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(_convert, (str(f), str(tmp_dir))): f for f in remaining}
                for fut in as_completed(futures):
                    record(fut.result())
            remaining = []
        except BrokenProcessPool:
            remaining = [f for f in remaining if f.name not in done_ids]
            probes = remaining[: 2 * workers]
            print(f"[pool-break] {len(remaining)} left; probing {len(probes)} solo", flush=True)
            for f in probes:
                probe_solo(f)
            remaining = [f for f in remaining if f.name not in done_ids]

    with open(out_csv, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    ok = [r for r in rows if r.get("status") == "ok"]
    print(f"\n=== full corpus: {len(ok)} ok / {len(rows) - len(ok)} failed -> {out_csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
