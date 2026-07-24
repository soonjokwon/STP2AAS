"""Mapping table A (docs/mapping-draft.md §2): IR tree → IDTA 02011 HierarchicalStructures.

# VERIFY(A*): 02011 v1.1 semanticIds not yet checked against spec PDF.
Design decision D2: one Node per occurrence; SameAs of duplicates points to the
same part AAS (A4). Instance transforms are intentionally not represented — gap G1.
"""

from __future__ import annotations

from collections.abc import Callable

from basyx.aas import model

from stp2aas.mapping.rules import clean_mlp, ext_ref, global_asset_ref, load
from stp2aas.model import PartNode

_RULES = load("bom")
_ELEM = _RULES["elements"]


def _sem(key: str) -> model.ExternalReference:
    return ext_ref(_ELEM[key])


def build_bom(
    root: PartNode,
    sm_id: str,
    assembly_asset_id: str,
    asset_id_of: Callable[[PartNode], str],
    recurse: bool = True,
    linked: bool = True,
) -> model.Submodel:
    """A1~A6. `asset_id_of(part)` resolves a node's AAS globalAssetId.

    recurse=True  → nest the whole occurrence tree as Nodes (flat / single mode);
                    ArcheType "Full" (A6).
    recurse=False → only direct children become Nodes; each SameAs its own
                    sub-assembly AAS, whose BOM lists its children (hierarchical);
                    ArcheType "OneDown" (A6).
    linked=True   → Nodes are self-managed with a globalAssetId + SameAs to the
                    referenced AAS. linked=False → co-managed Nodes, no SameAs
                    (single mode: no separate part/sub-assembly AAS to point at).

    A node is linked only when its target AAS actually exists: in flat mode
    (linked + recurse) nested sub-assemblies exist only as BOM nodes — no AAS —
    so those become co-managed with no SameAs (a SameAs to a never-emitted asset
    would dangle); their leaf parts still link normally.
    """
    counter = _Counter()

    # A1: root product → EntryNode; globalAssetId = the assembly AAS's asset id.
    entry = model.Entity(
        id_short="EntryNode",
        entity_type=model.EntityType.SELF_MANAGED_ENTITY,
        global_asset_id=assembly_asset_id,
        semantic_id=_sem("EntryNode"),
    )

    # Build the nested Node entities first (A2), recording relationships to wire
    # up once every entity has a parent chain (from_referable needs it).
    has_part: list[tuple[model.Entity, model.Entity]] = []
    same_as: list[tuple[model.Entity, str]] = []
    for child in root.children:
        _build_node(child, entry, has_part, same_as, asset_id_of, counter, recurse, linked)

    submodel = model.Submodel(
        id_=sm_id,
        id_short=_RULES["submodel"]["idShort"],
        semantic_id=ext_ref(_RULES["submodel"]["semanticId"]),  # VERIFY
        submodel_element=[
            # A6: whole tree in one submodel → "Full"; direct children only,
            # sub-assemblies carrying their own BOM → "OneDown" (02011 ValueList).
            model.Property(
                id_short="ArcheType",
                value_type=model.datatypes.String,
                value=_RULES["constants"]["ArcheTypeFull" if recurse else "ArcheTypeOneDown"],
                semantic_id=_sem("ArcheType"),
            ),
            entry,
        ],
    )

    # Now that EntryNode is parented under the submodel, add relationships.
    for parent_entity, child_entity in has_part:  # A3
        parent_entity.statement.add(
            model.RelationshipElement(
                id_short=f"HasPart{counter.next('hp')}",
                first=model.ModelReference.from_referable(parent_entity),
                second=model.ModelReference.from_referable(child_entity),
                semantic_id=_sem("HasPart"),
            )
        )
    for node_entity, part_asset_id in same_as:  # A4
        node_entity.statement.add(
            model.RelationshipElement(
                id_short=f"SameAs{counter.next('sa')}",
                first=model.ModelReference.from_referable(node_entity),
                second=global_asset_ref(part_asset_id),
                semantic_id=_sem("SameAs"),
            )
        )
    return submodel


def _build_node(
    part: PartNode,
    parent: model.Entity,
    has_part: list[tuple[model.Entity, model.Entity]],
    same_as: list[tuple[model.Entity, str]],
    asset_id_of: Callable[[PartNode], str],
    counter: "_Counter",
    recurse: bool,
    linked: bool,
) -> None:
    """A2: one Node Entity per occurrence, nested under its parent's statements."""
    # Flat mode nests sub-assemblies that have no AAS of their own — those nodes
    # must be co-managed (no globalAssetId / SameAs target exists for them).
    node_linked = linked and not (recurse and part.is_assembly)
    part_asset_id = asset_id_of(part) if node_linked else None
    node = model.Entity(
        id_short=f"Node{counter.next('node')}",
        # Human-readable occurrence name so viewers can tell which part a Node
        # is (idShort alone is just "NodeN").
        display_name={"en": clean_mlp(part.name)[:128]},
        entity_type=(
            model.EntityType.SELF_MANAGED_ENTITY
            if node_linked
            else model.EntityType.CO_MANAGED_ENTITY
        ),
        global_asset_id=part_asset_id,
        semantic_id=_sem("Node"),
    )
    parent.statement.add(node)
    has_part.append((parent, node))  # A3 (wired later)
    if node_linked:
        same_as.append((node, part_asset_id))  # A4 (wired later)

    if recurse:  # nest sub-assemblies inline; hierarchical mode stops at direct children
        for child in part.children:
            _build_node(child, node, has_part, same_as, asset_id_of, counter, recurse, linked)


class _Counter:
    """Monotonic per-key suffix source for unique idShorts."""

    def __init__(self) -> None:
        self._n: dict[str, int] = {}

    def next(self, key: str) -> int:
        self._n[key] = self._n.get(key, 0) + 1
        return self._n[key]
