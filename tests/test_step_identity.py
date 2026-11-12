"""D11: identity taken from the STEP file (product ids, NAUOs, UUIDs), persistent
asset ids, and the trace from a use back to the STEP file."""

from __future__ import annotations

import json
import pathlib
import re
import zipfile

import pytest
from lxml import etree

pytest.importorskip("OCC")

from step2aas.aasx_writer import _name_product, _p21_string, write_aasx
from step2aas.extract import extract_document
from step2aas.extract.step_identity import scan_p21

AS1 = pathlib.Path(__file__).parent / "as1" / "as1_root.stp"
NS = "https://example.com/parts"


def _nodes(node):
    yield node
    for child in node.children:
        yield from _nodes(child)


def _package(tmp_path, source, name, **kwargs):
    out = tmp_path / f"{name}.aasx"
    write_aasx(extract_document(str(source)), str(out), **kwargs)
    return zipfile.ZipFile(out)


def _ids(archive):
    xml = etree.fromstring(archive.read("aasx/data.xml"))
    ns = {"a": etree.QName(xml).namespace}
    assets = sorted(set(xml.xpath(".//a:assetInformation/a:globalAssetId/text()", namespaces=ns)))
    shells = sorted(xml.xpath(".//a:assetAdministrationShell/a:id/text()", namespaces=ns))
    submodels = sorted(xml.xpath(".//a:submodel/a:id/text()", namespaces=ns))
    return assets, shells, submodels


def _reexport(tmp_path) -> pathlib.Path:
    """AS1 with another time stamp: the same model, different bytes."""
    text = AS1.read_text(encoding="latin-1")
    changed, n = re.subn(r"(FILE_NAME\s*\(\s*'[^']*'\s*,\s*')[^']*'", r"\g<1>2031-01-01T00:00:00'",
                         text, count=1)
    assert n == 1 and changed != text
    path = tmp_path / "as1_reexport.stp"
    path.write_text(changed, encoding="latin-1")
    return path


def test_designs_and_uses_carry_step_identity():
    root = extract_document(str(AS1)).root
    assert (root.step_product_id, root.step_nauo_ref) == ("as1", None)
    nodes = [n for n in _nodes(root) if n is not root]
    assert all(n.step_product_id and n.step_pd_ref.startswith("#") for n in nodes)
    assert all(n.step_nauo_id and n.step_nauo_ref.startswith("#") for n in nodes)
    # one design, several uses: same product_definition, different NAUOs
    nuts = [n for n in nodes if n.step_product_id == "nut"]
    assert len({n.step_pd_ref for n in nuts}) == 1
    assert len({n.step_nauo_ref for n in nuts}) == 3
    # every instance name points at an entity of that type in the file
    text = AS1.read_text(encoding="latin-1")
    for n in nodes:
        assert re.search(rf"{n.step_nauo_ref}\s*=\s*NEXT_ASSEMBLY_USAGE_OCCURRENCE\b", text)
        assert re.search(rf"{n.step_pd_ref}\s*=\s*PRODUCT_DEFINITION\b", text)


def test_default_ids_stay_file_scoped(tmp_path):
    a = _ids(_package(tmp_path, AS1, "a"))
    b = _ids(_package(tmp_path, _reexport(tmp_path), "b"))
    assert all(x.startswith("urn:step2aas:asset:") for x in a[0])
    assert not set(a[0]) & set(b[0])  # another file, other identifiers


@pytest.mark.parametrize("mode", ["hierarchical", "lean-hierarchical", "flat", "single"])
def test_namespace_gives_ids_that_survive_a_reexport(tmp_path, mode):
    kwargs = {"assembly_structure": mode, "asset_namespace": NS}
    a = _ids(_package(tmp_path, AS1, "a", **kwargs))
    b = _ids(_package(tmp_path, _reexport(tmp_path), "b", **kwargs))
    assert a == b
    assert f"{NS}/as1" in a[0] and all(x.startswith(NS + "/") for x in a[0])
    assert len(set(a[1])) == len(a[1]) and len(set(a[2])) == len(a[2])


