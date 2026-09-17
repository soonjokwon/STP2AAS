"""Regression cases from the 2026-09-06 implementation review."""

import io
import json

import pytest
from basyx.aas.adapter.aasx import AASXReader, DictSupplementaryFileContainer

from step2aas.aasx_writer import _Ids, write_aasx
from step2aas.compat import ObjectStore
from step2aas.extract import ap242xml
from step2aas.model import P21Header, PartNode, PhysicalProps, StepDocument


def _read(path):
    store = ObjectStore()
    files = DictSupplementaryFileContainer()
    with AASXReader(str(path)) as reader:
        reader.read_into(store, files)
    return store, files


def _bytes(files, name):
    output = io.BytesIO()
    files.write_file(name, output)
    return output.getvalue()


def test_xml_single_part_embeds_referenced_step(tmp_path, monkeypatch):
    """C1: the root XML is a structure source, never a STEP geometry payload."""
    source = tmp_path / "single.stpx"
    source.write_text("""<Root><Part uid="p"><Name><CharacterString>Widget</CharacterString>
    </Name><PartVersion><PartView uid="pv"><DocumentAssignment>
    <AssignedDocument uidRef="f"/></DocumentAssignment></PartView></PartVersion></Part>
    <File uid="f"><ExternalItem><Id id="part.stp"/></ExternalItem></File></Root>""")
    step = tmp_path / "part.stp"
    # Geometry loading is isolated; this test checks source selection, not CAD validity.
    step.write_bytes(b"ISO-10303-21;\nEND-ISO-10303-21;")
    monkeypatch.setattr(ap242xml, "load_geometry", lambda _: (None, PhysicalProps(), None, False))
    doc = ap242xml.extract_ap242_xml(str(source))
    package = tmp_path / "single.aasx"
    write_aasx(doc, str(package))
    _, files = _read(package)
    geometry = [name for name in files if name.endswith(".stp")]
    assert len(geometry) == 1
    assert _bytes(files, geometry[0]) == step.read_bytes()


def test_xml_in_file_cycle_has_explicit_error(tmp_path):
    source = tmp_path / "cycle.stpx"
    source.write_text("""<Root><Part uid="p"><PartTypes><ClassString>assembly</ClassString>
    </PartTypes><PartVersion><PartView uid="pv"><Occurrence uid="o"/>
    <ViewOccurrenceRelationship><Related uidRef="o"/></ViewOccurrenceRelationship>
    </PartView></PartVersion></Part></Root>""")
    with pytest.raises(ValueError, match="cycle"):
        ap242xml.extract_ap242_xml(str(source))


def test_xml_without_parts_has_explicit_error(tmp_path):
    source = tmp_path / "empty.stpx"
    source.write_text("<Root/>")
    with pytest.raises(ValueError, match="no Part"):
        ap242xml.extract_ap242_xml(str(source))


def test_xml_reused_prototype_is_not_a_cycle(tmp_path):
    source = tmp_path / "reused.stpx"
    source.write_text("""<Root><Part uid="parent"><PartVersion><PartView uid="pv0">
    <ViewOccurrenceRelationship><Related uidRef="a"/></ViewOccurrenceRelationship>
    <ViewOccurrenceRelationship><Related uidRef="b"/></ViewOccurrenceRelationship>
    </PartView></PartVersion></Part><Part uid="child"><PartVersion><PartView uid="pv1">
    <Occurrence uid="a"/><Occurrence uid="b"/></PartView></PartVersion></Part></Root>""")
    doc = ap242xml.extract_ap242_xml(str(source))
    assert len(doc.root.children) == 2
    assert doc.root.children[0].ref_key == doc.root.children[1].ref_key


