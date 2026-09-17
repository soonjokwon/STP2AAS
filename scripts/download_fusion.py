"""Download the Fusion 360 Gallery Assembly Dataset archives (a1.0.0_00..10.7z).

Usage: python scripts/download_fusion.py <dest_dir> [n ...]

With no archive numbers, every incomplete archive of the 11 is fetched. Downloads
are resumable: a file whose size already matches the server's Content-Length is
skipped, and a partial file continues with an HTTP Range request.

The server closes long connections early, which makes a plain read loop stop
mid-file without raising. Every transfer is therefore verified against
Content-Length and a short read is retried (resuming where it stopped) rather
than reported as complete.
"""

from __future__ import annotations

import pathlib
import sys
import time
import urllib.request

BASE = "https://fusion-360-gallery-dataset.s3.us-west-2.amazonaws.com/assembly/a1.0.0/"
N_ARCHIVES = 11
MAX_PASSES = 40


class ShortRead(Exception):
    """The connection ended before Content-Length bytes arrived."""


def remote_size(url: str) -> int:
    with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=60) as r:
        return int(r.headers["Content-Length"])


def fetch(url: str, dest: pathlib.Path) -> None:
    """Fetch (or resume) one archive. Raises ShortRead if it ends up truncated."""
    total = remote_size(url)
    have = dest.stat().st_size if dest.is_file() else 0
    if have == total:
        print(f"[skip] {dest.name} complete ({total / 1e9:.2f} GB)", flush=True)
        return
    req = urllib.request.Request(url)
    mode = "wb"
    if have:
        req.add_header("Range", f"bytes={have}-")
        mode = "ab"
        print(f"[resume] {dest.name} at {have / 1e9:.2f}/{total / 1e9:.2f} GB", flush=True)
    t0 = time.time()
    done = have
    with urllib.request.urlopen(req, timeout=120) as resp, open(dest, mode) as fh:
        while chunk := resp.read(8 << 20):
            fh.write(chunk)
            done += len(chunk)
            el = time.time() - t0
            if el > 0 and done % (256 << 20) < (8 << 20):
                rate = (done - have) / el / 1e6
                print(
                    f"  {dest.name}: {done / 1e9:.2f}/{total / 1e9:.2f} GB ({rate:.1f} MB/s)",
                    flush=True,
                )
    if done != total:
        raise ShortRead(f"{dest.name}: got {done / 1e9:.2f} of {total / 1e9:.2f} GB")
    print(
        f"[done] {dest.name} {done / 1e9:.2f} GB in {(time.time() - t0) / 60:.1f} min",
        flush=True,
    )


def incomplete(root: pathlib.Path, numbers: list[int]) -> list[int]:
    out = []
    for i in numbers:
        name = f"a1.0.0_{i:02d}.7z"
        path = root / name
        have = path.stat().st_size if path.is_file() else 0
        if have != remote_size(BASE + name):
            out.append(i)
    return out


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
    root.mkdir(parents=True, exist_ok=True)
    numbers = [int(a) for a in sys.argv[2:]] or list(range(N_ARCHIVES))

    t0 = time.time()
    todo = incomplete(root, numbers)
    for _pass in range(1, MAX_PASSES + 1):
        if not todo:
            break
        print(f"[pass {_pass}] {len(todo)} archive(s) to go: {todo}", flush=True)
        for i in todo:
            name = f"a1.0.0_{i:02d}.7z"
            try:
                fetch(BASE + name, root / name)
            except ShortRead as exc:
                print(f"[short] {exc} - will resume", flush=True)
            except Exception as exc:  # noqa: BLE001 - network flakiness: resume next pass
                print(f"[error] {name}: {type(exc).__name__}: {exc}", flush=True)
                time.sleep(5)
        todo = incomplete(root, todo)

    if todo:
        print(f"[FAIL] still incomplete after {MAX_PASSES} passes: {todo}", flush=True)
        return 1
    print(f"=== requested archives complete in {(time.time() - t0) / 60:.1f} min", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