def test_modes_share_assets_but_not_shell_ids(tmp_path):
    h = _ids(_package(tmp_path, AS1, "h", assembly_structure="hierarchical", asset_namespace=NS))
    f = _ids(_package(tmp_path, AS1, "f", assembly_structure="flat", asset_namespace=NS))
    assert set(f[0]) <= set(h[0])
    root_h = [s for s in h[1] if s not in f[1]]
    assert root_h  # the root shell differs per mode, as before


def test_specific_asset_ids_state_the_step_product(tmp_path):
    xml = etree.fromstring(_package(tmp_path, AS1, "a").read("aasx/data.xml"))
    ns = {"a": etree.QName(xml).namespace}
    values = xml.xpath(
        ".//a:assetInformation/a:specificAssetIds/a:specificAssetId"
        "[a:name='StepProductId']/a:value/text()",
        namespaces=ns,
    )
    assert {"as1", "nut", "bolt", "plate"} <= set(values)


def test_bad_namespace_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        write_aasx(extract_document(str(AS1)), str(tmp_path / "x.aasx"), asset_namespace="parts")


def test_uses_trace_back_to_the_step_file(tmp_path):
    archive = _package(tmp_path, AS1, "a", assembly_structure="lean-hierarchical",
                       use_records=True)
    scene_name = next(n for n in archive.namelist() if n.endswith("as1_lean_h___assembly__scene.json"))
    scene = json.loads(archive.read(scene_name))
    text = AS1.read_text(encoding="latin-1")
    assert len(scene["step_source_sha256"]) == 64
    assert len(scene["instances"]) == 18
    seen = set()
    for inst in scene["instances"]:
        assert len(inst["nauo"]) == len(inst["path"].split("/")) == len(inst["nauo_id"])
        for ref in inst["nauo"]:
            assert re.search(rf"{ref}\s*=\s*NEXT_ASSEMBLY_USAGE_OCCURRENCE\b", text)
        seen.add(tuple(inst["nauo"]))
    assert len(seen) == 18  # the NAUO chain tells every use apart
    xml = etree.fromstring(archive.read("aasx/data.xml"))
    ns = {"a": etree.QName(xml).namespace}
    paths = xml.xpath(".//a:property[a:idShort='StepNauoPath']/a:value/text()", namespaces=ns)
    assert len(paths) >= 18 and all(p.startswith("#") for p in paths)


def test_split_files_keep_the_product_name_and_lowercase_names(tmp_path):
    archive = _package(tmp_path, AS1, "a")
    names = archive.namelist()
    assert all(n == n.lower() for n in names if n.startswith("aasx/suppl/"))
    rels = archive.read("aasx/_rels/data.xml.rels").decode()
    for target in re.findall(r'Target="/([^"]+)"', rels):
        assert target in names
    bolt = next(n for n in names if re.search(r"/bolt_.*\.stp$", n))
    assert re.search(rb"PRODUCT\s*\(\s*'bolt'\s*,\s*'bolt'", archive.read(bolt))


def test_uuid_of_a_design_becomes_its_asset_id(tmp_path):
    root = extract_document(str(AS1)).root
    nut = next(n for n in _nodes(root) if n.step_product_id == "nut")
    uid = "123e4567-e89b-52d3-a456-426614174000"
    text = AS1.read_text(encoding="latin-1")
    line = f"#999901=V5_UUID_ATTRIBUTE('{uid}',({nut.step_pd_ref}));\n"
    head, sep, tail = text.rpartition("ENDSEC;")
    source = tmp_path / "as1_uuid.stp"
    source.write_text(head + line + sep + tail, encoding="latin-1")
    assert scan_p21(str(source)).uuid_of[nut.step_pd_ref] == uid
    assets, _, _ = _ids(_package(tmp_path, source, "u"))
    assert f"urn:uuid:{uid}" in assets


def test_p21_strings_and_product_renaming():
    assert _p21_string("O'Ring \\ 5") == "'O''Ring \\\\ 5'"
    assert _p21_string("볼트") == "'\\X2\\BCFC\\X0\\\\X2\\D2B8\\X0\\'"
    data = b"#7 = PRODUCT('SOLID','SOLID','',(#8));"
    assert _name_product(data, ("P-1", "bolt")) == b"#7 = PRODUCT('P-1','bolt','',(#8));"
    two = data + b"#9 = PRODUCT('A','A','',(#8));"
    assert _name_product(two, ("P-1", "bolt")) == two
