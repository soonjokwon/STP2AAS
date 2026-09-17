"""Run the full Fusion 360 Gallery corpus experiment end to end.

Usage: python scripts/run_fusion_experiment.py <dir_with_7z_archives> [workers=16] [conns=6]

Stage 1 downloads every incomplete archive, `conns` of them at a time (the S3
endpoint throttles per connection, so parallel transfers multiply throughput),
then repairs any archive left short in a final serial pass. Stage 2 extracts the
chunks that are not extracted yet. Stage 3 runs the section 6 sweep.

Every stage is resumable, so this can be re-launched at any point: complete
archives are skipped, populated chunk directories are left alone, and assemblies
already in out/fusion_full.csv are not re-converted.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def _incomplete(root: pathlib.Path) -> list[int]:
    sys.path.insert(0, str(SCRIPTS))
    from download_fusion import N_ARCHIVES, incomplete

    return incomplete(root, list(range(N_ARCHIVES)))


def _spawn(args: list[str]) -> subprocess.Popen:
    cmd = [sys.executable, *args]
    print(f"[spawn] {' '.join(str(c) for c in cmd[1:])}", flush=True)
    return subprocess.Popen(cmd, cwd=str(ROOT))


def _run(script: str, *args: str) -> int:
    cmd = [sys.executable, str(SCRIPTS / script), *args]
    print(f"[run] {' '.join(cmd[1:])}", flush=True)
    return subprocess.call(cmd, cwd=str(ROOT))


def download_all(root: pathlib.Path, conns: int) -> None:
    todo = _incomplete(root)
    if not todo:
        print("[download] all archives already complete", flush=True)
        return
    print(f"[download] {len(todo)} incomplete: {todo} - {conns} parallel connections", flush=True)
    groups: list[list[int]] = [[] for _ in range(min(conns, len(todo)))]
    for k, n in enumerate(todo):  # round-robin so each connection gets a fair share
        groups[k % len(groups)].append(n)

    procs = [
        _spawn([str(SCRIPTS / "download_fusion.py"), str(root), *[str(n) for n in g]])
        for g in groups
        if g
    ]
    for p in procs:
        p.wait()

    # A connection dropped early leaves an archive short; finish those serially.
    left = _incomplete(root)
    if left:
        print(f"[download] repairing {len(left)} short archive(s): {left}", flush=True)
        _run("download_fusion.py", str(root), *[str(n) for n in left])


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
    workers = sys.argv[2] if len(sys.argv) > 2 else "16"
    conns = int(sys.argv[3]) if len(sys.argv) > 3 else 6
    t0 = time.time()

    download_all(root, conns)
    still = _incomplete(root)
    if still:
        print(f"[warn] archives still incomplete, they will be skipped: {still}", flush=True)

    if _run("extract_fusion.py", str(root)) != 0:
        print("[error] extraction failed", flush=True)
        return 1

    csv_path = ROOT / "out" / "fusion_full.csv"
    rc = _run("exp_fusion_full.py", str(root), workers, str(csv_path))
    print(f"=== experiment finished in {(time.time() - t0) / 3600:.2f} h (rc={rc})", flush=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
