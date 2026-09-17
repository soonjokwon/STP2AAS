"""S1~S10, S12, S14: XDE/XCAF extraction via pythonocc-core.

Traversal structure adapted from STP2X3D (github.com/usnistgov/STP2X3D), which
uses the same XCAF API in C++: STEPCAFControl_Reader with name/color/GDT modes,
GetFreeShapes for roots, then a recursive walk with the three-way
IsAssembly / IsReference / IsSimpleShape classification (see STEP_Reader.cpp
AddSubComponents).
"""

from __future__ import annotations

import hashlib
import logging

# Importing OCC.Extend.DataExchange registers the XCAF storage drivers; without
# it, constructing a TDocStd_Document hard-crashes the interpreter (pythonocc 7.9).
import OCC.Extend.DataExchange  # noqa: F401
from OCC.Core.Bnd import Bnd_Box
from OCC.Core.BRepBndLib import brepbndlib
from OCC.Core.BRepGProp import brepgprop
from OCC.Core.GProp import GProp_GProps
from OCC.Core.IFSelect import IFSelect_RetDone
from OCC.Core.STEPCAFControl import STEPCAFControl_Reader
from OCC.Core.TCollection import TCollection_AsciiString
from OCC.Core.TDF import TDF_Label, TDF_LabelSequence, TDF_Tool
from OCC.Core.TDocStd import TDocStd_Document
from OCC.Core.XCAFDoc import XCAFDoc_DocumentTool

from step2aas.extract.p21_header import parse_header
from step2aas.model import PartNode, PhysicalProps, Provenance, StepDocument

logger = logging.getLogger(__name__)


def extract(path: str) -> StepDocument:
    """Load a STEP file into the intermediate representation (S1~S12, S14)."""
    doc = _read_step_document(path)
    shape_tool = XCAFDoc_DocumentTool.ShapeTool(doc.Main())
    mat_tool = _material_tool(doc)
    color_tool = XCAFDoc_DocumentTool.ColorTool(doc.Main())  # S9
    pmi_entries = _collect_pmi_shape_entries(doc)

    roots = TDF_LabelSequence()
    shape_tool.GetFreeShapes(roots)  # S1: root/free shapes (usually 1)
    if roots.Length() == 0:
        raise ValueError(f"No shapes found in STEP file: {path}")

    top_nodes = [
        _walk(shape_tool, roots.Value(i), pmi_entries, mat_tool, color_tool)
        for i in range(1, roots.Length() + 1)
    ]

    # Present a single root. Multiple free shapes → wrap in a synthetic assembly
    # so the BOM has one EntryNode (A1).
    if len(top_nodes) == 1:
        root = top_nodes[0]
    else:
        root = PartNode(name="Model", product_id="Model", ref_key="__root__")
        root.children = top_nodes

    header = parse_header(path)
    return StepDocument(
        root=root,
        header=header,
        source_path=path,
        file_hash=_file_hash(path),
        color_tool=color_tool,  # S9: per-face colours for split part re-export
        xcaf_doc=doc,
    )


def load_geometry(path: str):
    """Load a STEP part file's combined shape + derived props/bbox/PMI.

    Used by the AP242 XML path to attach geometry to a leaf part from its external
    STEP file. Returns (shape, PhysicalProps, bbox_tuple_or_None, has_pmi).
    """
    doc = _read_step_document(path)
    shape_tool = XCAFDoc_DocumentTool.ShapeTool(doc.Main())
    mat_tool = _material_tool(doc)
    pmi = bool(_collect_pmi_shape_entries(doc))

    roots = TDF_LabelSequence()
    shape_tool.GetFreeShapes(roots)
    if roots.Length() == 0:
        raise ValueError(f"No shapes found in STEP file: {path}")
    if roots.Length() == 1:
        lab = roots.Value(1)
        shape = shape_tool.GetShape(lab)
        props = _physical_props(shape)
        _fill_material(mat_tool, lab, props)
        return shape, props, _bounding_box(shape), pmi

    from OCC.Core.BRep import BRep_Builder
    from OCC.Core.TopoDS import TopoDS_Compound

    comp = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(comp)
    for i in range(1, roots.Length() + 1):
        builder.Add(comp, shape_tool.GetShape(roots.Value(i)))
    props = _physical_props(comp)
    _fill_material(mat_tool, roots.Value(1), props)
    return comp, props, _bounding_box(comp), pmi


