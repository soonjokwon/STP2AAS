"""BOM mapping without geometry / pythonocc."""

from __future__ import annotations

from basyx.aas import model

from step2aas.mapping.bom import build_bom
from step2aas.mapping.rules import load
from step2aas.model import PartNode


def _tree() -> PartNode:
    # Assembly
    #   leaf A (ref a) × 2
    #   sub-assembly S
    #     leaf B (ref b)
    a1 = PartNode(name="A1", product_id="a", ref_key="a")
    a2 = PartNode(name="A2", product_id="a", ref_key="a")
    b = PartNode(name="B", product_id="b", ref_key="b")
    sub = PartNode(name="Sub", product_id="s", ref_key="s", children=[b])
    return PartNode(name="Asm", product_id="asm", ref_key="asm", children=[a1, a2, sub])


def _asset(part: PartNode) -> str:
    return f"urn:asset:{part.ref_key}"


def _walk_entities(entity: model.Entity) -> list[model.Entity]:
    out = [entity]
    for stmt in entity.statement:
        if isinstance(stmt, model.Entity):
            out.extend(_walk_entities(stmt))
    return out


def test_yaml_semantic_ids_match_verified_specs():
    bom = load("bom")
    assert bom["submodel"]["semanticId"].endswith("/HierarchicalStructures/1/1/Submodel")
    td = load("technical_data")
    assert td["submodel"]["semanticId"].endswith("/TechnicalData/Submodel/1/2")
    np = load("nameplate")
    assert np["submodel"]["semanticId"].endswith("/nameplate/2/0/Nameplate")


def test_asset_links_only_on_leaf_self_managed_nodes():
    """Flat-mode BOM (recurse=True): nested assemblies are co-managed, leaves identify assets."""
    root = _tree()
    sm = build_bom(root, "urn:sm:bom", "urn:asset:asm", _asset, recurse=True, linked=True)
    entry = next(e for e in sm.submodel_element if isinstance(e, model.Entity))
    nodes = [e for e in _walk_entities(entry) if e.id_short != "EntryNode"]

    co = [n for n in nodes if n.entity_type == model.EntityType.CO_MANAGED_ENTITY]
    self_m = [n for n in nodes if n.entity_type == model.EntityType.SELF_MANAGED_ENTITY]
    # One co-managed sub-assembly; three leaf occurrences (A, A, B)
    assert len(co) == 1
    assert len(self_m) == 3
    assert all(n.global_asset_id is None for n in co)

    asset_targets = [n.global_asset_id for n in nodes if n.global_asset_id]
    assert not any(
        isinstance(stmt, model.RelationshipElement) and stmt.id_short.startswith("SameAs")
        for n in nodes for stmt in n.statement
    )
    assert sorted(asset_targets) == ["urn:asset:a", "urn:asset:a", "urn:asset:b"]
    assert "urn:asset:s" not in asset_targets


def test_hierarchical_onedown_links_subassemblies():
    """Hierarchical BOM (recurse=False): direct children only, including sub-assembly asset IDs."""
    root = _tree()
    sm = build_bom(root, "urn:sm:bom", "urn:asset:asm", _asset, recurse=False, linked=True)
    arche = next(e for e in sm.submodel_element if isinstance(e, model.Property))
    assert arche.value == "OneDown"
    entry = next(e for e in sm.submodel_element if isinstance(e, model.Entity))
    nodes = [e for e in _walk_entities(entry) if e.id_short != "EntryNode"]
    assert len(nodes) == 3  # A1, A2, Sub — no nested B
    assert all(n.entity_type == model.EntityType.SELF_MANAGED_ENTITY for n in nodes)
    asset_targets = [n.global_asset_id for n in nodes if n.global_asset_id]
    assert not any(
        isinstance(stmt, model.RelationshipElement) and stmt.id_short.startswith("SameAs")
        for n in nodes for stmt in n.statement
    )
    assert sorted(asset_targets) == ["urn:asset:a", "urn:asset:a", "urn:asset:s"]


def test_bulk_strategy_emits_bulkcount_and_fewer_nodes():
    root = _tree()
    occ = build_bom(root, "urn:sm:bom", "urn:asset:asm", _asset, strategy="occurrence")
    bulk = build_bom(root, "urn:sm:bom-b", "urn:asset:asm", _asset, strategy="bulk")

    def n_nodes(sm: model.Submodel) -> int:
        entry = next(e for e in sm.submodel_element if isinstance(e, model.Entity))
        return len(_walk_entities(entry)) - 1  # minus EntryNode

    assert n_nodes(bulk) < n_nodes(occ)

    entry = next(e for e in bulk.submodel_element if isinstance(e, model.Entity))
    bulk_props = [
        s
        for n in _walk_entities(entry)
        for s in n.statement
        if isinstance(s, model.Property) and s.id_short == "BulkCount"
    ]
    assert bulk_props
    assert int(bulk_props[0].value) == 2
