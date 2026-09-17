"""Selectively extract Fusion 360 Gallery assembly archives.

Usage: python scripts/extract_fusion.py <dir_with_7z_archives> [n ...]

Extracts only `assembly.step` + `assembly.json` from archive `a1.0.0_{n:02d}.7z`
into `<dir>/a{n}` - the same chunk-directory naming `exp_fusion_full.py` derives
from the sorted archive list, so a chunk extracted here is skipped there.

With no archive numbers, every complete archive present is extracted. An archive
whose size does not match the server's is skipped as still-downloading, and a
chunk directory that already holds assembly.step files is left alone.
"""

from __future__ import annotations

import pathlib
import sys
import time
import urllib.request

BASE = "https://fusion-360-gallery-dataset.s3.us-west-2.amazonaws.com/assembly/a1.0.0/"


def is_complete(archive: pathlib.Path) -> bool:
    """True if the local archive size matches the published one (i.e. not partial)."""
    try:
        req = urllib.request.Request(BASE + archive.name, method="HEAD")
        with urllib.request.urlopen(req, timeout=60) as resp:
            return archive.stat().st_size == int(resp.headers["Content-Length"])
    except Exception as exc:  # noqa: BLE001 - offline: fall back to "assume complete"
        print(
            f"[warn] {archive.name}: size check failed ({type(exc).__name__}); assuming complete",
            flush=True,
        )
        return True


def extract(archive: pathlib.Path, out_dir: pathlib.Path) -> int:
    import py7zr

    if out_dir.is_dir() and any(
        d.is_dir() and (d / "assembly.step").is_file() for d in out_dir.iterdir()
    ):
        n = sum(1 for d in out_dir.iterdir() if (d / "assembly.step").is_file())
        print(f"[skip] {out_dir.name} already holds {n} assemblies", flush=True)
        return n
    t0 = time.time()
    with py7zr.SevenZipFile(archive) as z:
        want = [n for n in z.getnames() if n.endswith(("assembly.step", "assembly.json"))]
        z.extract(path=out_dir, targets=want)
    n = sum(1 for d in out_dir.iterdir() if (d / "assembly.step").is_file())
    size = sum(f.stat().st_size for d in out_dir.iterdir() if d.is_dir() for f in d.iterdir())
    print(
        f"[done] {archive.name} -> {out_dir.name}: {n} assemblies, "
        f"{size / 1e9:.2f} GB, {time.time() - t0:.0f}s",
        flush=True,
    )
    return n


def _ascii_safe_console() -> None:
    """Legacy Windows consoles (e.g. Korean cp949) raise on characters they lack;
    keep logging alive by replacing them instead (see step2aas/__main__.py)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001, S110 - best effort; not all streams support it
            pass


def main() -> int:
    _ascii_safe_console()
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    root = pathlib.Path(sys.argv[1])
    if len(sys.argv) > 2:
        numbers = [int(a) for a in sys.argv[2:]]
    else:
        numbers = sorted(int(p.stem.split("_")[-1]) for p in root.glob("a1.0.0_*.7z"))

    total = 0
    for n in numbers:
        archive = root / f"a1.0.0_{n:02d}.7z"
        if not archive.is_file():
            print(f"[missing] {archive.name}", flush=True)
            continue
        if not is_complete(archive):
            print(f"[partial] {archive.name} still downloading - skipped", flush=True)
            continue
        total += extract(archive, root / f"a{n}")
    print(f"=== {total} assemblies available across the extracted chunks", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
