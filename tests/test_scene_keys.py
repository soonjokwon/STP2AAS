"""PlacementScene occurrence keys (G1/D8): each pose is bound to its BOM Node.

Checked with an independent XML reading of the package (no writer/IR objects).
"""

import json
import zipfile

import pytest
from lxml import etree

pytest.importorskip("OCC")

from scripts.make_test_fixtures import make_nested_assembly
from step2aas.aasx_writer import write_aasx
from step2aas.extract import extract_document


def _entities_by_path(xml, ns):
    """(submodel id, idShort path) -> (entityType, globalAssetId)."""
    out = {}
    for sm in xml.findall(".//a:submodels/a:submodel", ns):
        sm_id = sm.findtext("a:id", namespaces=ns)

        def walk(el, prefix, sm_id=sm_id):
            for ent in (
                el.findall("a:statements/a:entity", ns)
                if prefix
                else el.findall("a:submodelElements/a:entity", ns)
            ):
                name = ent.findtext("a:idShort", namespaces=ns)
                path = f"{prefix}.{name}" if prefix else name
                out[(sm_id, path)] = (
                    ent.findtext("a:entityType", namespaces=ns),
                    ent.findtext("a:globalAssetId", namespaces=ns),
                )
                walk(ent, path, sm_id)

        walk(sm, "")
    return out


@pytest.mark.parametrize(
    "mode", ["hierarchical", "flat", "single", "lean-hierarchical", "lean-flat"]
)
def test_scene_instances_bind_to_bom_nodes(tmp_path, mode):
    source = tmp_path / "nested.step"
    make_nested_assembly(source)
    package = tmp_path / "nested.aasx"
    write_aasx(extract_document(str(source)), str(package), assembly_structure=mode)
    with zipfile.ZipFile(package) as archive:
        xml = etree.fromstring(archive.read("aasx/data.xml"))
        ns = {"a": etree.QName(xml).namespace}
        entities = _entities_by_path(xml, ns)
        scenes = [
            json.loads(archive.read(n)) for n in archive.namelist() if n.endswith("__scene.json")
        ]
    root_scene = max(scenes, key=lambda s: len(s["instances"]))
    assert len(root_scene["instances"]) == 5  # 2 x (Cube, Pin) + Cube
    assert sorted(i["path"] for i in root_scene["instances"]) == ["0/0", "0/1", "1/0", "1/1", "2"]
    for scene in scenes:
        for inst in scene["instances"]:
            key = (inst["bom"], inst["node"])
            assert key in entities, key
            kind, gid = entities[key]
            if inst["below"] == 0 and kind == "SelfManagedEntity":
                assert gid == inst["asset"]
    below = {i["path"]: i["below"] for i in root_scene["instances"]}
    if mode in ("hierarchical", "lean-hierarchical"):  # OneDown (Pair is repeated): deeper occurrences have no Node here
        assert below == {"0/0": 1, "0/1": 1, "1/0": 1, "1/1": 1, "2": 0}
    else:  # Full BOM: every occurrence has its own Node
        assert set(below.values()) == {0}
        assert len({i["node"] for i in root_scene["instances"]}) == 5


def test_repeated_subassembly_sharing_by_mode(tmp_path):
    """Prop. 1: a repeated sub-assembly shares one asset id only if it has a shell."""
    source = tmp_path / "nested.step"
    make_nested_assembly(source)
    shared = {}
    for mode in ("hierarchical", "flat", "single"):
        package = tmp_path / f"{mode}.aasx"
        write_aasx(extract_document(str(source)), str(package), assembly_structure=mode)
        with zipfile.ZipFile(package) as archive:
            xml = etree.fromstring(archive.read("aasx/data.xml"))
        ns = {"a": etree.QName(xml).namespace}
        pair_ids = [
            e.findtext("a:globalAssetId", namespaces=ns)
            for e in xml.iter(f"{{{ns['a']}}}entity")
            if (
                e.find("a:displayName/a:langStringNameType/a:text", ns) is not None
                and e.find("a:displayName/a:langStringNameType/a:text", ns).text == "Pair"
            )
        ]
        shared[mode] = pair_ids
    assert len(shared["hierarchical"]) == 2 and len(set(shared["hierarchical"])) == 1
    assert shared["flat"] == [None, None]
    assert shared["single"] == [None, None]
