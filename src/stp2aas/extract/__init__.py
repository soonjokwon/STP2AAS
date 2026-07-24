"""Input dispatch shared by the CLI and the GUI."""

from __future__ import annotations

import pathlib

_XML_EXT = {".stpx", ".xml"}


def extract_any(path: str):
    """Extract any supported input into the IR: AP242 Domain Model XML
    (.stpx/.xml) or Part 21 STEP (everything else). Imports lazily — pythonocc
    is heavy, so nothing loads until a file is actually converted."""
    if pathlib.Path(path).suffix.lower() in _XML_EXT:
        from stp2aas.extract.ap242xml import extract_ap242_xml

        return extract_ap242_xml(path)
    from stp2aas.extract.xde import extract

    return extract(path)
