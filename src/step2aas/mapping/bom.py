"""Mapping table A (docs/mapping-draft.md §2): IR tree → IDTA 02011 HierarchicalStructures.

semanticIds verified against IDTA 02011-1-1 (see docs/verification-log.md).
Design decision D2: one Node per occurrence by default; Entity.globalAssetId
identifies the represented asset for AAS discovery (A4). Instance transforms are
intentionally not represented in 02011 — gap G1 (PlacementScene instead).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from typing import Literal

from basyx.aas import model

from step2aas.mapping.rules import clean_mlp, ext_ref, load
from step2aas.model import PartNode

_RULES = load("bom")
_ELEM = _RULES["elements"]

BomStrategy = Literal["occurrence", "bulk"]


def _sem(key: str) -> model.ExternalReference:
    return ext_ref(_ELEM[key])


def build_bom(
    root: PartNode,
    sm_id: str,
    assembly_asset_id: str,
    asset_id_of: Callable[[PartNode], str],
    *,
    recurse: bool = True,
    linked: bool = True,
    strategy: BomStrategy = "occurrence",
    node_paths: dict[tuple[int, ...], str] | None = None,
    in_aas: Callable[[PartNode], bool] | None = None,
) -> model.Submodel:
    """A1~A6. `asset_id_of(part)` resolves a node's AAS globalAssetId.

    recurse=True  → nest the whole occurrence tree as Nodes (flat / single mode);
                    ArcheType "Full" (A6).
    recurse=False → only direct children become Nodes; each identifies its own
                    sub-assembly AAS, whose BOM lists its children (hierarchical);
                    ArcheType "OneDown" (A6).
    linked=True   → Nodes are self-managed with a globalAssetId for AAS discovery. linked=False → co-managed Nodes, no asset id
                    (single mode: no separate part/sub-assembly AAS to point at).
    strategy='occurrence' (D2 default): one Node per occurrence.
    strategy='bulk': one Node per unique ref_key under a parent, with BulkCount
                    (A5); used by scripts/evaluate.py, not by the CLI.

    A node is linked only when its target AAS actually exists: in flat mode
    (linked + recurse) nested sub-assemblies exist only as BOM nodes — no AAS —
    so those become co-managed; their leaf parts still link normally.
    node_paths (optional, filled in place): occurrence path (child indices from
    `root`) → idShort path of the Node representing that occurrence in this BOM,
    e.g. (0, 2) → "EntryNode.Node1.Node4". Occurrences without a Node in this
    submodel (deeper than one level under OneDown) are absent. Used to key the
    PlacementScene by occurrence (G1/D8).
    in_aas (lean modes, D9): a predicate naming the designs that have their own
                    AAS. It overrides `recurse`/`linked` per node: a child in the set
                    becomes a self-managed leaf Node (its own BOM continues the
                    structure); a child assembly outside it is nested inline as a
                    co-managed Node (coverage rule: each structure is written once).
                    ArcheType is "OneDown" if any child assembly continues in its own
                    BOM, otherwise "Full".
    A4: optional SameAs is omitted. IDTA 02011-1-1 Table 5 defines it between
    Entities, not from an Entity to an external asset identifier.
    """
    counter = _Counter()
    stops: list[bool] = []

    # A1: root product → EntryNode; globalAssetId = the assembly AAS's asset id.
    entry = model.Entity(
        id_short="EntryNode",
        entity_type=model.EntityType.SELF_MANAGED_ENTITY,
        global_asset_id=assembly_asset_id,
        semantic_id=_sem("EntryNode"),
    )

    has_part: list[tuple[model.Entity, model.Entity]] = []
    _emit_children(
        root,
        entry,
        has_part,
        asset_id_of,
        counter,
        recurse,
        linked,
        strategy,
        path=(),
        id_path="EntryNode",
        node_paths=node_paths,
        in_aas=in_aas,
        stops=stops,
    )
    if in_aas is not None:
        archetype = "ArcheTypeOneDown" if any(stops) else "ArcheTypeFull"
    else:
        archetype = "ArcheTypeFull" if recurse else "ArcheTypeOneDown"

    submodel = model.Submodel(
        id_=sm_id,
        id_short=_RULES["submodel"]["idShort"],
        semantic_id=ext_ref(_RULES["submodel"]["semanticId"]),
        submodel_element=[
            # A6: whole tree in one submodel → "Full"; direct children only,
            # sub-assemblies carrying their own BOM → "OneDown" (02011 ValueList).
            model.Property(
                id_short="ArcheType",
                value_type=model.datatypes.String,
                value=_RULES["constants"][archetype],
                semantic_id=_sem("ArcheType"),
            ),
            entry,
        ],
    )

    for parent_entity, child_entity in has_part:  # A3
        parent_entity.statement.add(
            model.RelationshipElement(
                id_short=f"HasPart{counter.next('hp')}",
                first=model.ModelReference.from_referable(parent_entity),
                second=model.ModelReference.from_referable(child_entity),
                semantic_id=_sem("HasPart"),
            )
        )
    return submodel


def _emit_children(
    parent_part: PartNode,
    parent_entity: model.Entity,
    has_part: list[tuple[model.Entity, model.Entity]],
    asset_id_of: Callable[[PartNode], str],
    counter: _Counter,
    recurse: bool,
    linked: bool,
    strategy: BomStrategy,
    *,
    path: tuple[int, ...] = (),
    id_path: str = "EntryNode",
    node_paths: dict[tuple[int, ...], str] | None = None,
    in_aas: Callable[[PartNode], bool] | None = None,
    stops: list[bool] | None = None,
) -> None:
    if strategy == "bulk":
        groups: dict[str, list[PartNode]] = defaultdict(list)
        order: list[str] = []
        for child in parent_part.children:
            key = child.ref_key or child.product_id or child.name
            if key not in groups:
                order.append(key)
            groups[key].append(child)
        for key in order:
            members = groups[key]
            _emit_node(
                members[0],
                parent_entity,
                has_part,
                asset_id_of,
                counter,
                recurse,
                linked,
                strategy,
                bulk_count=len(members),
            )
        return
    for i, child in enumerate(parent_part.children):
        _emit_node(
            child,
            parent_entity,
            has_part,
            asset_id_of,
            counter,
            recurse,
            linked,
            strategy,
            path=path + (i,),
            id_path=id_path,
            node_paths=node_paths,
            in_aas=in_aas,
            stops=stops,
        )


def _emit_node(
    part: PartNode,
    parent: model.Entity,
    has_part: list[tuple[model.Entity, model.Entity]],
    asset_id_of: Callable[[PartNode], str],
    counter: _Counter,
    recurse: bool,
    linked: bool,
    strategy: BomStrategy,
    *,
    bulk_count: int | None = None,
    path: tuple[int, ...] = (),
    id_path: str = "EntryNode",
    node_paths: dict[tuple[int, ...], str] | None = None,
    in_aas: Callable[[PartNode], bool] | None = None,
    stops: list[bool] | None = None,
) -> None:
    """A2: one Node per occurrence, or per unique ref_key when strategy='bulk'."""
    # Flat mode nests sub-assemblies that have no AAS of their own — those nodes
    # must be co-managed (no globalAssetId / SameAs target exists for them).
    node_linked = linked and not (recurse and part.is_assembly)
    descend = recurse
    if in_aas is not None:  # D9: per-node choice from the packaging set
        node_linked = in_aas(part)
        descend = part.is_assembly and not node_linked
        if stops is not None and part.is_assembly and node_linked:
            stops.append(True)
    part_asset_id = asset_id_of(part) if node_linked else None
    node = model.Entity(
        id_short=f"Node{counter.next('node')}",
        display_name={"en": clean_mlp(part.name)[:128]},
        entity_type=(
            model.EntityType.SELF_MANAGED_ENTITY
            if node_linked
            else model.EntityType.CO_MANAGED_ENTITY
        ),
        global_asset_id=part_asset_id,
        semantic_id=_sem("Node"),
    )
    if bulk_count is not None and bulk_count > 1:
        ulong = getattr(model.datatypes, "ULong", None) or getattr(
            model.datatypes, "NonNegativeInteger", model.datatypes.Int
        )
        node.statement.add(
            model.Property(
                id_short="BulkCount",
                value_type=ulong,
                value=bulk_count,
                semantic_id=_sem("BulkCount"),
            )
        )

    parent.statement.add(node)
    has_part.append((parent, node))  # A3 (wired later)
    node_id_path = f"{id_path}.{node.id_short}"
    if node_paths is not None and bulk_count is None:
        node_paths[path] = node_id_path
    if descend:  # nest sub-assemblies inline; hierarchical mode stops at direct children
        _emit_children(
            part,
            node,
            has_part,
            asset_id_of,
            counter,
            recurse,
            linked,
            strategy,
            path=path,
            id_path=node_id_path,
            node_paths=node_paths,
            in_aas=in_aas,
            stops=stops,
        )


class _Counter:
    """Monotonic per-key suffix source for unique idShorts."""

    def __init__(self) -> None:
        self._n: dict[str, int] = {}

    def next(self, key: str) -> int:
        self._n[key] = self._n.get(key, 0) + 1
        return self._n[key]
