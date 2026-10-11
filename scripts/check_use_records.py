"""Check per-use records (D10) in packages written with --use-records.

Usage: python check_use_records.py <package.aasx|.zip> [...]

Package-only: reads data.xml and the placement scenes with lxml. For every scene
instance it looks for exactly one record on the Entity the instance names, with
the same path below that Entity and the same transform. Prints one JSON line per
package: uses, matched, records, shared (uses below a shared sub-assembly BOM).
"""
from __future__ import annotations

import json
import sys
import zipfile

from lxml import etree


def records(xml, ns) -> dict:
    out: dict = {}
    for sm in xml.findall(".//a:submodels/a:submodel", ns):
        sm_id = sm.findtext("a:id", namespaces=ns)

        def walk(el, prefix):
            kids = el.findall("a:statements/a:entity", ns) if prefix else el.findall("a:submodelElements/a:entity", ns)
            for ent in kids:
                name = ent.findtext("a:idShort", namespaces=ns)
                path = f"{prefix}.{name}" if prefix else name
                for smc in ent.findall("a:statements/a:submodelElementCollection", ns):
                    if smc.findtext("a:idShort", namespaces=ns) != "UseRecords":
                        continue
                    for rec in smc.findall("a:value/a:submodelElementCollection", ns):
                        props = {p.findtext("a:idShort", namespaces=ns): p.findtext("a:value", namespaces=ns) or ""
                                 for p in rec.findall("a:value/a:property", ns)}
                        out.setdefault((sm_id, path), []).append(props)
                walk(ent, path)

        walk(sm, "")
    return out


def check(path: str) -> dict:
    with zipfile.ZipFile(path) as z:
        xml = etree.fromstring(z.read(next(n for n in z.namelist() if n.endswith("data.xml"))))
        scenes = [json.loads(z.read(n)) for n in z.namelist() if n.endswith("__scene.json")]
    ns = {"a": etree.QName(xml).namespace}
    recs = records(xml, ns)
    uses = matched = shared = 0
    root = max(scenes, key=lambda sc: len(sc["instances"])) if scenes else None
    root_uses = root_matched = root_shared = 0
    for scene in scenes:
        for inst in scene["instances"]:
            if "node" not in inst:
                continue
            uses += 1
            shared += inst["below"] > 0
            parts = inst["path"].split("/") if inst["path"] else []
            below = "/".join(parts[len(parts) - inst["below"]:]) if inst["below"] else ""
            cand = [r for r in recs.get((inst["bom"], inst["node"]), []) if r["PathBelow"] == below]
            if len(cand) == 1:
                t = [float(v) for v in cand[0]["Transform"].split()]
                if all(abs(a - b) <= 1e-6 * max(1.0, abs(b)) for a, b in zip(t, inst["world"])):
                    matched += 1
                    if scene is root:
                        root_matched += 1
            if scene is root:
                root_uses += 1
                root_shared += inst["below"] > 0
    return {"package": path, "uses": uses, "matched": matched,
            "records": sum(len(v) for v in recs.values()), "shared": shared,
            "root_uses": root_uses, "root_matched": root_matched, "root_shared": root_shared}


if __name__ == "__main__":
    for p in sys.argv[1:]:
        print(json.dumps(check(p)))
