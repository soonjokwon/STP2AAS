"""D2/D5 regressions for AP242 dependency identity and reference resolution."""

import hashlib
import json
import shutil
from pathlib import Path

import pytest
from lxml import etree

from step2aas.extract import ap242xml
from step2aas.model import P21Header, PartNode, PhysicalProps, StepDocument


def _source(path: Path, references: list[str], *, label: str = "Assembly") -> Path:
    """Small parser fixture; geometry loading is mocked, not CAD validation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    root = etree.Element("Root")
    part = etree.SubElement(root, "Part", uid="root")
    etree.SubElement(etree.SubElement(part, "Name"), "CharacterString").text = label
    pv = etree.SubElement(etree.SubElement(part, "PartVersion"), "PartView", uid="pv-root")
    for index, reference in enumerate(references):
        edge = etree.SubElement(pv, "ViewOccurrenceRelationship")
        etree.SubElement(edge, "Related", uidRef=f"o{index}")
        child = etree.SubElement(root, "Part", uid=f"p{index}")
        view = etree.SubElement(
            etree.SubElement(child, "PartVersion"), "PartView", uid=f"pv{index}"
        )
        etree.SubElement(view, "Occurrence", uid=f"o{index}")
        assigned = etree.SubElement(view, "DocumentAssignment")
        etree.SubElement(assigned, "AssignedDocument", uidRef=f"f{index}")
        file_el = etree.SubElement(root, "File", uid=f"f{index}")
        etree.SubElement(etree.SubElement(file_el, "ExternalItem"), "Id", id=reference)
    path.write_bytes(etree.tostring(root, encoding="utf-8"))
    return path


def _geometry(path: Path, content: bytes = b"synthetic geometry v1") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


@pytest.fixture(autouse=True)
def isolated_geometry(monkeypatch):
    monkeypatch.setattr(ap242xml, "load_geometry", lambda _: (None, PhysicalProps(), None, False))


def _extract(path):
    return ap242xml.extract_ap242_xml(str(path))


def _keys(node):
    return [node.ref_key] + [key for child in node.children for key in _keys(child)]


def test_external_geometry_edit_changes_identity(tmp_path):
    source = _source(tmp_path / "assembly.stpx", ["parts/bolt.stp"])
    geometry = _geometry(tmp_path / "parts/bolt.stp")
    before = _extract(source)
    geometry.write_bytes(b"synthetic geometry v2")
    after = _extract(source)
    assert before.file_hash != after.file_hash
    assert (
        before.legacy_file_hash
        == after.legacy_file_hash
        == hashlib.sha256(source.read_bytes()).hexdigest()
    )
    assert before.root.children[0].ref_key == after.root.children[0].ref_key
    assert after.identity_scheme == "ap242-dependency-v2"
    assert (
        next(item for item in after.source_manifest if item["role"] == "geometry")["sha256"]
        == hashlib.sha256(geometry.read_bytes()).hexdigest()
    )


def test_nested_xml_edit_changes_identity(tmp_path):
    source = _source(tmp_path / "assembly.stpx", ["sub/module.stpx"])
    nested = _source(tmp_path / "sub/module.stpx", ["../geometry/bolt.stp"])
    _geometry(tmp_path / "geometry/bolt.stp")
    before = _extract(source)
    _source(nested, ["../geometry/bolt.stp"], label="Changed module")
    after = _extract(source)
    assert before.file_hash != after.file_hash
    assert before.legacy_file_hash == after.legacy_file_hash
    assert {item["path"] for item in after.source_manifest} == {
        "assembly.stpx",
        "sub/module.stpx",
        "geometry/bolt.stp",
    }


def test_relocating_dependency_tree_preserves_manifest_and_keys(tmp_path):
    first = tmp_path / "original"
    source = _source(first / "assembly.stpx", ["left/module.stpx", "right/module.stpx"])
    _source(first / "left/module.stpx", ["../geometry/bolt.stp"])
    _source(first / "right/module.stpx", ["../geometry/bolt.stp"])
    _geometry(first / "geometry/bolt.stp")
    before = _extract(source)
    relocated = tmp_path / "elsewhere" / "copied"
    shutil.copytree(first, relocated)
    after = _extract(relocated / "assembly.stpx")
    assert before.file_hash == after.file_hash
    assert before.source_manifest == after.source_manifest
    assert _keys(before.root) == _keys(after.root)
    assert all(not Path(item["path"]).is_absolute() for item in after.source_manifest)
    assert before.root.children[0].ref_key != before.root.children[1].ref_key
    assert (
        before.root.children[0].children[0].ref_key == before.root.children[1].children[0].ref_key
    )


def test_same_basename_in_distinct_directories_does_not_collapse(tmp_path):
    source = _source(tmp_path / "assembly.stpx", ["left/bolt.stp", "right/bolt.stp"])
    left = _geometry(tmp_path / "left/bolt.stp")
    right = _geometry(tmp_path / "right/bolt.stp")
    doc = _extract(source)
    assert [child.ref_key for child in doc.root.children] == [
        "file:left/bolt.stp",
        "file:right/bolt.stp",
    ]
    assert [Path(child.source_file) for child in doc.root.children] == [
        left.resolve(),
        right.resolve(),
    ]


def test_repeated_geometry_file_is_loaded_and_recorded_once(tmp_path, monkeypatch):
    source = _source(tmp_path / "assembly.stpx", ["parts/bolt.stp", "parts/../parts/bolt.stp"])
    _geometry(tmp_path / "parts/bolt.stp")
    loads = []

    def load(path):
        loads.append(path)
        return None, PhysicalProps(), None, False

    monkeypatch.setattr(ap242xml, "load_geometry", load)
    doc = _extract(source)
    assert len(loads) == 1
    assert doc.root.children[0].ref_key == doc.root.children[1].ref_key
    assert len([item for item in doc.source_manifest if item["role"] == "geometry"]) == 1


def test_missing_dependency_becoming_present_changes_fingerprint(tmp_path):
    source = _source(tmp_path / "assembly.stpx", ["missing/bolt.stp"])
    before = _extract(source)
    missing = next(item for item in before.source_manifest if item["role"] == "geometry")
    assert missing == {
        "path": "missing/bolt.stp",
        "role": "geometry",
        "status": "missing",
        "sha256": "",
    }
    assert before.root.children[0].source_file is None
    _geometry(tmp_path / "missing/bolt.stp")
    after = _extract(source)
    assert before.file_hash != after.file_hash
    assert before.legacy_file_hash == after.legacy_file_hash
    assert (
        next(item for item in after.source_manifest if item["role"] == "geometry")["status"]
        == "present"
    )
    assert after.root.children[0].source_file is not None


def test_reference_directory_is_not_discarded_for_basename_fallback(tmp_path):
    source = _source(tmp_path / "assembly.stpx", ["absent/bolt.stp"])
    _geometry(tmp_path / "bolt.stp")  # A different file must not satisfy the reference.
    doc = _extract(source)
    assert doc.root.children[0].source_file is None
    assert {item["path"] for item in doc.source_manifest} == {"assembly.stpx", "absent/bolt.stp"}


def test_windows_separator_and_relative_parent_are_resolved(tmp_path):
    source = _source(tmp_path / "assembly.stpx", [r"sub\module.stpx"])
    _source(tmp_path / "sub/module.stpx", [r"..\geometry\bolt.stp"])
    geometry = _geometry(tmp_path / "geometry/bolt.stp")
    doc = _extract(source)
    assert Path(doc.root.children[0].children[0].source_file) == geometry.resolve()
    assert doc.root.children[0].children[0].ref_key == "file:geometry/bolt.stp"


def test_nested_cycle_still_terminates_with_stable_gap_identity(tmp_path, caplog):
    source = _source(tmp_path / "assembly.stpx", ["sub/module.stpx"])
    _source(tmp_path / "sub/module.stpx", ["../assembly.stpx"])
    before = _extract(source)
    assert "cycle/depth" in caplog.text
    boundary = before.root.children[0].children[0]
    assert not boundary.children
    assert boundary.ref_key == "unexpanded:file:assembly.stpx"
    assert len(before.source_manifest) == 2
    assert before.file_hash == _extract(source).file_hash


def test_canonical_digest_is_reproducible_from_manifest(tmp_path):
    source = _source(tmp_path / "assembly.stpx", ["z.stp", "a.stp"])
    _geometry(tmp_path / "z.stp")
    _geometry(tmp_path / "a.stp")
    doc = _extract(source)
    canonical = json.dumps(
        {"identity_scheme": "ap242-dependency-v2", "sources": doc.source_manifest},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    assert doc.file_hash == hashlib.sha256(canonical).hexdigest()
    assert [item["path"] for item in doc.source_manifest] == ["a.stp", "assembly.stpx", "z.stp"]


def test_ir_default_preserves_p21_identity_contract():
    doc = StepDocument(PartNode("Part", "part"), P21Header(), "part.stp", "existing-p21-hash")
    assert doc.file_hash == "existing-p21-hash"
    assert doc.identity_scheme == "p21-sha256-v1"
    assert doc.source_manifest == []
    assert doc.legacy_file_hash is None


def test_case_variation_is_resolved_within_the_referenced_directory(tmp_path):
    source = _source(tmp_path / "assembly.stpx", ["parts/bolt.STP"])
    geometry = _geometry(tmp_path / "Parts/Bolt.stp")
    doc = _extract(source)
    assert Path(doc.root.children[0].source_file) == geometry.resolve()
    assert doc.root.children[0].ref_key == "file:Parts/Bolt.stp"


def test_missing_nested_xml_becoming_present_changes_identity(tmp_path):
    source = _source(tmp_path / "assembly.stpx", ["sub/module.stpx"])
    before = _extract(source)
    assert any(
        item["role"] == "xml" and item["status"] == "missing" for item in before.source_manifest
    )
    _source(tmp_path / "sub/module.stpx", ["bolt.stp"])
    _geometry(tmp_path / "sub/bolt.stp")
    after = _extract(source)
    assert before.file_hash != after.file_hash
    assert before.legacy_file_hash == after.legacy_file_hash
    assert len(after.root.children[0].children) == 1


def test_geometry_load_failure_preserves_source_hash_and_fallback(tmp_path, monkeypatch):
    source = _source(tmp_path / "assembly.stpx", ["part.stp"])
    geometry = _geometry(tmp_path / "part.stp")

    def fail(path):
        raise RuntimeError("synthetic CAD load error")

    monkeypatch.setattr(ap242xml, "load_geometry", fail)
    doc = _extract(source)
    assert doc.root.children[0].props.provenance.source == "fallback"
    entry = next(item for item in doc.source_manifest if item["role"] == "geometry")
    assert entry["status"] == "present"
    assert entry["sha256"] == hashlib.sha256(geometry.read_bytes()).hexdigest()


def test_nested_depth_boundary_still_stops_with_recorded_boundary(tmp_path, caplog):
    for index in range(22):
        _source(tmp_path / f"level{index}.stpx", [f"level{index + 1}.stpx"])
    doc = _extract(tmp_path / "level0.stpx")
    assert "cycle/depth" in caplog.text
    node = doc.root
    while node.children:
        node = node.children[0]
    assert node.ref_key == "unexpanded:file:level21.stpx"
    assert len(doc.source_manifest) == 22
    assert "level22.stpx" not in {item["path"] for item in doc.source_manifest}
