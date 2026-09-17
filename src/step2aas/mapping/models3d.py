"""Mapping table C (docs/mapping-draft.md §4): IR → IDTA 02026 Models3D submodel.

semanticIds verified against IDTA 02026-1-0 spec (see docs/verification-log.md).
One Model3D entry per part: a source or re-exported STEP (C1~C12, C18~C24). Derived
lightweight geometry (a second Model3D entry) is future work (§6a).

Structure:  [SM] Models3D → [SML] Model3D → [SMC]{ File, Capability, Geometry }
"""

from __future__ import annotations

import datetime
import logging
from collections.abc import Collection

from basyx.aas import model

from step2aas.mapping.rules import clean_mlp, ext_ref, load, provenance_qualifier
from step2aas.model import P21Header, PartNode

_RULES = load("models3d")
_SEM = _RULES["semantics"]
_CONST = _RULES["constants"]
logger = logging.getLogger(__name__)


def _sem(key: str) -> model.ExternalReference:
    return ext_ref(_SEM[key])


def _model3d_entry(
    part: PartNode,
    step_file_name: str,
    preview_file_name: str,
    header: P21Header,
    file_version_id: str | None = None,
    *,
    pmi_included: bool = False,
) -> model.SubmodelElementCollection:
    """One Model3D entry; optional Geometry requires a known representation (C22)."""
    elements = [
        _file_smc(part, step_file_name, preview_file_name, header, file_version_id),
        _capability_smc(part, pmi_included=bool(step_file_name) and pmi_included),
    ]
    geometry = _geometry_smc(part)
    if geometry is not None:
        elements.append(geometry)
    return model.SubmodelElementCollection(
        id_short=None,  # AASd-120: list elements have no idShort
        semantic_id=_sem("Model3D"),  # AASd-114: matches semantic_id_list_element
        value=elements,
    )


def _models3d_submodel(entries: list, sm_id: str) -> model.Submodel:
    model3d_list = model.SubmodelElementList(
        id_short="Model3D",
        type_value_list_element=model.SubmodelElementCollection,
        semantic_id=_sem("Model3D"),
        semantic_id_list_element=_sem("Model3D"),
        value=entries,
    )
    return model.Submodel(
        id_=sm_id,
        id_short=_RULES["submodel"]["idShort"],
        semantic_id=ext_ref(_RULES["submodel"]["semanticId"]),
        submodel_element=[model3d_list],
    )


def build_models3d(
    part: PartNode,
    step_file_name: str,
    preview_file_name: str,
    sm_id: str,
    header: P21Header,
    *,
    file_version_id: str | None = None,
    pmi_included: bool = False,
) -> model.Submodel:
    """C1~C24. `step_file_name`/`preview_file_name` are the AASX supplementary
    part names already registered in the file store (values of the File elements).
    `pmi_included` needs evidence for the referenced DigitalFile; the source
    PartNode.has_pmi flag alone does not establish retention after re-export.

    C25 CartRefSystem is not emitted: instance 4×4 cannot fit the 02026
    offset+normal pair (G1). Assembly placement is the PlacementScene file.
    """
    return _models3d_submodel(
        [
            _model3d_entry(
                part, step_file_name, preview_file_name, header, file_version_id,
                pmi_included=pmi_included,
            )
        ],
        sm_id,
    )


def build_models3d_multi(
    entries_data: list,
    sm_id: str,
    header: P21Header,
    *,
    file_version_id: str | None = None,
    pmi_included_files: Collection[str] = (),
) -> model.Submodel:
    """One Models3D submodel holding many Model3D entries (single-AAS mode).

    entries_data: list of (part, step_file_name, preview_file_name).
    pmi_included_files: DigitalFile references with explicit retention evidence.
    """
    entries = [
        _model3d_entry(
            p, stp, png, header, file_version_id, pmi_included=stp in pmi_included_files
        )
        for (p, stp, png) in entries_data
    ]
    return _models3d_submodel(entries, sm_id)


# --- [SMC] File (C1~C12) ----------------------------------------------------


