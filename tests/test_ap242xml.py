"""AP242 Domain Model XML (BOM) input tests.

These run against whatever AP242 .stpx datasets are present under tests/ (they are
large, real-world CAx-IF exports and are gitignored). If none are present the
tests skip — the synthetic STEP roundtrip in test_roundtrip.py still covers the
core pipeline.
"""

import pathlib

import pytest
from basyx.aas import model
from basyx.aas.adapter.aasx import AASXReader, DictSupplementaryFileContainer

from stp2aas.aasx_writer import write_aasx
from stp2aas.extract.ap242xml import extract_ap242_xml

TESTS = pathlib.Path(__file__).parent
ALL_STPX = sorted(TESTS.rglob("*.stpx"))


def _leaves(node):
    return [node] if not node.children else [x for c in node.children for x in _leaves(c)]


def _node_count(node):
    return 1 + sum(_node_count(c) for c in node.children)


def _single_part_stpx():
    """A .stpx that wraps a single part (fast to convert)."""
    for f in ALL_STPX:
        try:
            doc = extract_ap242_xml(str(f))
        except Exception:  # noqa: BLE001
            continue
        if _node_count(doc.root) <= 3 and any(n.shape_ref is not None for n in _leaves(doc.root)):
            return f
    return None


def _assembly_stpx():
    for f in ALL_STPX:
        if "assy" in f.name.lower() or "asm" in f.name.lower() or "kinematic" in f.name.lower():
            return f
    return None


@pytest.mark.skipif(not ALL_STPX, reason="no AP242 .stpx datasets present")
def test_ap242_assembly_tree_extraction():
    f = _assembly_stpx()
    if f is None:
        pytest.skip("no AP242 assembly .stpx present")
    doc = extract_ap242_xml(str(f))
    assert doc.header.schema == "AP242"
    assert doc.root.is_assembly, "top of an assembly .stpx should be an assembly node"
    leaves = _leaves(doc.root)
    assert leaves, "assembly produced no leaf parts"
    # most leaves should resolve to geometry (allowing a few genuinely-missing files)
    with_geo = sum(1 for n in leaves if n.shape_ref is not None)
    assert with_geo >= 0.5 * len(leaves), "too few AP242 parts resolved geometry"


@pytest.mark.skipif(not ALL_STPX, reason="no AP242 .stpx datasets present")
def test_ap242_single_part_roundtrip(tmp_path):
    f = _single_part_stpx()
    if f is None:
        pytest.skip("no single-part AP242 .stpx present")
    out = tmp_path / "ap242.aasx"
    write_aasx(extract_ap242_xml(str(f)), str(out))

    store = model.DictObjectStore()
    files = DictSupplementaryFileContainer()
    with AASXReader(str(out)) as reader:
        reader.read_into(store, files)

    shells = [o for o in store if isinstance(o, model.AssetAdministrationShell)]
    submodels = {o.id_short for o in store if isinstance(o, model.Submodel)}
    assert shells, "no AAS produced from AP242 XML"
    assert "TechnicalData" in submodels
    assert "Models3D" in submodels
    assert any(n.endswith(".stp") for n in files), "no embedded STEP geometry"
