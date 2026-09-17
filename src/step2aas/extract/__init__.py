"""CAD extractors: Part 21 STEP (XDE) and AP242 Domain Model XML."""

from __future__ import annotations

import pathlib

from step2aas.model import StepDocument


def extract_document(input_path: str) -> StepDocument:
    """Dispatch on suffix: AP242 Domain Model XML (.stpx/.xml) vs Part 21 STEP."""
    suffix = pathlib.Path(input_path).suffix.lower()
    if suffix in (".stpx", ".xml"):
        from step2aas.extract.ap242xml import extract_ap242_xml

        return extract_ap242_xml(input_path)
    from step2aas.extract.xde import extract

    return extract(input_path)


# Alias used by the zip-side CLI/GUI/fusion scripts.
extract_any = extract_document