def _file_smc(
    part: PartNode,
    step_file_name: str,
    preview_file_name: str,
    header: P21Header,
    file_version_id: str | None,
) -> model.SubmodelElementCollection:
    return model.SubmodelElementCollection(
        id_short="File",
        semantic_id=_sem("File"),
        value=[
            _file_id(part),  # C2
            _file_classification(),  # C12
            _file_version(part, step_file_name, preview_file_name, header, file_version_id),
        ],
    )


def _file_id(part: PartNode) -> model.SubmodelElementCollection:
    # C2: DomainId="STEP", ValueId=product.id, IsPrimary=true
    return model.SubmodelElementCollection(
        id_short="FileId",
        semantic_id=_sem("FileId"),
        value=[
            _str("FileDomainId", "STEP", _sem("FileDomainId")),
            _str("ValueId", part.product_id, _sem("ValueId")),
            model.Property(
                id_short="IsPrimary",
                value_type=model.datatypes.Boolean,
                value=True,
                semantic_id=_sem("IsPrimary"),
            ),
        ],
    )


def _file_classification() -> model.SubmodelElementCollection:
    # C12 [1] — gap G4, self-defined classification
    fc = _CONST["FileClassification"]
    return model.SubmodelElementCollection(
        id_short="FileClassification",
        semantic_id=_sem("FileClassification"),
        value=[
            _str("ClassId", fc["ClassId"], _sem("ClassId"), source="fallback"),
            _str("ClassName", fc["ClassName"], _sem("ClassName"), source="fallback"),
            _str(
                "ClassificationSystem",
                fc["ClassificationSystem"],
                _sem("ClassificationSystem"),
                source="fallback",
            ),
        ],
    )


def _file_version(
    part: PartNode,
    step_file_name: str,
    preview_file_name: str,
    header: P21Header,
    file_version_id: str | None,
) -> model.SubmodelElementCollection:
    org = header.organization or _RULES["fallbacks"]["organization"]
    org_src = "p21-header" if header.organization else "fallback"
    version = file_version_id or _CONST["FileVersionId"]
    elements: list[model.SubmodelElement] = [
        # C1: source or re-exported STEP supplementary File (value None if geometry
        # was unavailable — basyx PathType forbids an empty string).
        model.File(
            id_short="DigitalFile",
            content_type=_CONST["StepMimeType"],
            value=step_file_name or None,
            semantic_id=_sem("DigitalFile"),
        ),
        # C3: product.name @en
        model.MultiLanguageProperty(
            id_short="Title", value={"en": clean_mlp(part.name)}, semantic_id=_sem("Title")
        ),
        # C4: safe file name
        _str(
            "FileName", _basename(step_file_name) if step_file_name else "unknown", _sem("FileName")
        ),
        # C5: version id (policy D5 — input hash prefix, stable across re-conversion)
        _str("FileVersionId", version, _sem("FileVersionId")),
        # C6: ValueList "Released"
        _str("StatusValue", _CONST["StatusValue"], _sem("StatusValue"), source="fallback"),
    ]

    set_date = _parse_date(header.timestamp)  # C7
    if set_date is not None:
        elements.append(
            model.Property(
                id_short="SetDate",
                value_type=model.datatypes.Date,
                value=set_date,
                semantic_id=_sem("SetDate"),
            )
        )

    elements.append(_file_format(header))  # C8
    elements.append(_source_application(header))  # C9
    elements.append(_providing_organization(org, org_src))  # C10
    # C11: PreviewFile [1] required
    elements.append(
        model.File(
            id_short="PreviewFile",
            content_type=_CONST["PreviewMimeType"],
            value=preview_file_name,
            semantic_id=_sem("PreviewFile"),
        )
    )
    return model.SubmodelElementCollection(
        id_short="FileVersion", semantic_id=_sem("FileVersion"), value=elements
    )


def _file_format(header: P21Header) -> model.SubmodelElementCollection:
    # C8: FormatName="STEP", FormatVersion=AP (e.g. "AP242"), FormatQualifier
    ap = header.schema or "STEP"
    return model.SubmodelElementCollection(
        id_short="FileFormat",
        semantic_id=_sem("FileFormat"),
        value=[
            _str("FormatName", "STEP", _sem("FormatName")),
            _str("FormatVersion", ap, _sem("FormatVersion")),
            _str("FormatQualifier", f"STEP-{ap}", _sem("FormatQualifier")),
        ],
    )


