"""Mapping table D (docs/mapping-draft.md §5): IR → IDTA 02006 Digital Nameplate.

This mapping is *intentionally partial*. Policy D7: since STEP cannot supply a
required Nameplate field (URIOfTheProduct, D-4), the submodel is OMITTED by
default and the gap is logged (G6). `build_nameplate(..., include_incomplete=True)`
materialises the partial submodel anyway, for the paper's gap-analysis material.

# VERIFY(D*): 02006 semanticIds not yet checked against the spec PDF.
"""

from __future__ import annotations

import datetime
import logging

from basyx.aas import model

from stp2aas.mapping.rules import clean_mlp, clean_text, ext_ref, load
from stp2aas.model import P21Header, PartNode

logger = logging.getLogger("stp2aas.nameplate")

_RULES = load("nameplate")
_SEM = _RULES["semantics"]


def _sem(key: str) -> model.ExternalReference:
    return ext_ref(_SEM[key])


def build_nameplate(
    part: PartNode, header: P21Header, sm_id: str, *, include_incomplete: bool = False
) -> model.Submodel | None:
    """D-1~D-5. Returns None when required fields are unsatisfiable (D7 omission)."""
    missing = list(_RULES["required_unsatisfiable"])  # D-4, D-5
    if not include_incomplete:
        # D7: omit the whole submodel; record the gap (G6) rather than emit an
        # invalid, required-field-missing Nameplate. INFO — expected per part, a
        # WARNING for every part would drown genuine warnings.
        logger.info(
            "Nameplate omitted for %r (D7): STEP cannot supply required field(s) %s",
            part.name,
            ", ".join(missing),
        )
        return None

    org = header.organization or _RULES["fallbacks"]["organization"]
    elements: list[model.SubmodelElement] = [
        # D-1: header organization (design org ≠ manufacturer — documented limit)
        model.Property(
            id_short="ManufacturerName",
            value_type=model.datatypes.String,
            value=clean_text(org),
            semantic_id=_sem("ManufacturerName"),
        ),
        # D-2: product name
        model.MultiLanguageProperty(
            id_short="ManufacturerProductDesignation",
            value={"en": clean_mlp(part.name)},
            semantic_id=_sem("ManufacturerProductDesignation"),
        ),
    ]
    year = _year(header.timestamp)  # D-3 ⚠️ semantic mismatch (design ≠ construction), G6
    if year is not None:
        elements.append(
            model.Property(
                id_short="YearOfConstruction",
                value_type=model.datatypes.String,
                value=str(year),
                semantic_id=_sem("YearOfConstruction"),
            )
        )

    logger.warning(
        "Nameplate emitted PARTIAL for %r: required field(s) %s left unfilled (G6)",
        part.name,
        ", ".join(missing),
    )
    return model.Submodel(
        id_=sm_id,
        id_short=_RULES["submodel"]["idShort"],
        semantic_id=ext_ref(_RULES["submodel"]["semanticId"]),
        submodel_element=elements,
    )


def _year(timestamp: str | None) -> int | None:
    if not timestamp:
        return None
    try:
        return datetime.date.fromisoformat(timestamp[:10]).year
    except ValueError:
        return None