@pytest.mark.parametrize("node_count", [20, 21])
def test_xml_in_file_depth_boundary(tmp_path, node_count):
    parts = []
    for index in range(node_count):
        edge = (
            f'<ViewOccurrenceRelationship><Related uidRef="o{index + 1}"/>'
            "</ViewOccurrenceRelationship>"
            if index + 1 < node_count
            else ""
        )
        parts.append(
            f'<Part uid="p{index}"><PartVersion><PartView uid="pv{index}">'
            f'<Occurrence uid="o{index}"/>{edge}</PartView></PartVersion></Part>'
        )
    source = tmp_path / "deep.stpx"
    source.write_text("<Root>" + "".join(parts) + "</Root>")
    if node_count == 21:
        with pytest.raises(ValueError, match="depth exceeds 20"):
            ap242xml.extract_ap242_xml(str(source))
    else:
        node = ap242xml.extract_ap242_xml(str(source)).root
        count = 1
        while node.children:
            node = node.children[0]
            count += 1
        assert count == 20


def _translate(x):
    return [[1.0, 0.0, 0.0, x], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]]


def test_subassembly_scene_uses_prototype_coordinates(tmp_path):
    """D2/D8: a prototype scene excludes the placement of its first occurrence."""
    leaf = PartNode("Leaf", "leaf", ref_key="leaf", transform=_translate(2))
    sub = PartNode("Sub", "sub", ref_key="sub", transform=_translate(10), children=[leaf])
    sub2 = PartNode("Sub2", "sub", ref_key="sub", transform=_translate(30), children=[leaf])
    root = PartNode("Root", "root", ref_key="root", children=[sub, sub2])
    doc = StepDocument(root, P21Header(), "", "review-hash")
    package = tmp_path / "scene.aasx"
    write_aasx(doc, str(package))
    _, files = _read(package)
    scenes = {
        scene["assembly_asset_id"]: scene
        for name in files
        if name.endswith("__scene.json")
        for scene in [json.loads(_bytes(files, name))]
    }
    ids = _Ids(doc.file_hash)
    sub_scene = scenes[ids.asset("asm:sub")]
    root_scene = scenes[ids.asset("__assembly__")]
    assert [x["world"][3] for x in sub_scene["instances"]] == [2]
    assert [x["world"][3] for x in root_scene["instances"]] == [12, 32]
    assert sub.transform[0][3] == 10  # writing must preserve the occurrence IR


def test_partial_assembly_properties_are_not_reported_as_complete():
    from step2aas.aasx_writer import _aggregate_props
    known = PartNode("Known", "known", props=PhysicalProps(
        volume_mm3=12.0, surface_area_mm2=8.0))
    missing = PartNode("Missing", "missing", props=PhysicalProps(surface_area_mm2=2.0))
    root = PartNode("Root", "root", children=[known, missing])
    totals = _aggregate_props(root).props
    assert totals.volume_mm3 is None
    assert totals.surface_area_mm2 == 10.0
    missing.props.volume_mm3 = 0.0
    assert _aggregate_props(root).props.volume_mm3 == 12.0


def test_short_asset_names_are_compatible_with_pinned_xsd():
    from step2aas.aasx_writer import _IdShortAllocator
    allocator = _IdShortAllocator()
    assert allocator.allocate("A") == "A_"
    assert allocator.allocate("A") == "A__2"


def test_provenance_is_embedded_and_records_coverage(tmp_path):
    root = PartNode("Root", "root", ref_key="root", children=[
        PartNode("Known", "k", ref_key="k", props=PhysicalProps(volume_mm3=12.0)),
        PartNode("Missing", "m", ref_key="m"),
    ])
    doc = StepDocument(root, P21Header(), "", "synthetic-test")
    package = tmp_path / "coverage.aasx"
    write_aasx(doc, str(package))
    _, files = _read(package)
    record = json.loads(_bytes(files, "/aasx/suppl/conversion-provenance.json"))
    assert record["coverage"]["leaf_occurrences"] == 2
    assert record["coverage"]["occurrences_with_volume"] == 1
    assert record["identity_hash"] == "synthetic-test"
    assert record["toolchain"]["step2aas"] != "unavailable"
    assert record["source_manifest"] == []