def _source_application(header: P21Header) -> model.SubmodelElementCollection:
    # C9: VendorOrganization[1] required → originating_system reused, else fallback
    src = "p21-header" if header.originating_system else "fallback"
    vendor = header.originating_system or _RULES["fallbacks"]["organization"]
    return model.SubmodelElementCollection(
        id_short="SourceApplication",
        semantic_id=_sem("SourceApplication"),
        value=[
            _str(
                "ApplicationName",
                header.originating_system or "unknown",
                _sem("ApplicationName"),
                source=src,
            ),
            _str("ApplicationVersion", "", _sem("ApplicationVersion"), source="fallback"),
            _str("VendorOrganization", vendor, _sem("VendorOrganization"), source=src),
        ],
    )


def _providing_organization(org: str, source: str) -> model.SubmodelElementCollection:
    # C10: header organization
    return model.SubmodelElementCollection(
        id_short="ProvidingOrganization",
        semantic_id=_sem("ProvidingOrganization"),
        value=[_str("OrganizationName", org, _sem("OrganizationName"), source=source)],
    )


# --- [SMC] Capability (C18~C21) ---------------------------------------------


def _capability_smc(part: PartNode, *, pmi_included: bool) -> model.SubmodelElementCollection:
    object_type = (
        _CONST["ObjectType"]["assembly"] if part.is_assembly else _CONST["ObjectType"]["component"]
    )  # C18
    elements: list[model.SubmodelElement] = [
        _str("ObjectType", object_type, _sem("ObjectType")),
        _str("Origin", _CONST["Origin"], _sem("Origin"), source="fallback"),  # C19, required
        # C20 [1] required — gap G5 (ValueList lacks "authoritative geometry")
        _string_list(
            "PosModelPurpose",
            [_CONST["PosModelPurpose"]],
            _sem("PosModelPurpose"),
            source="fallback",
        ),
    ]
    if pmi_included:  # C21 describes the DigitalFile, not upstream DimTol detection.
        elements.append(
            _string_list("EmbeddedInfo", [_CONST["EmbeddedInfoPMI"]], _sem("EmbeddedInfo"))
        )
    return model.SubmodelElementCollection(
        id_short="Capability", semantic_id=_sem("Capability"), value=elements
    )


# --- [SMC] Geometry (C22~C24) -----------------------------------------------


def _shape_representation(shape) -> str | None:
    """C22: classify top-level primitives, not the boundary edges of a solid.

    A mixed compound has no single value in IDTA 02026-1-0 Table 107. Surface
    triangulation used for rendering does not change an analytic face to Mesh.
    """
    from OCC.Core.BRep import BRep_Tool
    from OCC.Core.TopAbs import (
        TopAbs_COMPOUND,
        TopAbs_COMPSOLID,
        TopAbs_EDGE,
        TopAbs_FACE,
        TopAbs_SHELL,
        TopAbs_SOLID,
        TopAbs_VERTEX,
        TopAbs_WIRE,
    )
    from OCC.Core.TopLoc import TopLoc_Location
    from OCC.Core.TopoDS import TopoDS_Iterator, topods

    if shape.IsNull():
        return None
    kind = shape.ShapeType()
    if kind == TopAbs_EDGE:
        return "wire"
    if kind == TopAbs_VERTEX:
        return "point"
    if kind == TopAbs_FACE:
        face = topods.Face(shape)
        if BRep_Tool.Surface(face) is not None:
            return "surface"
        triangulation = BRep_Tool.Triangulation(face, TopLoc_Location())
        return "mesh" if triangulation is not None and triangulation.NbTriangles() else None
    if kind in (TopAbs_COMPOUND, TopAbs_COMPSOLID, TopAbs_SHELL, TopAbs_SOLID, TopAbs_WIRE):
        members = TopoDS_Iterator(shape)
        kinds = set()
        while members.More():
            kinds.add(_shape_representation(members.Value()))
            members.Next()
        representation = next(iter(kinds)) if len(kinds) == 1 else None
        if kind == TopAbs_SOLID:
            # An empty solid is non-null; a triangulation-only solid remains Mesh.
            return (
                "solid"
                if representation == "surface"
                else ("mesh" if representation == "mesh" else None)
            )
        return representation
    return None