def _read_step_document(path: str) -> TDocStd_Document:
    reader = STEPCAFControl_Reader()
    reader.SetNameMode(True)  # S3: product names
    reader.SetColorMode(True)  # S9: colours (preview quality only)
    reader.SetLayerMode(True)
    reader.SetGDTMode(True)  # S14: GD&T / PMI
    # NOTE:
    # Material-table extraction through XCAF is unstable on some AP214 inputs
    # (e.g. AS1) with pythonocc 7.9 on Windows and can hard-crash the process
    # (access violation), which cannot be caught in Python.
    # Keep conversion stable by disabling material mode for now.
    reader.SetMatMode(False)
    if reader.ReadFile(path) != IFSelect_RetDone:
        raise ValueError(f"Failed to read STEP file: {path}")
    doc = TDocStd_Document("XmlXCAF")
    if not reader.Transfer(doc):
        raise ValueError(f"STEP transfer produced no data: {path}")
    return doc


# --- recursion: mirrors STP2X3D STEP_Reader.cpp AddSubComponents ------------


def _walk(shape_tool, label: TDF_Label, pmi_entries: set[str], mat_tool, color_tool) -> PartNode:
    """Walk a shape label into a PartNode subtree.

    Assembly (S1)  -> node with child occurrences from GetComponents.
    SimpleShape    -> leaf part with geometry-derived props (S6, S7, S12, S14).
    """
    if shape_tool.IsAssembly(label):
        node = PartNode(
            name=_label_name(label) or "Assembly",
            product_id=_entry(label),
            ref_key=_entry(label),
            shape_ref=shape_tool.GetShape(label),
        )
        comps = TDF_LabelSequence()
        shape_tool.GetComponents(label, comps)
        for i in range(1, comps.Length() + 1):
            node.children.append(
                _walk_component(shape_tool, comps.Value(i), pmi_entries, mat_tool, color_tool)
            )
        return node
    return _leaf(shape_tool, label, pmi_entries, mat_tool, color_tool)


def _walk_component(
    shape_tool, comp_label: TDF_Label, pmi_entries: set[str], mat_tool, color_tool
) -> PartNode:
    """A component is a *reference* (S1 occurrence) carrying a placement (S2)."""
    transform = _location_to_matrix(shape_tool, comp_label)

    ref = TDF_Label()
    if shape_tool.GetReferredShape(comp_label, ref):
        node = _walk(shape_tool, ref, pmi_entries, mat_tool, color_tool)
    else:
        node = _leaf(shape_tool, comp_label, pmi_entries, mat_tool, color_tool)

    # The occurrence's own name (if any) overrides the prototype's; the placement
    # belongs to the occurrence, not the shared prototype.
    node.name = _label_name(comp_label) or node.name
    node.transform = transform
    return node


def _leaf(shape_tool, label: TDF_Label, pmi_entries: set[str], mat_tool, color_tool) -> PartNode:
    shape = shape_tool.GetShape(label)
    entry = _entry(label)
    props = _physical_props(shape)  # S7 (computed via BRepGProp)
    _fill_material(mat_tool, label, props)  # S8 / B6 / B7
    return PartNode(
        name=_label_name(label) or "Part",
        product_id=entry,  # STEP product.id is not separately exposed by XDE (gap G2)
        ref_key=entry,
        shape_ref=shape,
        bbox_mm=_bounding_box(shape),  # S12
        props=props,
        has_pmi=entry in pmi_entries,  # S14 -> C21
        color=_shape_color(color_tool, shape_tool, label, shape),  # S9
    )


