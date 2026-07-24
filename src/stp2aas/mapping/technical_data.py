"""Mapping table B (docs/mapping-draft.md §3): IR → IDTA 02003 TechnicalData submodel.

# VERIFY(B*): 02003 v1.2 section/property semanticIds not yet checked against the
# spec PDF (see mapping/technical_data.yaml and docs/verification-log.md).
"""

from __future__ import annotations

from basyx.aas import model

from stp2aas.mapping.rules import clean_mlp, clean_text, ext_ref, load, provenance_qualifier
from stp2aas.model import PartNode

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
        semantic_id=ext_ref(_RULES["submodel"]["semanticId"]),  # VERIFY(B0)
        submodel_element=[general, technical],
    )


def _general_information(part: PartNode, org: str) -> model.SubmodelElementCollection:
    elements: list[model.SubmodelElement] = [
        # B1: product.name → en locale (required field)
        model.MultiLanguageProperty(
            id_short="ManufacturerProductDesignation",
            value={"en": clean_mlp(part.name)},
            semantic_id=_sem("ManufacturerProductDesignation"),  # VERIFY(B1)
        ),
        # B2: P21 header organization; "unknown" + gap G2 when absent
        model.Property(
            id_short="ManufacturerName",
            value_type=model.datatypes.String,
            value=clean_text(org),
            semantic_id=_sem("ManufacturerName"),  # VERIFY(B2)
        ),
    ]
    return model.SubmodelElementCollection(
        id_short="GeneralInformation",
        semantic_id=_sem("GeneralInformation"),  # VERIFY(B1/B2)
        value=elements,
    )


def _technical_properties(part: PartNode) -> model.SubmodelElementCollection:
    props = part.props
    source = props.provenance.source if props.provenance else "computed"
    elements: list[model.SubmodelElement] = []

    # B3 / B4: volume & surface area (mm³, mm²). Provenance qualifier distinguishes
    # file-provided GVP from BRepGProp-computed values.
    if props.volume_mm3 is not None:
        elements.append(_double("Volume", props.volume_mm3, _prop_sem("Volume"), source))  # B3
    if props.surface_area_mm2 is not None:
        elements.append(
            _double("SurfaceArea", props.surface_area_mm2, _prop_sem("SurfaceArea"), source)  # B4
        )

    # B5: centroid as SMC{X,Y,Z} in part-local mm.
    if props.centroid_mm is not None:
        x, y, z = props.centroid_mm
        elements.append(
            model.SubmodelElementCollection(
                id_short="Centroid",
                semantic_id=_prop_sem("Centroid"),
                value=[
                    _double("X", x, _prop_sem("CentroidX"), source),
                    _double("Y", y, _prop_sem("CentroidY"), source),
                    _double("Z", z, _prop_sem("CentroidZ"), source),
                ],
            )
        )

    # B6: material designation string (ECLASS mapping is gap G3).
    if props.material:
        elements.append(
            model.Property(
                id_short="Material",
                value_type=model.datatypes.String,
                value=clean_text(props.material),
                semantic_id=_prop_sem("Material"),
            )
        )

    # B7: mass = density × volume, only when density is known (derived value).
    if props.mass_kg is not None:
        elements.append(_double("Mass", props.mass_kg, _prop_sem("Mass"), "computed"))  # B7

    return model.SubmodelElementCollection(
        id_short="TechnicalProperties",
        semantic_id=_sem("TechnicalProperties"),  # VERIFY(B3~B7)
        value=elements,
    )


def _double(
    id_short: str, value: float, semantic: model.ExternalReference, source: str
) -> model.Property:
    """A Double property carrying a provenance Qualifier (B3~B5, B9 SI mm)."""
    return model.Property(
        id_short=id_short,
        value_type=model.datatypes.Double,
        value=float(value),
        semantic_id=semantic,
        qualifier=[provenance_qualifier(source)],
    )