def _part_representation(part: PartNode) -> str | None:
    """C22: XML assemblies may have only child shapes, without a root compound."""
    if part.shape_ref is not None:
        return _shape_representation(part.shape_ref)
    kinds = {_part_representation(child) for child in part.children}
    return next(iter(kinds)) if len(kinds) == 1 else None


def _geometry_smc(part: PartNode) -> model.SubmodelElementCollection | None:
    # C22: Geometry is optional (02026-1-0 Table 3); Representation is required
    # only inside Geometry (Table 73). No standard unknown/mixed value exists.
    representation = _part_representation(part)
    if representation is None:
        logger.warning("C22: omitting Geometry for %r: missing or mixed representation", part.name)
        return None
    elements: list[model.SubmodelElement] = [
        _str(
            "Representation",
            _CONST["RepresentationKinds"][representation],
            _sem("Representation"),
            source="computed",
        ),
        _str("LengthUnit", _CONST["LengthUnit"], _sem("LengthUnit")),  # C23, required
    ]
    if part.bbox_mm is not None:
        elements.append(_cart_bounding_box(part.bbox_mm))  # C24
    return model.SubmodelElementCollection(
        id_short="Geometry", semantic_id=_sem("Geometry"), value=elements
    )


def _cart_bounding_box(bbox: tuple[float, ...]) -> model.SubmodelElementCollection:
    # C24: Kind="MinEnvelope", AABB as (xmin,ymin,zmin,xmax,ymax,zmax)
    return model.SubmodelElementCollection(
        id_short="CartBoundingBox",
        semantic_id=_sem("CartBoundingBox"),
        value=[
            _str("BoundingBoxKind", _CONST["BoundingBoxKind"], _sem("BoundingBoxKind")),
            _double_list(
                "CartBoundingVector", [float(v) for v in bbox], _sem("CartBoundingVector")
            ),
        ],
    )


# --- small builders ---------------------------------------------------------


def _str(
    id_short: str | None,
    value: str,
    semantic: model.ExternalReference,
    *,
    source: str | None = None,
) -> model.Property:
    kwargs: dict = {}
    if source:
        kwargs["qualifier"] = [provenance_qualifier(source)]
    return model.Property(
        id_short=id_short,
        value_type=model.datatypes.String,
        value=value,
        semantic_id=semantic,
        **kwargs,
    )


def _string_list(
    id_short: str,
    values: list[str],
    semantic: model.ExternalReference,
    *,
    source: str | None = None,
) -> model.SubmodelElementList:
    return model.SubmodelElementList(
        id_short=id_short,
        type_value_list_element=model.Property,
        value_type_list_element=model.datatypes.String,
        semantic_id=semantic,
        semantic_id_list_element=semantic,
        value=[_str(None, v, semantic, source=source) for v in values],
    )


def _double_list(
    id_short: str, values: list[float], semantic: model.ExternalReference
) -> model.SubmodelElementList:
    return model.SubmodelElementList(
        id_short=id_short,
        type_value_list_element=model.Property,
        value_type_list_element=model.datatypes.Double,
        semantic_id=semantic,
        semantic_id_list_element=semantic,
        value=[
            model.Property(
                id_short=None, value_type=model.datatypes.Double, value=v, semantic_id=semantic
            )
            for v in values
        ],
    )


def _basename(name: str) -> str:
    return name.replace("\\", "/").rsplit("/", 1)[-1]


def _parse_date(timestamp: str | None) -> datetime.date | None:
    if not timestamp:
        return None
    try:
        return datetime.date.fromisoformat(timestamp[:10])  # YYYY-MM-DD (C7)
    except ValueError:
        return None
