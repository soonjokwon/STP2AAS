"""Analytic source-to-package checks; numerical oracle is independent of OCCT.

The synthetic CAD writer and extractor still share OCCT, so these cases do not
establish fidelity for vendor-exported freeform geometry.
"""
import json
import math
import zipfile

import pytest
from lxml import etree

pytest.importorskip("OCC")

from scripts.make_test_fixtures import make_assembly, make_single_part
from step2aas.aasx_writer import write_aasx
from step2aas.extract import extract_document


@pytest.mark.parametrize("mode", ["hierarchical", "flat", "single"])
def test_analytic_assembly_totals_and_occurrence_poses(tmp_path, mode):
    source = tmp_path / "analytic.step"
    make_assembly(source)
    package = tmp_path / "analytic.aasx"
    doc = extract_document(str(source))
    write_aasx(doc, str(package), assembly_structure=mode)
    with zipfile.ZipFile(package) as archive:
        xml = etree.fromstring(archive.read("aasx/data.xml"))
        ns = {"a": etree.QName(xml).namespace}
        volumes = [float(node.findtext("a:value", namespaces=ns))
                   for node in xml.findall(".//a:property", ns)
                   if node.findtext("a:idShort", namespaces=ns) == "Volume"]
        areas = [float(node.findtext("a:value", namespaces=ns))
                 for node in xml.findall(".//a:property", ns)
                 if node.findtext("a:idShort", namespaces=ns) == "SurfaceArea"]
        assert max(volumes) == pytest.approx(16000 + 750 * math.pi, rel=1e-8)
        assert max(areas) == pytest.approx(4800 + 350 * math.pi, rel=1e-8)
        scenes = [json.loads(archive.read(name)) for name in archive.namelist()
                  if name.endswith("__scene.json")]
        scene = max(scenes, key=lambda item: len(item["instances"]))
        translations = sorted((i["world"][3], i["world"][7], i["world"][11])
                              for i in scene["instances"])
        assert translations == [(0.0, 0.0, 0.0), (0.0, 50.0, 0.0), (50.0, 0.0, 0.0)]
        assert len({i["asset"] for i in scene["instances"]}) == 2


def test_analytic_single_box_properties(tmp_path):
    source = tmp_path / "box.step"
    make_single_part(source)
    doc = extract_document(str(source))
    assert doc.root.props.volume_mm3 == pytest.approx(24000, rel=1e-8)
    assert doc.root.props.surface_area_mm2 == pytest.approx(5200, rel=1e-8)
    assert doc.root.bbox_mm == pytest.approx((0, 0, 0, 40, 30, 20), abs=1e-5)


def test_xml_reference_loads_real_analytic_geometry(tmp_path):
    geometry = tmp_path / "box.step"
    make_single_part(geometry)
    source = tmp_path / "box.stpx"
    source.write_text("""<Root><Part uid="box"><PartVersion><PartView uid="view">
    <DocumentAssignment><AssignedDocument uidRef="file"/></DocumentAssignment>
    </PartView></PartVersion></Part><File uid="file"><ExternalItem>
    <Id id="box.step"/></ExternalItem></File></Root>""")
    doc = extract_document(str(source))
    assert doc.root.props.volume_mm3 == pytest.approx(24000, rel=1e-8)
    assert doc.root.props.surface_area_mm2 == pytest.approx(5200, rel=1e-8)
    assert len(doc.source_manifest) == 2
    package = tmp_path / "xml-box.aasx"
    write_aasx(doc, str(package))
    with zipfile.ZipFile(package) as archive:
        embedded = [archive.read(name) for name in archive.namelist() if name.endswith(".stp")]
        assert embedded == [geometry.read_bytes()]
