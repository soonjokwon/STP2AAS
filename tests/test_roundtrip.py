"""Roundtrip verification: convert fixture → read back with basyx → assert structure.

This is the automated half of the verification loop (CLAUDE.md). The manual half
(AASX Package Explorer) is the user's responsibility.
"""

import pathlib

import pytest
from basyx.aas import model
from basyx.aas.adapter.aasx import AASXReader, DictSupplementaryFileContainer

from stp2aas.aasx_writer import write_aasx
from stp2aas.extract.xde import extract
from stp2aas.mapping.rules import load

FIXTURES = sorted(pathlib.Path(__file__).parent.joinpath("fixtures").glob("*.st*p"))

pytestmark = pytest.mark.skipif(not FIXTURES, reason="no STEP fixtures present")

_M3 = load("models3d")
_TD = load("technical_data")
_BOM = load("bom")


def _convert_and_reload(fixture, tmp_path):
    out = tmp_path / (fixture.stem + ".aasx")
    write_aasx(extract(str(fixture)), str(out))
    store = model.DictObjectStore()
    files = DictSupplementaryFileContainer()
    with AASXReader(str(out)) as reader:  # (a) parses without error
        reader.read_into(store, files)
    return store, files


def _submodels(store):
    return [o for o in store if isinstance(o, model.Submodel)]


def _by_idshort(container, id_short):
    return [e for e in container if getattr(e, "id_short", None) == id_short]


def _sem(elem):
    return elem.semantic_id.key[0].value if elem and elem.semantic_id else None


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda p: p.name)
def test_roundtrip(fixture, tmp_path):
    store, files = _convert_and_reload(fixture, tmp_path)

    shells = [o for o in store if isinstance(o, model.AssetAdministrationShell)]
    submodels = _submodels(store)
    assert shells, "no AAS produced"

    # (b) expected submodels exist
    td = _by_idshort(submodels, "TechnicalData")
    m3 = _by_idshort(submodels, "Models3D")
    assert td, "TechnicalData submodel missing"
    assert m3, "Models3D submodel missing"

    # (c) semanticIds match mapping/*.yaml
    assert _sem(td[0]) == _TD["submodel"]["semanticId"]
    assert _sem(m3[0]) == _M3["submodel"]["semanticId"]

    # (d) required-cardinality elements present in every Models3D
    for sm in m3:
        _assert_models3d_required(sm)

    # every AAS's submodel references resolve within the package
    for shell in shells:
        for ref in shell.submodel:
            assert ref.resolve(store) is not None

    # at least one supplementary STEP + one preview PNG were embedded
    names = list(files)
    assert any(n.endswith(".stp") for n in names), "no STEP supplementary file"
    assert any(n.endswith(".png") for n in names), "no preview PNG (C11)"


def _assert_models3d_required(sm):
    model3d_list = _by_idshort(sm.submodel_element, "Model3D")
    assert model3d_list, "Models3D has no Model3D list"
    entry = list(model3d_list[0].value)[0]
    file_smc = _by_idshort(entry.value, "File")[0]
    capability = _by_idshort(entry.value, "Capability")[0]
    geometry = _by_idshort(entry.value, "Geometry")[0]

    file_version = _by_idshort(file_smc.value, "FileVersion")[0]
    # C11 PreviewFile[1], C1 DigitalFile
    assert _by_idshort(file_version.value, "PreviewFile"), "PreviewFile[1] missing (C11)"
    assert _by_idshort(file_version.value, "DigitalFile"), "DigitalFile missing (C1)"
    # C12 FileClassification[1]
    assert _by_idshort(file_smc.value, "FileClassification"), "FileClassification[1] missing (C12)"
    # C19 Origin, C20 PosModelPurpose required
    assert _by_idshort(capability.value, "Origin"), "Origin missing (C19)"
    assert _by_idshort(capability.value, "PosModelPurpose"), "PosModelPurpose missing (C20)"
    # C22 Representation "SolidBody", C23 LengthUnit
    rep = _by_idshort(geometry.value, "Representation")
    assert rep and rep[0].value == _M3["constants"]["Representation"]
    assert _by_idshort(geometry.value, "LengthUnit"), "LengthUnit missing (C23)"


def test_bom_structure_for_assembly(tmp_path):
    """M2: assembly fixtures produce a valid 02011 BOM (EntryNode, Nodes, HasPart, SameAs)."""
    assembly_fixtures = [f for f in FIXTURES if "assembly" in f.name.lower()]
    if not assembly_fixtures:
        pytest.skip("no assembly fixture present")

    store, _ = _convert_and_reload(assembly_fixtures[0], tmp_path)
    submodels = _submodels(store)
    bom = _by_idshort(submodels, "HierarchicalStructures")
    assert bom, "assembly produced no HierarchicalStructures submodel"
    bom = bom[0]
    assert _sem(bom) == _BOM["submodel"]["semanticId"]

    entry = _by_idshort(bom.submodel_element, "EntryNode")
    assert entry, "no EntryNode (A1)"
    entry = entry[0]

    nodes = [s for s in entry.statement if isinstance(s, model.Entity)]
    has_part = [
        s
        for s in entry.statement
        if isinstance(s, model.RelationshipElement) and s.id_short.startswith("HasPart")
    ]
    assert nodes, "no Node entities (A2)"
    assert len(has_part) == len(nodes), "HasPart count must match Node count (A3)"

    # A4: each Node has a SameAs whose target asset id belongs to a real AAS.
    asset_ids = {
        s.asset_information.global_asset_id
        for s in store
        if isinstance(s, model.AssetAdministrationShell)
    }
    for node in nodes:
        same_as = [
            s
            for s in node.statement
            if isinstance(s, model.RelationshipElement) and s.id_short.startswith("SameAs")
        ]
        assert same_as, "Node missing SameAs (A4)"
        target = same_as[0].second.key[-1].value
        assert target in asset_ids, f"SameAs target {target} has no AAS"


