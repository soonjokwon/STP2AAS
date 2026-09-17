"""Roundtrip: IR → .aasx → basyx re-parse. No pythonocc required (geometry-less nodes)."""

from __future__ import annotations

from basyx.aas import model
from basyx.aas.adapter.aasx import AASXReader, DictSupplementaryFileContainer

from step2aas.aasx_writer import write_aasx
from step2aas.compat import ObjectStore
from step2aas.mapping.rules import load
from step2aas.model import P21Header, PartNode, PhysicalProps, Provenance, StepDocument


def _doc(tmp_path, *, assembly: bool) -> StepDocument:
    src = tmp_path / "model.stp"
    src.write_text(
        "ISO-10303-21;\nHEADER;\nFILE_NAME('m.stp','2024-06-01T00:00:00',"
        "('A'),('Acme'),'pre','NX',' ');\n"
        "FILE_SCHEMA(('CONFIG_CONTROL_DESIGN'));\nENDSEC;\nDATA;\nENDSEC;\n"
        "END-ISO-10303-21;\n",
        encoding="ascii",
    )
    header = P21Header(
        schema="AP203",
        organization="Acme",
        originating_system="NX",
        timestamp="2024-06-01T00:00:00",
    )
    cube = PartNode(
        name="Cube",
        product_id="cube",
        ref_key="cube",
        props=PhysicalProps(
            volume_mm3=8000.0,
            surface_area_mm2=2400.0,
            centroid_mm=(10.0, 10.0, 10.0),
            provenance=Provenance(source="computed"),
        ),
        bbox_mm=(0, 0, 0, 20, 20, 20),
    )
    if not assembly:
        return StepDocument(root=cube, header=header, source_path=str(src), file_hash="abc123hash")
    pin = PartNode(name="Pin", product_id="pin", ref_key="pin")
    sub = PartNode(name="SubAsm", product_id="sub", ref_key="sub", children=[pin])
    cube2 = PartNode(name="CubeOcc2", product_id="cube", ref_key="cube")
    root = PartNode(
        name="Widget",
        product_id="widget",
        ref_key="widget",
        children=[cube, cube2, sub],
    )
    return StepDocument(root=root, header=header, source_path=str(src), file_hash="abc123hash")


def _reload(path: str):
    store = ObjectStore()
    files = DictSupplementaryFileContainer()
    with AASXReader(path) as reader:
        reader.read_into(store, files)
    return store, files


def _shells(store):
    return [o for o in store if isinstance(o, model.AssetAdministrationShell)]


def _submodels(store):
    return [o for o in store if isinstance(o, model.Submodel)]


def test_roundtrip_single_part(tmp_path):
    doc = _doc(tmp_path, assembly=False)
    out = tmp_path / "part.aasx"
    write_aasx(doc, str(out))
    store, _files = _reload(str(out))
    assert len(_shells(store)) == 1
    ids = {sm.id_short for sm in _submodels(store)}
    assert "TechnicalData" in ids
    assert "Models3D" in ids
    assert "Nameplate" not in ids
    td = load("technical_data")
    m3 = load("models3d")
    for sm in _submodels(store):
        iri = sm.semantic_id.key[0].value
        if sm.id_short == "TechnicalData":
            assert iri == td["submodel"]["semanticId"]
        if sm.id_short == "Models3D":
            assert iri == m3["submodel"]["semanticId"]


def _bom_stats(store):
    asset_ids = {s.asset_information.global_asset_id for s in _shells(store)}
    linked_assets = []
    co_managed = 0
    for sm in _submodels(store):
        for elem in sm.submodel_element:
            if not isinstance(elem, model.Entity):
                continue
            stack = [elem]
            while stack:
                ent = stack.pop()
                if ent.id_short != "EntryNode" and ent.global_asset_id:
                    linked_assets.append(ent.global_asset_id)
                if ent.entity_type == model.EntityType.CO_MANAGED_ENTITY:
                    co_managed += 1
                for stmt in ent.statement:
                    if isinstance(stmt, model.Entity):
                        stack.append(stmt)

    return asset_ids, linked_assets, co_managed