def _shape_color(color_tool, _shape_tool, label, shape) -> tuple[float, float, float] | None:
    """S9: representative RGB (0..1). Tries the label, then the shape, then faces."""
    from OCC.Core.Quantity import Quantity_Color
    from OCC.Core.TopAbs import TopAbs_FACE
    from OCC.Core.TopExp import TopExp_Explorer
    from OCC.Core.XCAFDoc import XCAFDoc_ColorGen, XCAFDoc_ColorSurf

    if color_tool is None:
        return None
    c = Quantity_Color()
    for target in (label, shape):
        for kind in (XCAFDoc_ColorSurf, XCAFDoc_ColorGen):
            try:
                if color_tool.GetColor(target, kind, c):
                    return (c.Red(), c.Green(), c.Blue())
            except Exception as exc:  # noqa: BLE001
                logger.debug("S9: color query unavailable: %s", exc)
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    while exp.More():
        for kind in (XCAFDoc_ColorSurf, XCAFDoc_ColorGen):
            try:
                if color_tool.GetColor(exp.Current(), kind, c):
                    return (c.Red(), c.Green(), c.Blue())
            except Exception as exc:  # noqa: BLE001
                logger.debug("S9: color query unavailable: %s", exc)
        exp.Next()
    return None


# --- geometry helpers -------------------------------------------------------


def _material_tool(doc: TDocStd_Document) -> None:
    """S8: material extraction is disabled; return no tool."""
    # Mat mode is intentionally disabled in _read_step_document due to
    # hard-crash instability on some files; do not query MaterialTool.
    _ = doc


def _hstr(obj) -> str | None:
    if obj is None:
        return None
    for attr in ("GetString", "ToCString"):
        getter = getattr(obj, attr, None)
        if callable(getter):
            try:
                text = getter()
            except Exception as exc:  # noqa: BLE001
                logger.debug("Material string accessor %s failed: %s", attr, exc)
                continue
            cleaned = (text or "").strip()
            return cleaned or None
    text = str(obj).strip()
    return text or None


