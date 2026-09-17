"""Negative controls for XML inspection independent of the serialization SDK."""
import zipfile

import pytest
from lxml import etree

from scripts.validate_package import validate_package
from step2aas.aasx_writer import write_aasx
from step2aas.model import P21Header, PartNode, StepDocument


@pytest.fixture
def package(tmp_path):
    root = PartNode("Assembly", "root", ref_key="root", children=[
        PartNode("Leaf", "leaf", ref_key="leaf"),
    ])
    path = tmp_path / "valid.aasx"
    write_aasx(StepDocument(root, P21Header(), "", "validator-fixture"), str(path))
    return path


def _mutate(path, edit):
    with zipfile.ZipFile(path) as archive:
        contents = {name: archive.read(name) for name in archive.namelist()}
    root = etree.fromstring(contents["aasx/data.xml"])
    ns = {"a": etree.QName(root).namespace}
    edit(root, ns, contents)
    contents["aasx/data.xml"] = etree.tostring(root)
    target = path.with_name("invalid.aasx")
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in contents.items():
            archive.writestr(name, content)
    return target


def test_independent_profile_accepts_generated_structure(package):
    result = validate_package(package)
    assert result["status"] == "pass", result["errors"]
    assert result["xsd"]["status"] == "not_run"
    assert result["counts"]["relationships"] == 1


@pytest.mark.parametrize("defect", ["dangling_asset", "global_reference_endpoint", "missing_file",
                                         "wrong_entity_key_type", "wrong_submodel_key_type"])
def test_independent_profile_rejects_corrupted_output(package, defect):
    def edit(root, ns, contents):
        if defect == "dangling_asset":
            root.find(".//a:entity/a:globalAssetId", ns).text = "urn:missing:asset"
        elif defect == "global_reference_endpoint":
            endpoint = root.find(".//a:relationshipElement/a:second", ns)
            endpoint.find("a:type", ns).text = "ExternalReference"
            keys = endpoint.find("a:keys", ns)
            for key in list(keys)[1:]:
                keys.remove(key)
            keys[0].find("a:type", ns).text = "GlobalReference"
            keys[0].find("a:value", ns).text = "urn:asset:wrong-endpoint-kind"
        elif defect == "wrong_entity_key_type":
            keys = root.findall(".//a:relationshipElement/a:second/a:keys/a:key", ns)
            keys[-1].find("a:type", ns).text = "Property"
        elif defect == "wrong_submodel_key_type":
            key = root.find("a:assetAdministrationShells/a:assetAdministrationShell/"
                            "a:submodels/a:reference/a:keys/a:key/a:type", ns)
            key.text = "AssetAdministrationShell"
        else:
            del contents[next(name for name in contents if name.endswith(".png"))]
    result = validate_package(_mutate(package, edit))
    assert result["status"] == "fail"
    expected = {
        "dangling_asset": "self-managed entity asset",
        "global_reference_endpoint": "endpoints must resolve to Entity",
        "missing_file": "missing or empty referenced supplementary file",
        "wrong_entity_key_type": "endpoints must resolve to Entity",
        "wrong_submodel_key_type": "unresolved shell submodel reference",
    }[defect]
    assert any(expected in error for error in result["errors"])


@pytest.mark.parametrize("defect", ["provenance", "xml", "missing_xml", "zip"])
def test_malformed_input_returns_structured_failure(package, defect):
    if defect == "zip":
        package.write_bytes(b"not a zip")
    else:
        with zipfile.ZipFile(package) as archive:
            data = {name: archive.read(name) for name in archive.namelist()}
        if defect == "provenance":
            data["aasx/suppl/conversion-provenance.json"] = b"not json"
        elif defect == "xml":
            data["aasx/data.xml"] = b"<not-closed"
        else:
            del data["aasx/data.xml"]
        with zipfile.ZipFile(package, "w") as archive:
            for name, content in data.items():
                archive.writestr(name, content)
    report = validate_package(package)
    assert report["status"] == "fail"
    assert report["errors"]