def test_roundtrip_assembly_hierarchical(tmp_path):
    doc = _doc(tmp_path, assembly=True)
    out = tmp_path / "asm.aasx"
    write_aasx(doc, str(out))  # default hierarchical
    store, _files = _reload(str(out))
    shells = _shells(store)
    # unique leaves (cube, pin) + unique assemblies (Widget, SubAsm)
    assert len(shells) == 4
    boms = [sm for sm in _submodels(store) if sm.id_short == "HierarchicalStructures"]
    assert boms
    assert all(sm.semantic_id.key[0].value == load("bom")["submodel"]["semanticId"] for sm in boms)
    arche = [
        e.value
        for sm in boms
        for e in sm.submodel_element
        if isinstance(e, model.Property) and e.id_short == "ArcheType"
    ]
    assert arche and all(v == "OneDown" for v in arche)
    asset_ids, linked_assets, co_managed = _bom_stats(store)
    assert co_managed == 0
    assert linked_assets
    assert all(t in asset_ids for t in linked_assets)


def test_roundtrip_assembly_flat(tmp_path):
    doc = _doc(tmp_path, assembly=True)
    out = tmp_path / "asm-flat.aasx"
    write_aasx(doc, str(out), assembly_structure="flat")
    store, _files = _reload(str(out))
    shells = _shells(store)
    # 2 unique leaves (cube, pin) + 1 assembly AAS
    assert len(shells) == 3
    asset_ids, linked_assets, co_managed = _bom_stats(store)
    # Sub-assembly is co-managed; globalAssetId targets must exist as AAS assets
    assert co_managed == 1
    assert linked_assets
    assert all(t in asset_ids for t in linked_assets)


def test_file_version_id_follows_hash(tmp_path):
    doc = _doc(tmp_path, assembly=False)
    out = tmp_path / "part.aasx"
    write_aasx(doc, str(out))
    store, _ = _reload(str(out))
    m3 = next(sm for sm in _submodels(store) if sm.id_short == "Models3D")
    found = []

    def walk(obj):
        if isinstance(obj, model.Property) and obj.id_short == "FileVersionId":
            found.append(obj.value)
        kids = getattr(obj, "value", None) or getattr(obj, "submodel_element", None) or []
        try:
            for k in kids:
                walk(k)
        except TypeError:
            pass

    walk(m3)
    assert found
    assert all(v == "abc123hash"[:12] for v in found)


def test_nameplate_omitted_unless_requested(tmp_path):
    doc = _doc(tmp_path, assembly=False)
    out = tmp_path / "np.aasx"
    write_aasx(doc, str(out), include_partial_nameplate=True)
    store, _ = _reload(str(out))
    assert any(sm.id_short == "Nameplate" for sm in _submodels(store))


def _identity(store):
    return {
        "shells": sorted(s.id for s in _shells(store)),
        "assets": sorted(s.asset_information.global_asset_id for s in _shells(store)),
        "sms": sorted(sm.id for sm in _submodels(store)),
    }


def test_d5_reconversion_idempotent(tmp_path):
    """Same IR twice → identical shell, asset, and submodel identifiers (D5)."""
    doc = _doc(tmp_path, assembly=True)
    a, b = tmp_path / "a.aasx", tmp_path / "b.aasx"
    write_aasx(doc, str(a))
    write_aasx(doc, str(b))
    assert _identity(_reload(str(a))[0]) == _identity(_reload(str(b))[0])


