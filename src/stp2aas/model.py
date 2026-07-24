"""Intermediate representation between XDE extraction and AAS mapping.

Every field maps to an inventory item (S*) in docs/mapping-draft.md §1.
"""

from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Provenance:
    """Where a derived value came from (mapping doc B3~B5, D7)."""

    source: str  # "gvp" | "computed" | "p21-header" | "fallback"


@dataclass
class PhysicalProps:
    volume_mm3: float | None = None  # S7
    surface_area_mm2: float | None = None  # S7
    centroid_mm: tuple[float, float, float] | None = None  # S7
    provenance: Provenance | None = None
    material: str | None = None  # S8
    mass_kg: float | None = None  # B7 (derived)


@dataclass
class PartNode:
    name: str  # S3
    product_id: str  # S4
    shape_ref: object | None = None  # S6: TopoDS_Shape (kept opaque here)
    transform: list[list[float]] | None = None  # S2: 4x4 row-major
    bbox_mm: tuple[float, ...] | None = None  # S12: (xmin..zmax)
    props: PhysicalProps = field(default_factory=PhysicalProps)
    has_pmi: bool = False  # S14 → C21
    children: list["PartNode"] = field(default_factory=list)  # S1
    # Stable identity of the *referred prototype* label (S1/S5). Occurrences that
    # reference the same prototype share this key, so one part AAS is emitted and
    # multiple BOM Nodes SameAs it (design decision D2).
    ref_key: str = ""
    # External geometry file backing this part, when it comes from an AP242 BOM XML
    # that references separate STEP part files. Embedded verbatim as the C1 file.
    source_file: str | None = None
    # S9: representative RGB colour (0..1) from XCAF, preserved when re-exporting a
    # split part STEP so the viewer keeps the CAD colour (else it falls back to grey).
    color: tuple[float, float, float] | None = None

    @property
    def is_assembly(self) -> bool:
        return bool(self.children)


@dataclass
class P21Header:
    """S11 — parsed from the Part 21 HEADER section as text."""

    schema: str | None = None  # FILE_SCHEMA → AP203/214/242 (C8)
    author: str | None = None
    organization: str | None = None
    originating_system: str | None = None
    timestamp: str | None = None


@dataclass
class StepDocument:
    root: PartNode
    header: P21Header
    source_path: str
    file_hash: str  # for deterministic AAS ids (§0) and D5
    # S9: source XCAF colour tool (+ its document, which must be kept alive for the
    # labels to stay valid). Used when re-exporting split part STEPs so per-face
    # colours survive — a single shape-level colour is lost on COMPOUND shapes.
    color_tool: object | None = None
    xcaf_doc: object | None = None
