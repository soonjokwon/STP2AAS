"""Mapping table C (docs/mapping-draft.md §4): IR → IDTA 02026 Models3D submodel.

semanticIds verified against IDTA 02026-1-0 spec (see docs/verification-log.md).
One Model3D entry per part: the original STEP file (C1~C12, C18~C24). Derived
lightweight geometry (a second Model3D entry) is future work (§6a).

Structure:  [SM] Models3D → [SML] Model3D → [SMC]{ File, Capability, Geometry }
"""

from __future__ import annotations

import datetime

from basyx.aas import model

from stp2aas.mapping.rules import clean_mlp, clean_text, ext_ref, load
from stp2aas.model import P21Header, PartNode

_RULES = load("models3d")
_SEM = _RULES["semantics"]
_CONST = _RULES["constants"]


def _sem(key: str) -> model.ExternalReference:
    return ext_ref(_SEM[key])


def _model3d_entry(
    part: PartNode, step_file_name: str, preview_file_name: str, header: P21Header
) -> model.SubmodelElementCollection:
    """One Model3D list entry: {File, Capability, Geometry} for a single part."""
    return model.SubmodelElementCollection(
        id_short=None,  # AASd-120: list elements have no idShort
        semantic_id=_sem("Model3D"),  # AASd-114: matches semantic_id_list_element
        value=[
            _file_smc(part, step_file_name, preview_file_name, header),
            _capability_smc(part),
            _geometry_smc(part),
        ],
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
) -> model.Submodel:
    """C1~C24 for a single part (one Model3D entry)."""
    return _models3d_submodel(
        [_model3d_entry(part, step_file_name, preview_file_name, header)], sm_id
    )


def build_models3d_multi(entries_data: list, sm_id: str, header: P21Header) -> model.Submodel:
    """One Models3D submodel holding many Model3D entries (single-AAS mode).

    entries_data: list of (part, step_file_name, preview_file_name).
    """
    entries = [_model3d_entry(p, stp, png, header) for (p, stp, png) in entries_data]
    return _models3d_submodel(entries, sm_id)


# --- [SMC] File (C1~C12) ----------------------------------------------------


def _file_smc(
    part: PartNode, step_file_name: str, preview_file_name: str, header: P21Header
) -> model.SubmodelElementCollection:
    return model.SubmodelElementCollection(
        id_short="File",
        semantic_id=_sem("File"),
        value=[
            _file_id(part),  # C2
            _file_classification(),  # C12
            _file_version(part, step_file_name, preview_file_name, header),  # C1,C3~C11
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
            _str("ClassId", fc["ClassId"], _sem("ClassId")),
            _str("ClassName", fc["ClassName"], _sem("ClassName")),
            _str("ClassificationSystem", fc["ClassificationSystem"], _sem("ClassificationSystem")),
        ],
    )


def _file_version(
    part: PartNode, step_file_name: str, preview_file_name: str, header: P21Header
) -> model.SubmodelElementCollection:
    org = header.organization or _RULES["fallbacks"]["organization"]
    elements: list[model.SubmodelElement] = [
        # C1: original STEP as an AASX supplementary File (value None if geometry
        # was unavailable — basyx PathType forbids an empty string).
        model.File(
            id_short="DigitalFile",
            content_type=_CONST["StepMimeType"],  # VERIFY(C1)
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
        # C5: version id (policy D5)
        _str("FileVersionId", _CONST["FileVersionId"], _sem("FileVersionId")),
        # C6: ValueList "Released"
        _str("StatusValue", _CONST["StatusValue"], _sem("StatusValue")),
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
    elements.append(_providing_organization(org))  # C10
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
    vendor = header.originating_system or _RULES["fallbacks"]["organization"]
    return model.SubmodelElementCollection(
        id_short="SourceApplication",
        semantic_id=_sem("SourceApplication"),
        value=[
            _str(
                "ApplicationName", header.originating_system or "unknown", _sem("ApplicationName")
            ),
            _str("ApplicationVersion", "", _sem("ApplicationVersion")),
            _str("VendorOrganization", vendor, _sem("VendorOrganization")),
        ],
    )


def _providing_organization(org: str) -> model.SubmodelElementCollection:
    # C10: header organization
    return model.SubmodelElementCollection(
        id_short="ProvidingOrganization",
        semantic_id=_sem("ProvidingOrganization"),
        value=[_str("OrganizationName", org, _sem("OrganizationName"))],
    )


# --- [SMC] Capability (C18~C21) ---------------------------------------------


def _capability_smc(part: PartNode) -> model.SubmodelElementCollection:
    object_type = (
        _CONST["ObjectType"]["assembly"] if part.is_assembly else _CONST["ObjectType"]["component"]
    )  # C18
    elements: list[model.SubmodelElement] = [
        _str("ObjectType", object_type, _sem("ObjectType")),
        _str("Origin", _CONST["Origin"], _sem("Origin")),  # C19, required
        # C20 [1] required — gap G5 (ValueList lacks "authoritative geometry")
        _string_list("PosModelPurpose", [_CONST["PosModelPurpose"]], _sem("PosModelPurpose")),
    ]
    if part.has_pmi:  # C21: DimTol present → EmbeddedInfo "PMI"
        elements.append(
            _string_list("EmbeddedInfo", [_CONST["EmbeddedInfoPMI"]], _sem("EmbeddedInfo"))
        )
    return model.SubmodelElementCollection(
        id_short="Capability", semantic_id=_sem("Capability"), value=elements
    )


# --- [SMC] Geometry (C22~C24) -----------------------------------------------


def _geometry_smc(part: PartNode) -> model.SubmodelElementCollection:
    elements: list[model.SubmodelElement] = [
        _str("Representation", _CONST["Representation"], _sem("Representation")),  # C22, required
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


def _str(id_short: str | None, value: str, semantic: model.ExternalReference) -> model.Property:
    return model.Property(
        id_short=id_short,
        value_type=model.datatypes.String,
        value=clean_text(value),  # AASD-130: drop control chars from STEP-derived text
        semantic_id=semantic,
    )


def _string_list(
    id_short: str, values: list[str], semantic: model.ExternalReference
) -> model.SubmodelElementList:
    return model.SubmodelElementList(
        id_short=id_short,
        type_value_list_element=model.Property,
        value_type_list_element=model.datatypes.String,
        semantic_id=semantic,
        semantic_id_list_element=semantic,
        value=[_str(None, v, semantic) for v in values],
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