# --- assembly composition modes (§0: hierarchical / flat / single) -----------


def _shells(store):
    return [o for o in store if isinstance(o, model.AssetAdministrationShell)]


def _boms(store):
    return _by_idshort(_submodels(store), "HierarchicalStructures")


def _archetype(bom):
    return next(e.value for e in bom.submodel_element if e.id_short == "ArcheType")


def _entities(bom):
    """All Node/EntryNode entities in a BOM, nested included."""
    out = []

    def walk(elems):
        for e in elems:
            if isinstance(e, model.Entity):
                out.append(e)
                walk(e.statement)

    walk(bom.submodel_element)
    return out


def _same_as_targets(entity):
    return [
        s.second.key[-1].value
        for s in entity.statement
        if isinstance(s, model.RelationshipElement) and s.id_short.startswith("SameAs")
    ]


def test_assembly_structure_modes(tmp_path):
    """The three compositions are internally consistent and can coexist:
    every SameAs resolves, ArcheType matches the nesting (A6), the root asset
    is the same across modes, and the root AAS ids differ per mode."""
    assembly_fixtures = [f for f in FIXTURES if "assembly" in f.name.lower()]
    if not assembly_fixtures:
        pytest.skip("no assembly fixture present")
    doc = extract(str(assembly_fixtures[0]))

    stores = {}
    for mode in ("hierarchical", "flat", "single"):
        out = tmp_path / f"{mode}.aasx"
        write_aasx(doc, str(out), assembly_structure=mode)
        store = model.DictObjectStore()
        files = DictSupplementaryFileContainer()
        with AASXReader(str(out)) as reader:
            reader.read_into(store, files)
        stores[mode] = store

    # hierarchical: one AAS per unique part AND assembly; every BOM is OneDown
    # and every SameAs target has an emitted AAS.
    h = stores["hierarchical"]
    h_assets = {s.asset_information.global_asset_id for s in _shells(h)}
    assert _boms(h), "hierarchical produced no BOM"
    for bom in _boms(h):
        assert _archetype(bom) == "OneDown", "hierarchical BOM must be OneDown (A6)"
        for ent in _entities(bom):
            for target in _same_as_targets(ent):
                assert target in h_assets, f"dangling SameAs {target}"

    # flat: single Full BOM; linked nodes resolve, nested sub-assembly nodes
    # (no AAS of their own) are co-managed without SameAs.
    f = stores["flat"]
    f_assets = {s.asset_information.global_asset_id for s in _shells(f)}
    (fbom,) = _boms(f)
    assert _archetype(fbom) == "Full"
    for ent in _entities(fbom):
        targets = _same_as_targets(ent)
        if ent.entity_type == model.EntityType.CO_MANAGED_ENTITY:
            assert not targets, "co-managed Node must not carry SameAs"
            assert not ent.global_asset_id
        for target in targets:
            assert target in f_assets, f"dangling SameAs {target}"

    # single: exactly one AAS; Full BOM; all Nodes co-managed.
    s = stores["single"]
    assert len(_shells(s)) == 1, "single mode must emit exactly one AAS"
    (sbom,) = _boms(s)
    assert _archetype(sbom) == "Full"
    for ent in _entities(sbom):
        if ent.id_short != "EntryNode":
            assert ent.entity_type == model.EntityType.CO_MANAGED_ENTITY

    # Cross-mode identity: same physical asset → same root globalAssetId; the
    # shells differ in content → distinct AAS ids (coexist in one repository).
    def root_shell(store):
        # root = has a BOM and is not SameAs-referenced by any other BOM
        all_targets = set()
        for bom in _boms(store):
            for ent in _entities(bom):
                all_targets.update(_same_as_targets(ent))
        bom_ids = {b.id for b in _boms(store)}
        candidates = [
            sh
            for sh in _shells(store)
            if any(ref.key[0].value in bom_ids for ref in sh.submodel)
            and sh.asset_information.global_asset_id not in all_targets
        ]
        assert len(candidates) == 1, f"expected one root shell, got {len(candidates)}"
        return candidates[0]

    roots = {m: root_shell(stores[m]) for m in stores}
    asset_ids = {r.asset_information.global_asset_id for r in roots.values()}
    assert len(asset_ids) == 1, "root asset id must be identical across modes"
    aas_ids = {r.id for r in roots.values()}
    assert len(aas_ids) == 3, "root AAS ids must be distinct across modes"
