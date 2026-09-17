"""Load declarative mapping rules (semanticIds, constants, fallbacks) from mapping/*.yaml.

Never hardcode semanticIds in mapper modules. This module
also holds the small basyx construction helpers shared by every mapper so those
concerns stay in one place.
"""

from __future__ import annotations

import functools
import os
import pathlib

import yaml
from basyx.aas import model


def _rules_dir() -> pathlib.Path:
    """Repo-root `mapping/` in a checkout; colocated YAML after `pip install`; env override."""
    env = os.environ.get("STEP2AAS_MAPPING_DIR")
    if env:
        p = pathlib.Path(env)
        if (p / "bom.yaml").is_file():
            return p
    here = pathlib.Path(__file__).resolve().parent
    for parent in here.parents:
        cand = parent / "mapping"
        if (cand / "bom.yaml").is_file():
            return cand
    if (here / "bom.yaml").is_file():
        return here
    raise FileNotFoundError(
        "mapping/*.yaml not found. Run from the step2aas repo or set STEP2AAS_MAPPING_DIR."
    )


RULES_DIR = _rules_dir()


@functools.cache
def load(name: str) -> dict:
    return yaml.safe_load((RULES_DIR / f"{name}.yaml").read_text(encoding="utf-8"))


def ext_ref(iri: str) -> model.ExternalReference:
    """A GlobalReference to an external semantic id (semanticId of a SubmodelElement)."""
    return model.ExternalReference((model.Key(model.KeyTypes.GLOBAL_REFERENCE, iri),))


def global_asset_ref(global_asset_id: str) -> model.ExternalReference:
    """A GlobalReference to an asset identifier; not an Entity SameAs target (A4)."""
    return model.ExternalReference((model.Key(model.KeyTypes.GLOBAL_REFERENCE, global_asset_id),))


def provenance_qualifier(source: str) -> model.Qualifier:
    """Provenance marker for derived values (mapping doc B3~B5, D7).

    source ∈ {"gvp", "computed", "p21-header", "fallback"}.
    """
    return model.Qualifier(
        type_="provenance",
        value_type=model.datatypes.String,
        value=source,
    )


def unit_qualifier(unit: str) -> model.Qualifier:
    """Unit of a numeric property (B9: SI millimetre family)."""
    return model.Qualifier(
        type_="unit",
        value_type=model.datatypes.String,
        value=unit,
    )


def clean_text(s: str | None) -> str:
    """Strip characters AAS forbids in text values (AASD-130)."""
    if not s:
        return ""
    return "".join(
        c
        for c in s
        if c in "\t\n\r"
        or 0x20 <= ord(c) <= 0xD7FF
        or 0xE000 <= ord(c) <= 0xFFFD
        or 0x10000 <= ord(c) <= 0x10FFFF
    )


def clean_mlp(s: str | None, fallback: str = "unnamed") -> str:
    """Cleaned text guaranteed non-empty — MultiLanguageProperty requires length ≥ 1."""
    return clean_text(s).strip() or fallback
