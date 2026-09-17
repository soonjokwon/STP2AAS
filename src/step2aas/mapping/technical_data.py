"""Mapping table B (docs/mapping-draft.md §3): IR → IDTA 02003 TechnicalData submodel.

semanticIds verified against IDTA 02003-1-2 (see docs/verification-log.md).
"""

from __future__ import annotations

from basyx.aas import model

from step2aas.mapping.rules import clean_mlp, ext_ref, load, provenance_qualifier, unit_qualifier
from step2aas.model import PartNode

_RULES = load("technical_data")
_SEM = _RULES["semantics"]
_PROPS = _RULES["props"]


def _sem(key: str) -> model.ExternalReference:
    return ext_ref(_SEM[key])


def _prop_sem(key: str) -> model.ExternalReference:
    return ext_ref(_PROPS[key])


def build_technical_data(part: PartNode, org_fallback: str, sm_id: str) -> model.Submodel:
    """B1~B9. `org_fallback` is the P21 header organization (or the G2 fallback)."""
    general = _general_information(part, org_fallback)
    technical = _technical_properties(part)
    return model.Submodel(
        id_=sm_id,
        id_short=_RULES["submodel"]["idShort"],
        semantic_id=ext_ref(_RULES["submodel"]["semanticId"]),  # verified 02003-1-2
        submodel_element=[general, technical],
    )


def _general_information(part: PartNode, org: str) -> model.SubmodelElementCollection:
    elements: list[model.SubmodelElement] = [
        # B1: product.name → en locale (required field)
        model.MultiLanguageProperty(
            id_short="ManufacturerProductDesignation",
            value={"en": clean_mlp(part.name)},
            semantic_id=_sem("ManufacturerProductDesignation"),  # verified 02003-1-2
        ),
        # B2: P21 header organization; "unknown" + gap G2 when absent
        model.Property(
            id_short="ManufacturerName",
            value_type=model.datatypes.String,
            value=org,
            semantic_id=_sem("ManufacturerName"),  # verified 02003-1-2
            qualifier=[
                provenance_qualifier(
                    "p21-header" if org != _RULES["fallbacks"]["organization"] else "fallback"
                )
            ],
        ),
    ]
    return model.SubmodelElementCollection(
        id_short="GeneralInformation",
        semantic_id=_sem("GeneralInformation"),
        value=elements,
    )


def _technical_properties(part: PartNode) -> model.SubmodelElementCollection:
    props = part.props
    source = props.provenance.source if props.provenance else "computed"
    elements: list[model.SubmodelElement] = []

    # B3 / B4: volume & surface area (mm³, mm²). Provenance qualifier distinguishes
    # file-provided GVP from BRepGProp-computed values.
    if props.volume_mm3 is not None:
        elements.append(
            _double("Volume", props.volume_mm3, _prop_sem("Volume"), source, "mm3")  # B3
        )
    if props.surface_area_mm2 is not None:
        elements.append(
            _double(
                "SurfaceArea", props.surface_area_mm2, _prop_sem("SurfaceArea"), source, "mm2"
            )  # B4
        )

    # B5: centroid as SMC{AxisX,AxisY,AxisZ} in part-local mm.
    if props.centroid_mm is not None:
        x, y, z = props.centroid_mm
        elements.append(
            model.SubmodelElementCollection(
                id_short="Centroid",
                semantic_id=_prop_sem("Centroid"),
                value=[
                    _double("AxisX", x, _prop_sem("CentroidX"), source, "mm"),
                    _double("AxisY", y, _prop_sem("CentroidY"), source, "mm"),
                    _double("AxisZ", z, _prop_sem("CentroidZ"), source, "mm"),
                ],
            )
        )

    # B6: material designation string (ECLASS mapping is gap G3).
    if props.material:
        elements.append(
            model.Property(
                id_short="Material",
                value_type=model.datatypes.String,
                value=props.material,
                semantic_id=_prop_sem("Material"),
            )
        )

    # B7: mass = density × volume, only when density is known (derived value).
    mass = props.mass_kg
    if mass is None and props.density_g_cm3 and props.volume_mm3 is not None:
        mass = props.density_g_cm3 * props.volume_mm3 * 1e-6
    if mass is not None:
        elements.append(_double("Mass", mass, _prop_sem("Mass"), "computed", "kg"))  # B7

    return model.SubmodelElementCollection(
        id_short="TechnicalProperties",
        semantic_id=_sem("TechnicalProperties"),  # verified 02003-1-2
        value=elements,
    )


def _double(
    id_short: str,
    value: float,
    semantic: model.ExternalReference,
    source: str,
    unit: str | None = None,
) -> model.Property:
    """A Double property carrying provenance (B3~B5) and optional unit (B9)."""
    qualifiers = [provenance_qualifier(source)]
    if unit:
        qualifiers.append(unit_qualifier(unit))
    return model.Property(
        id_short=id_short,
        value_type=model.datatypes.Double,
        value=float(value),
        semantic_id=semantic,
        qualifier=qualifiers,
    )