def _fill_material(mat_tool, shape_label: TDF_Label, props: PhysicalProps) -> None:
    """S8 / B6 / B7: material name + density → mass when volume is known.

    XCAF density is conventionally g/cm³ (OCCT). mass_kg = ρ(g/cm³) · V(mm³) · 1e-6.
    """
    if mat_tool is None:
        return
    name = None
    dens = 0.0
    dens_type = ""
    try:
        dens = float(mat_tool.GetDensityForShape(shape_label) or 0.0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("S8: density unavailable for label %s: %s", _entry(shape_label), exc)
    try:
        got = mat_tool.GetMaterial(shape_label)
        if isinstance(got, tuple) and got:
            # pythonocc: (ok, name, desc, density, densName, densValType)
            if got[0] and len(got) >= 2:
                name = _hstr(got[1])
            if len(got) >= 4 and got[3]:
                dens = float(got[3] or dens)
            if len(got) >= 6:
                dens_type = _hstr(got[5]) or ""
        elif got:
            name = _hstr(got)
    except Exception as exc:  # noqa: BLE001
        logger.warning("S8: material unavailable for label %s: %s", _entry(shape_label), exc)
    if name:
        props.material = name
    if dens <= 0:
        return
    unit = dens_type.lower()
    if "kg" in unit and "m" in unit:
        dens_g_cm3 = dens / 1000.0
    else:
        dens_g_cm3 = dens
    props.density_g_cm3 = dens_g_cm3
    if props.volume_mm3 is not None:
        props.mass_kg = dens_g_cm3 * props.volume_mm3 * 1e-6


def _physical_props(shape) -> PhysicalProps:
    """S7 / B3~B5: volume, surface area, centroid. GVP is not exposed through
    XDE in pythonocc, so we compute with BRepGProp and mark provenance=computed."""
    volume = area = None
    centroid = None
    try:
        vp = GProp_GProps()
        brepgprop.VolumeProperties(shape, vp)
        volume = vp.Mass()
        c = vp.CentreOfMass()
        centroid = (c.X(), c.Y(), c.Z())
        sp = GProp_GProps()
        brepgprop.SurfaceProperties(shape, sp)
        area = sp.Mass()
    except Exception as exc:  # noqa: BLE001 — pathological geometry may fail
        logger.warning("S7: incomplete computed physical properties: %s", exc)
    return PhysicalProps(
        volume_mm3=volume,
        surface_area_mm2=area,
        centroid_mm=centroid,
        provenance=Provenance(source="computed"),
    )


def _bounding_box(shape) -> tuple[float, ...] | None:
    """S12: axis-aligned bounding box (xmin,ymin,zmin,xmax,ymax,zmax) in mm."""
    try:
        box = Bnd_Box()
        brepbndlib.Add(shape, box)
        return tuple(box.Get())  # (xmin, ymin, zmin, xmax, ymax, zmax)
    except Exception:  # noqa: BLE001
        return None


def _location_to_matrix(shape_tool, comp_label: TDF_Label) -> list[list[float]]:
    """S2: 4x4 row-major placement matrix from the component's TopLoc_Location."""
    trsf = shape_tool.GetLocation(comp_label).Transformation()
    return [[trsf.Value(r, c) for c in range(1, 5)] for r in range(1, 4)] + [[0.0, 0.0, 0.0, 1.0]]


# --- label helpers ----------------------------------------------------------


def _label_name(label: TDF_Label) -> str | None:
    # pythonocc exposes the TDataStd_Name attribute via this convenience method.
    try:
        raw = label.GetLabelName()
    except Exception:  # noqa: BLE001
        return None
    cleaned = (raw or "").replace("\r", " ").replace("\n", " ").strip()
    # pythonocc renders unnamed reference (component) labels as "=>[0:1:1:x]";
    # that is a synthetic pointer, not a product name — treat it as absent so
    # the occurrence inherits its prototype's real name.
    if not cleaned or cleaned.startswith("=>"):
        return None
    return cleaned


def _entry(label: TDF_Label) -> str:
    """Stable per-document label path (e.g. ``0:1:1:2``) used as prototype key."""
    s = TCollection_AsciiString()
    TDF_Tool.Entry(label, s)
    return s.ToCString()


def _collect_pmi_shape_entries(doc: TDocStd_Document) -> set[str]:
    """S14: entries of shape labels that carry GD&T (dimensions or tolerances).

    Mirrors STP2X3D ReadGDT: DimTolTool.GetDimensionLabels /
    GetGeomToleranceLabels, resolving each to its referenced shape label.
    """
    entries: set[str] = set()
    try:
        gdt = XCAFDoc_DocumentTool.DimTolTool(doc.Main())
    except Exception:  # noqa: BLE001
        return entries

    for getter in ("GetDimensionLabels", "GetGeomToleranceLabels"):
        seq = TDF_LabelSequence()
        try:
            getattr(gdt, getter)(seq)
        except Exception as exc:  # noqa: BLE001
            logger.warning("S14: PMI labels unavailable from %s: %s", getter, exc)
            continue
        for i in range(1, seq.Length() + 1):
            gdt_label = seq.Value(i)
            shapes = TDF_LabelSequence()
            try:
                if gdt.GetRefShapeLabel(gdt_label, TDF_LabelSequence(), shapes):
                    for j in range(1, shapes.Length() + 1):
                        entries.add(_entry(shapes.Value(j)))
            except Exception as exc:  # noqa: BLE001
                logger.warning("S14: PMI shape reference unavailable: %s", exc)
                # Presence alone matters for C21; if we cannot resolve the
                # referenced shape we simply do not flag a specific part.
                continue
    return entries


def _file_hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()
