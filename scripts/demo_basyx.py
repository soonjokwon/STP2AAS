"""M4 demo: upload an .aasx to a local Eclipse BaSyx AAS Environment and print the BOM.

Requires a running BaSyx 2.x environment, for example:

    docker run --rm -p 8081:8081 eclipsebasyx/aas-environment:2.0.0-SNAPSHOT

Then:

    python scripts/demo_basyx.py out/model.aasx --url http://localhost:8081

Without --url the script only prints the BOM tree from the local .aasx (offline).
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))


def _load_local(aasx: str):
    from basyx.aas.adapter.aasx import AASXReader, DictSupplementaryFileContainer

    from step2aas.compat import ObjectStore

    store = ObjectStore()
    files = DictSupplementaryFileContainer()
    with AASXReader(aasx) as reader:
        reader.read_into(store, files)
    return store


def _print_bom(store) -> None:
    from basyx.aas import model

    sms = [o for o in store if isinstance(o, model.Submodel)]
    bom = next((s for s in sms if s.id_short == "HierarchicalStructures"), None)
    if bom is None:
        print("no HierarchicalStructures submodel (single-part AASX?)")
        return

    def walk(ent, indent: int = 0) -> None:
        kind = ent.entity_type.name if hasattr(ent.entity_type, "name") else str(ent.entity_type)
        asset = ent.global_asset_id or "-"
        print(f"{'  ' * indent}{ent.id_short} [{kind}] asset={asset}")
        for stmt in ent.statement:
            if isinstance(stmt, model.Entity):
                walk(stmt, indent + 1)
            elif isinstance(stmt, model.RelationshipElement) and stmt.id_short.startswith("SameAs"):
                print(f"{'  ' * (indent + 1)}SameAs -> {stmt.second.key[0].value}")

    for elem in bom.submodel_element:
        if isinstance(elem, model.Entity):
            walk(elem)


def _upload(url: str, aasx: Path) -> None:
    endpoint = url.rstrip("/") + "/upload/aasx"
    data = aasx.read_bytes()
    req = urllib.request.Request(
        endpoint,
        data=data,
        method="POST",
        headers={"Content-Type": "application/asset-administration-shell-package+xml"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            print(f"upload {resp.status} {endpoint}")
            body = resp.read()[:500]
            if body:
                print(body.decode("utf-8", errors="replace"))
    except urllib.error.URLError as exc:
        print(f"upload failed ({exc}). Is BaSyx running at {url}?")
        print("docker run --rm -p 8081:8081 eclipsebasyx/aas-environment:2.0.0-SNAPSHOT")
        sys.exit(2)


def _get_shells(url: str) -> None:
    endpoint = url.rstrip("/") + "/shells"
    try:
        with urllib.request.urlopen(endpoint, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        print(f"GET {endpoint} failed: {exc}")
        return
    result = payload.get("result", payload)
    if isinstance(result, list):
        print(f"{len(result)} shells registered")
        for item in result[:20]:
            ident = item.get("id") or item.get("identification", {})
            print(f"  {ident}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Print BOM from an .aasx; optionally upload to BaSyx")
    p.add_argument("aasx", help="path to .aasx")
    p.add_argument("--url", help="BaSyx AAS Environment base URL, e.g. http://localhost:8081")
    args = p.parse_args(argv)
    path = Path(args.aasx)
    if not path.is_file():
        print(f"not found: {path}", file=sys.stderr)
        return 1
    store = _load_local(str(path))
    print(f"local {path}:")
    _print_bom(store)
    if args.url:
        _upload(args.url, path)
        _get_shells(args.url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
