"""D10: per-use records on BOM Entities (prototype of the proposed 02011 element).

With an AAS for every repeated sub-assembly, a package should still let a reader
attach data to each use: every placement-scene instance must have exactly one
record on the Entity it names, with the same path below and transform.
"""

import json
import zipfile

import pytest
from lxml import etree

pytest.importorskip("OCC")

from scripts.make_test_fixtures import make_cart_assembly
from step2aas.aasx_writer import write_aasx
from step2aas.extract import extract_document


def _records(xml, ns):
    """(bom id, entity idShort path) -> list of (PathBelow, Transform)."""
    out = {}
    for sm in xml.findall(".//a:submodels/a:submodel", ns):
        sm_id = sm.findtext("a:id", namespaces=ns)

        def walk(el, prefix):
            kids = el.findall("a:statements/a:entity", ns) if prefix else el.findall(
                "a:submodelElements/a:entity", ns)
            for ent in kids:
                path = f"{prefix}.{ent.findtext('a:idShort', namespaces=ns)}" if prefix else ent.findtext(
                    "a:idShort", namespaces=ns)
                for smc in ent.findall("a:statements/a:submodelElementCollection", ns):
                    if smc.findtext("a:idShort", namespaces=ns) != "UseRecords":
                        continue
                    for rec in smc.findall("a:value/a:submodelElementCollection", ns):
                        props = {p.findtext("a:idShort", namespaces=ns): p.findtext("a:value", namespaces=ns) or ""
                                 for p in rec.findall("a:value/a:property", ns)}
                        out.setdefault((sm_id, path), []).append((props["PathBelow"], props["Transform"]))
                walk(ent, path)

        walk(sm, "")
    return out


@pytest.mark.parametrize("mode", ["hierarchical", "lean-hierarchical", "flat"])
def test_every_use_has_a_record(tmp_path, mode):
    source = tmp_path / "cart.step"
    make_cart_assembly(source)
    package = tmp_path / f"{mode}.aasx"
    write_aasx(extract_document(str(source)), str(package), assembly_structure=mode, use_records=True)
    with zipfile.ZipFile(package) as archive:
        xml = etree.fromstring(archive.read("aasx/data.xml"))
        scenes = [json.loads(archive.read(n)) for n in archive.namelist() if n.endswith("__scene.json")]
    ns = {"a": etree.QName(xml).namespace}
    recs = _records(xml, ns)
    root_scene = max(scenes, key=lambda s: len(s["instances"]))
    assert len(root_scene["instances"]) == 6
    for inst in root_scene["instances"]:
        path = inst["path"].split("/")
        below = "/".join(path[len(path) - inst["below"]:]) if inst["below"] else ""
        got = [r for r in recs[(inst["bom"], inst["node"])] if r[0] == below]
        assert len(got) == 1
        assert [float(v) for v in got[0][1].split()] == pytest.approx(inst["world"], abs=1e-6)
    # the two bolts inside a shared caster get different records (different Entities)
    if mode != "flat":
        shared = [i for i in root_scene["instances"] if i["below"] >= 1]
        assert len({(i["node"], i["path"]) for i in shared}) == len(shared) >= 4


def test_records_are_opt_in(tmp_path):
    source = tmp_path / "cart.step"
    make_cart_assembly(source)
    package = tmp_path / "plain.aasx"
    write_aasx(extract_document(str(source)), str(package))
    with zipfile.ZipFile(package) as archive:
        assert b"UseRecords" not in archive.read("aasx/data.xml")
