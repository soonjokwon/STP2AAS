"""C21 describes the delivered STEP, not PMI detected upstream.

Synthetic IR flags isolate the packaging contract from GD&T detection. These
cases verify byte lineage and metadata; they do not validate semantic or
graphical PMI fidelity in arbitrary CAD files.
"""

import zipfile

import pytest
from lxml import etree

pytest.importorskip("OCC")

from scripts.make_test_fixtures import make_single_part
from step2aas.aasx_writer import write_aasx
from step2aas.extract import extract_document
from step2aas.mapping.models3d import build_models3d
from step2aas.model import P21Header, PartNode, StepDocument


def _model_files(archive):
    xml = etree.fromstring(archive.read("aasx/data.xml"))
    ns = {"a": etree.QName(xml).namespace}
    for entry in xml.xpath(
        ".//a:submodelElementList[a:idShort='Model3D']/a:value/a:submodelElementCollection",
        namespaces=ns,
    ):
        file_name = entry.xpath(
            ".//a:file[a:idShort='DigitalFile']/a:value/text()", namespaces=ns
        )
        pmi = entry.xpath(
            "a:value/a:submodelElementCollection[a:idShort='Capability']/a:value/"
            "a:submodelElementList[a:idShort='EmbeddedInfo']/a:value/a:property/a:value/text()",
            namespaces=ns,
        )
        yield file_name[0] if file_name else None, pmi


@pytest.mark.parametrize("mode", ["hierarchical", "flat", "single"])
@pytest.mark.parametrize("xml_source", [False, True], ids=["p21", "xml"])
def test_pmi_metadata_follows_payload_lineage(tmp_path, mode, xml_source):
    source = tmp_path / "box.step"
    make_single_part(source)
    leaf = extract_document(str(source)).root
    leaf.name = "Leaf"
    leaf.ref_key = "leaf"
    leaf.has_pmi = True  # reader detection is deliberately isolated here
    if xml_source:
        leaf.source_file = str(source)
    sub = PartNode("Sub", "sub", ref_key="sub", children=[leaf], has_pmi=True)
    root = PartNode("Root", "root", ref_key="root", children=[sub])
    document_source = tmp_path / "structure.stpx" if xml_source else source
    if xml_source:
        document_source.write_text("<Root/>")
    doc = StepDocument(root, P21Header(), str(document_source), "pmi-lineage-test")
    package = tmp_path / f"{mode}.aasx"
    write_aasx(doc, str(package), assembly_structure=mode)
    with zipfile.ZipFile(package) as archive:
        entries = list(_model_files(archive))
        assert len(entries) == (3 if mode == "hierarchical" else 2)
        for file_name, pmi in entries:
            assert file_name is not None
            verbatim = ("Leaf_" in file_name) if xml_source else ("Root_" in file_name)
            if verbatim:
                assert archive.read(file_name.lstrip("/")) == source.read_bytes()
                assert pmi == ["PMI"]
            else:
                assert archive.read(file_name.lstrip("/")) != source.read_bytes()
                assert pmi == []
    assert leaf.has_pmi and sub.has_pmi  # output policy never rewrites source evidence
    assert not root.has_pmi


@pytest.mark.parametrize("embed", [True, False], ids=["embedded", "linked"])
def test_original_single_part_keeps_detected_pmi(tmp_path, embed):
    source = tmp_path / "part.step"
    make_single_part(source)
    doc = extract_document(str(source))
    doc.root.has_pmi = True
    package = tmp_path / "part.aasx"
    write_aasx(doc, str(package), embed=embed)
    with zipfile.ZipFile(package) as archive:
        [(file_name, pmi)] = list(_model_files(archive))
        assert pmi == ["PMI"]
        if embed:
            assert archive.read(file_name.lstrip("/")) == source.read_bytes()
        else:
            assert file_name.replace("\\", "/") == str(source).replace("\\", "/")


def test_missing_geometry_never_advertises_embedded_pmi(tmp_path):
    leaf = PartNode("Missing", "missing", ref_key="missing", has_pmi=True)
    doc = StepDocument(leaf, P21Header(), "", "missing-geometry")
    package = tmp_path / "missing.aasx"
    write_aasx(doc, str(package))
    with zipfile.ZipFile(package) as archive:
        assert list(_model_files(archive)) == [(None, [])]


def test_mapping_requires_explicit_payload_evidence_for_pmi():
    part = PartNode("Source", "source", has_pmi=True)
    submodel = build_models3d(part, "derived.stp", "preview.png", "urn:sm:pmi", P21Header())
    entry = next(iter(next(iter(submodel.submodel_element)).value))
    capability = next(element for element in entry.value if element.id_short == "Capability")
    assert all(element.id_short != "EmbeddedInfo" for element in capability.value)