def test_d5_ids_follow_hash_not_property_values(tmp_path):
    """File-hash change forks every id; editing a property with the same hash/ref_key does not."""
    doc = _doc(tmp_path, assembly=True)
    write_aasx(doc, str(tmp_path / "base.aasx"))
    base = _identity(_reload(str(tmp_path / "base.aasx"))[0])

    doc.file_hash = "otherhash000"
    write_aasx(doc, str(tmp_path / "rehash.aasx"))
    rehash = _identity(_reload(str(tmp_path / "rehash.aasx"))[0])
    assert set(base["assets"]).isdisjoint(rehash["assets"])

    doc.file_hash = "abc123hash"
    cube = next(c for c in doc.root.children if c.ref_key == "cube")
    cube.props.volume_mm3 = 9000.0
    write_aasx(doc, str(tmp_path / "edited.aasx"))
    edited = _identity(_reload(str(tmp_path / "edited.aasx"))[0])
    assert edited == base


def _boms(store):
    sid = load("bom")["submodel"]["semanticId"]
    return [
        sm
        for sm in _submodels(store)
        if sm.semantic_id and any(k.value == sid for k in sm.semantic_id.key)
    ]


def _arche_types(sm):
    return {
        e.value
        for e in sm.submodel_element
        if isinstance(e, model.Property) and e.id_short == "ArcheType"
    }


def _root_asset(store):
    """The root EntryNode asset: the one no globalAssetId in the package points at."""
    boms = _boms(store)
    _assets, linked_assets, _co = _bom_stats(store)
    roots = [
        e.global_asset_id
        for sm in boms
        for e in sm.submodel_element
        if isinstance(e, model.Entity) and e.id_short == "EntryNode"
        if e.global_asset_id not in set(linked_assets)
    ]
    assert len(roots) == 1, f"expected exactly one root EntryNode, got {len(roots)}"
    return roots[0]


def test_composition_modes_hold_section4_invariants(tmp_path):
    """One model in all three modes → the §4 invariants.

    Per mode: ArcheType matches the mode, no globalAssetId dangles, co-managed nodes carry no
    asset id. Across modes: the root asset id is shared (same physical asset), root shell
    ids are mode-specific (packages coexist in one repository), and part AAS are shared
    verbatim between hierarchical and flat (D2).
    """
    doc = _doc(tmp_path, assembly=True)
    expected_arche = {"hierarchical": "OneDown", "flat": "Full", "single": "Full"}
    root_asset: dict[str, str] = {}
    root_shell: dict[str, str] = {}
    shell_ids: dict[str, set[str]] = {}

    for mode in ("hierarchical", "flat", "single"):
        out = tmp_path / f"modes-{mode}.aasx"
        write_aasx(doc, str(out), assembly_structure=mode)
        store, _files = _reload(str(out))

        shells = _shells(store)
        assert shells, f"{mode}: no shells emitted"
        shell_ids[mode] = {s.id for s in shells}

        boms = _boms(store)
        assert boms, f"{mode}: no 02011 BOM submodel"
        for sm in boms:
            assert _arche_types(sm) == {expected_arche[mode]}, f"{mode}: wrong ArcheType"

        asset_ids, linked_assets, _co = _bom_stats(store)
        # linkage honesty: a globalAssetId is emitted only when its target AAS exists here
        assert all(t in asset_ids for t in linked_assets), f"{mode}: dangling globalAssetId"
        if mode == "single":
            assert len(shells) == 1
            assert not linked_assets, "single mode is fully co-managed"

        root_asset[mode] = _root_asset(store)
        root_shell[mode] = next(
            s.id for s in shells if s.asset_information.global_asset_id == root_asset[mode]
        )

    # same physical asset in every mode
    assert len(set(root_asset.values())) == 1
    # ... but mode-specific shells, so the packages can coexist in one repository
    assert len(set(root_shell.values())) == 3
    # part AAS are mode-independent and shared between hierarchical and flat (D2)
    flat_parts = shell_ids["flat"] - {root_shell["flat"]}
    assert flat_parts and flat_parts <= shell_ids["hierarchical"]
