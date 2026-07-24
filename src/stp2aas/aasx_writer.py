"""Assemble AAS objects + supplementary files into an .aasx via basyx-python-sdk.

AAS composition strategy: mapping doc §0 — one AAS per unique part + one assembly
AAS whose 02011 BOM links to the parts via SameAs. Deterministic ids
(urn:stp2aas:...{uuid5(file_hash + ref_key)}) give idempotent re-conversion (§0, D5).
"""

from __future__ import annotations

import io
import json
import logging
import pathlib
import tempfile
import uuid

from basyx.aas import model
from basyx.aas.adapter.aasx import AASXWriter, DictSupplementaryFileContainer

from stp2aas.mapping.bom import build_bom
from stp2aas.mapping.models3d import build_models3d, build_models3d_multi
from stp2aas.mapping.nameplate import build_nameplate
from stp2aas.mapping.rules import clean_mlp, ext_ref, load
from stp2aas.mapping.technical_data import build_technical_data
from stp2aas.model import PartNode, PhysicalProps, Provenance, StepDocument
from stp2aas.preview import render_preview

logger = logging.getLogger("stp2aas.writer")

# Fixed namespace so uuid5 ids are stable across runs and machines (§0 idempotency).
_NS = uuid.UUID("6f9619ff-8b86-d011-b42d-00cf4fc964ff")
_ORG_FALLBACK = load("models3d")["fallbacks"]["organization"]  # G2


ASSEMBLY_STRUCTURES = ("hierarchical", "flat", "single")


def write_aasx(
    doc: StepDocument,
    out_path: str,
    embed: bool = True,
    include_partial_nameplate: bool = False,
    assembly_structure: str = "hierarchical",
) -> None:
    """Convert the intermediate representation to an .aasx package.

    assembly_structure (mapping doc §0):
      "hierarchical" — one AAS per unique part AND per unique sub-assembly; each
                       assembly AAS's 02011 BOM SameAs-links its direct children.
      "flat"         — one assembly AAS (root) + one AAS per unique part; nested
                       sub-assemblies appear only as BOM nodes.
      "single"       — one AAS for the whole model (all parts as Model3D entries,
                       a co-managed BOM tree). Experimental / comparison baseline.
    """
    if assembly_structure not in ASSEMBLY_STRUCTURES:
        raise ValueError(f"assembly_structure must be one of {ASSEMBLY_STRUCTURES}")

    obj_store = model.DictObjectStore()
    file_store = DictSupplementaryFileContainer()
    ids = _Ids(doc.file_hash)
    idshorts = _IdShortAllocator()
    org = doc.header.organization or _ORG_FALLBACK

    # The displayed name comes from the STEP's internal product name. When that is
    # missing, generic, or corrupt (control chars), fall back to the file name so a
    # meaningfully-titled file (e.g. "IRÁNYVÁLTÓ 2.0.stp") still shows its title.
    stem = pathlib.Path(doc.source_path).stem
    if _weak_name(doc.root.name):
        doc.root.name = stem

    if assembly_structure == "single" and doc.root.is_assembly:
        _build_single_aas(
            doc,
            obj_store,
            file_store,
            ids,
            idshorts,
            org,
            embed=embed,
            include_partial_nameplate=include_partial_nameplate,
        )
    else:
        single_part = not doc.root.is_assembly
        for ref_key, part in _unique_leaf_prototypes(doc.root).items():
            whole_model = single_part and part is doc.root
            _build_part_aas(
                part,
                doc,
                obj_store,
                file_store,
                ids,
                idshorts,
                org,
                embed=embed,
                whole_model=whole_model,
                include_partial_nameplate=include_partial_nameplate,
            )
        if doc.root.is_assembly:
            if assembly_structure == "hierarchical":
                for asm in _unique_assemblies(doc.root).values():
                    _build_assembly_aas(
                        asm,
                        doc,
                        obj_store,
                        file_store,
                        ids,
                        idshorts,
                        org,
                        embed=embed,
                        is_root=(asm is doc.root),
                        recurse=False,
                    )
            else:  # flat
                _build_assembly_aas(
                    doc.root,
                    doc,
                    obj_store,
                    file_store,
                    ids,
                    idshorts,
                    org,
                    embed=embed,
                    is_root=True,
                    recurse=True,
                )

    out = pathlib.Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with AASXWriter(str(out)) as writer:
        writer.write_all_aas_objects("/aasx/data.xml", obj_store, file_store, write_json=False)
    logger.info("wrote %s AAS objects to %s", len(obj_store), out_path)


# --- part AAS ---------------------------------------------------------------


def _build_part_aas(
    part,
    doc,
    obj_store,
    file_store,
    ids,
    idshorts,
    org,
    *,
    embed,
    whole_model,
    include_partial_nameplate,
):
    ref_key = part.ref_key
    stp_name = _embed_step(file_store, part, doc, embed=embed, whole_model=whole_model)
    png_name = _embed_preview(file_store, part, ids, ref_key)

    td = build_technical_data(part, org, ids.sm(ref_key, "td"))  # §3 B1~B9
    m3 = build_models3d(part, stp_name, png_name, ids.sm(ref_key, "m3"), doc.header)  # §4 C1~C24
    submodels = [td, m3]

    # §5 / D7: Nameplate omitted by default (required fields unsatisfiable).
    nameplate = build_nameplate(
        part,
        doc.header,
        ids.sm(ref_key, "np"),
        include_incomplete=include_partial_nameplate,
    )
    if nameplate is not None:
        submodels.append(nameplate)

    for sm in submodels:
        obj_store.add(sm)

    aas = model.AssetAdministrationShell(
        asset_information=model.AssetInformation(
            asset_kind=model.AssetKind.TYPE,  # design-time twin → Type AAS (gap G6)
            global_asset_id=ids.asset(ref_key),
        ),
        id_=ids.aas(ref_key),
        id_short=idshorts.allocate(part.name),
        display_name=_display_name(part.name),  # human-readable unicode name
        submodel={model.ModelReference.from_referable(sm) for sm in submodels},
    )
    obj_store.add(aas)


# --- assembly AAS -----------------------------------------------------------


# The whole model is ONE physical asset regardless of AAS composition mode, so
# every mode's root shell carries the same globalAssetId. The AAS/submodel ids
# differ per mode (their content differs), letting outputs of several modes for
# the same file coexist in one repository without id collisions.
_ROOT_ASSET_KEY = "__assembly__"


def _asm_key(node: PartNode, is_root: bool, flat: bool = False) -> str:
    """Stable id key for an assembly node's AAS + submodels (root: per mode)."""
    if is_root:
        return "flat:__assembly__" if flat else "__assembly__"
    return f"asm:{node.ref_key}"


def _child_asset_key(node: PartNode) -> str:
    """The id key a BOM node should SameAs: sub-assembly AAS or part AAS."""
    return f"asm:{node.ref_key}" if node.is_assembly else node.ref_key


def _build_assembly_aas(
    node, doc, obj_store, file_store, ids, idshorts, org, *, embed, is_root, recurse
):
    """Build one assembly AAS (root or sub-assembly). recurse=False → BOM lists only
    direct children (hierarchical); True → whole subtree nested (flat)."""
    key = _asm_key(node, is_root, flat=recurse)
    asset_key = _ROOT_ASSET_KEY if is_root else key  # same asset across modes
    asm_asset_id = ids.asset(asset_key)

    thumb_shape = node.shape_ref if node.shape_ref is not None else _placed_compound(node)
    if is_root:
        stp_name = _embed_root_step(file_store, node, doc, embed=embed, thumb_shape=thumb_shape)
    else:
        # Sub-assembly has no single source file → embed the placed compound of its subtree.
        stp_name = _embed_composed_step(file_store, node, thumb_shape, source_colors=doc.color_tool)
    png_name = _embed_preview(file_store, node, ids, key, shape=thumb_shape)

    bom = build_bom(
        node,
        ids.sm(key, "bom"),
        asm_asset_id,
        lambda p: ids.asset(_child_asset_key(p)),
        recurse=recurse,
    )  # §2
    m3 = build_models3d(node, stp_name, png_name, ids.sm(key, "m3"), doc.header)  # §4
    td = build_technical_data(_aggregate_props(node), org, ids.sm(key, "td"))  # D3

    # Derived placement "scene": each leaf occurrence's world 4x4 in this subtree
    # (gap G1 retained so viewers can compose the assembled geometry).
    scene_name = _embed_assembly_scene(file_store, node, ids, asm_asset_id, key)
    if scene_name:
        m3.submodel_element.add(
            model.File(
                id_short="PlacementScene",
                content_type="application/json",
                value=scene_name,
                semantic_id=ext_ref(load("models3d")["semantics"]["PlacementScene"]),
            )
        )

    for sm in (bom, m3, td):
        obj_store.add(sm)

    aas = model.AssetAdministrationShell(
        asset_information=model.AssetInformation(
            asset_kind=model.AssetKind.TYPE,
            global_asset_id=asm_asset_id,
        ),
        id_=ids.aas(key),
        id_short=idshorts.allocate(node.name),
        display_name=_display_name(node.name),
        submodel={model.ModelReference.from_referable(sm) for sm in (bom, m3, td)},
    )
    obj_store.add(aas)


def _build_single_aas(
    doc, obj_store, file_store, ids, idshorts, org, *, embed, include_partial_nameplate
):
    """One AAS for the whole model: all unique parts as Model3D entries + a
    co-managed 02011 BOM tree + aggregate TechnicalData (experimental baseline)."""
    root = doc.root
    key = "__single__"  # AAS/submodel ids stay mode-specific …
    asset_id = ids.asset(_ROOT_ASSET_KEY)  # … but the asset is the same product

    entries = []  # (part, step_name, preview_name) per unique leaf part
    file_of: dict[str, str] = {}  # ref_key -> embedded .stp (for the viewer scene)
    for ref_key, part in _unique_leaf_prototypes(root).items():
        stp = _embed_step(file_store, part, doc, embed=embed, whole_model=False)
        png = _embed_preview(file_store, part, ids, ref_key)
        entries.append((part, stp, png))
        if stp:
            file_of[ref_key] = stp

    # Whole-model Model3D entry FIRST: its PreviewFile is the assembled geometry
    # (the AAS thumbnail) and its DigitalFile the source STEP — not the first part.
    thumb_shape = root.shape_ref if root.shape_ref is not None else _placed_compound(root)
    root_stp = _embed_root_step(file_store, root, doc, embed=embed, thumb_shape=thumb_shape)
    root_png = _embed_preview(file_store, root, ids, key, shape=thumb_shape)
    entries.insert(0, (root, root_stp, root_png))

    m3 = build_models3d_multi(entries, ids.sm(key, "m3"), doc.header)  # many Model3D

    # Placement scene so the viewer can show the full top-level 3D. File-keyed:
    # single mode has no per-part AAS to resolve instances by asset id. Must be
    # referenced by a File element or the AASX writer drops it from the package.
    if root.is_assembly and file_of:
        scene_name = _embed_assembly_scene(file_store, root, ids, asset_id, key, file_of=file_of)
        if scene_name:
            m3.submodel_element.add(
                model.File(
                    id_short="PlacementScene",
                    content_type="application/json",
                    value=scene_name,
                    semantic_id=ext_ref(load("models3d")["semantics"]["PlacementScene"]),
                )
            )
    td = build_technical_data(_aggregate_props(root), org, ids.sm(key, "td"))
    # Co-managed BOM (no separate part AAS to SameAs); full occurrence tree.
    bom = build_bom(root, ids.sm(key, "bom"), asset_id, lambda p: "", recurse=True, linked=False)

    for sm in (bom, m3, td):
        obj_store.add(sm)

    aas = model.AssetAdministrationShell(
        asset_information=model.AssetInformation(
            asset_kind=model.AssetKind.TYPE,
            global_asset_id=asset_id,
        ),
        id_=ids.aas(key),
        id_short=idshorts.allocate(root.name),
        display_name=_display_name(root.name),
        submodel={model.ModelReference.from_referable(sm) for sm in (bom, m3, td)},
    )
    obj_store.add(aas)


def _embed_composed_step(file_store, node: PartNode, compound, source_colors=None) -> str | None:
    """Embed an assembly's placed compound as its own STEP (C1)."""
    if compound is None:
        return None
    const = load("models3d")["constants"]
    target = f"/aasx/suppl/{_safe(node.name or node.ref_key)}_{_safe(node.ref_key)}_asm.stp"
    try:
        data = _export_shape_step(compound, source_colors=source_colors)
    except Exception:  # noqa: BLE001
        return None
    return file_store.add_file(target, io.BytesIO(data), const["StepMimeType"])


def _embed_root_step(file_store, root: PartNode, doc: StepDocument, *, embed, thumb_shape):
    """Root assembly's C1 DigitalFile.

    STEP input → the source file itself (verbatim). AP242 XML input → the source
    is XML, not STEP, so embed the placed compound instead (embedding the XML
    bytes as a .stp would be wrong content with a wrong media type)."""
    if pathlib.Path(doc.source_path).suffix.lower() in (".stp", ".step"):
        return _embed_step(file_store, root, doc, embed=embed, whole_model=True)
    return _embed_composed_step(file_store, root, thumb_shape, source_colors=doc.color_tool)


def _embed_assembly_scene(
    file_store,
    root: PartNode,
    ids: "_Ids",
    asm_asset_id: str,
    key: str,
    file_of: dict[str, str] | None = None,
) -> str:
    """Embed the placement scene (world transforms per part occurrence).

    `file_of` (ref_key → embedded .stp name) adds a "file" key per instance so the
    viewer can resolve parts that have no AAS of their own (single mode)."""
    instances: list[dict] = []
    for node, w in _iter_world_leaves(root):
        inst = {
            "asset": ids.asset(node.ref_key),
            "world": [v for row in w for v in row],  # 16 floats, row-major
        }
        if file_of and file_of.get(node.ref_key):
            inst["file"] = file_of[node.ref_key]
        instances.append(inst)
    scene = {"assembly_asset_id": asm_asset_id, "unit": "mm", "instances": instances}
    payload = json.dumps(scene).encode("utf-8")
    return file_store.add_file(
        f"/aasx/suppl/{_safe(root.name)}_{_safe(key)}__scene.json",
        io.BytesIO(payload),
        "application/json",
    )


def _mat4_mul(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def _iter_world_leaves(root: PartNode):
    """Yield (leaf_node, world_4x4_row_major) for every leaf occurrence, applying
    the accumulated instance transforms (S2). Shared by the placement scene and
    the placed-compound builder."""
    identity = [[1.0 if i == j else 0.0 for j in range(4)] for i in range(4)]

    def walk(node: PartNode, world: list[list[float]]):
        w = _mat4_mul(world, node.transform) if node.transform else world
        if node.is_assembly:
            for child in node.children:
                yield from walk(child, w)
        else:
            yield node, w

    yield from walk(root, identity)


# --- supplementary files ----------------------------------------------------


def _embed_step(
    file_store, part: PartNode, doc: StepDocument, *, embed: bool, whole_model: bool
) -> str | None:
    """Register the part/assembly STEP as an AASX supplementary file (C1)."""
    const = load("models3d")["constants"]
    safe = _safe(part.name or part.ref_key)
    target = f"/aasx/suppl/{safe}_{_safe(part.ref_key)}.stp"

    if whole_model:
        data = pathlib.Path(doc.source_path).read_bytes()
    elif part.source_file:
        # AP242 XML: embed the referenced external STEP verbatim (preserves header).
        data = pathlib.Path(part.source_file).read_bytes()
    elif part.shape_ref is not None:
        data = _export_shape_step(part.shape_ref, part.color, source_colors=doc.color_tool)
    else:
        # No geometry available (e.g. AP242 XML part with a missing STEP file):
        # emit no DigitalFile value rather than crash (gap).
        logger.warning("no geometry for part %r; DigitalFile left empty", part.name)
        return None

    if not embed:
        # --link-only: reference the split file by name without embedding bytes is
        # not meaningful for re-exported parts, so link-only only bypasses embedding
        # of the whole-model file; parts are always embedded.
        if whole_model:
            return doc.source_path
    return file_store.add_file(target, io.BytesIO(data), const["StepMimeType"])


def _embed_preview(file_store, part: PartNode, ids: "_Ids", ref_key: str, shape=None) -> str:
    """Render a preview PNG and register it (C11). Never raises (D6 fallback).

    `shape` overrides part.shape_ref (used to render the *assembled* geometry for an
    assembly whose root has no single shape, e.g. AP242 XML)."""
    const = load("models3d")["constants"]
    with tempfile.TemporaryDirectory() as tmp:
        png_path = str(pathlib.Path(tmp) / "preview.png")
        render_preview(shape if shape is not None else part.shape_ref, png_path)
        data = pathlib.Path(png_path).read_bytes()
    target = f"/aasx/suppl/{_safe(part.name or ref_key)}_{_safe(ref_key)}_preview.png"
    return file_store.add_file(target, io.BytesIO(data), const["PreviewMimeType"])


def _placed_compound(root: PartNode):
    """Compound of every leaf part shape placed at its world transform (for the
    assembly thumbnail when the root has no single shape). Returns None if empty.

    copy=False shares the source TShapes, so the source document's face colours
    still resolve against the placed faces when the compound is re-exported."""
    from OCC.Core.BRep import BRep_Builder
    from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCC.Core.gp import gp_Trsf
    from OCC.Core.TopoDS import TopoDS_Compound

    comp = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(comp)
    added = 0
    for node, w in _iter_world_leaves(root):
        if node.shape_ref is None:
            continue
        trsf = gp_Trsf()
        trsf.SetValues(
            w[0][0],
            w[0][1],
            w[0][2],
            w[0][3],
            w[1][0],
            w[1][1],
            w[1][2],
            w[1][3],
            w[2][0],
            w[2][1],
            w[2][2],
            w[2][3],
        )
        try:
            placed = BRepBuilderAPI_Transform(node.shape_ref, trsf, False).Shape()
            builder.Add(comp, placed)
            added += 1
        except Exception:  # noqa: BLE001
            pass
    return comp if added else None


def _export_shape_step(
    shape,
    color: tuple[float, float, float] | None = None,
    source_colors=None,
) -> bytes:
    """Re-export a single TopoDS_Shape to its own STEP (M2: per-part split).

    Uses STEPCAFControl_Writer so the part's colour (S9) survives — a plain
    STEPControl_Writer would drop it, leaving the viewer to grey-fill the part.

    `source_colors` is the *source document's* XCAFDoc_ColorTool. Colours are
    copied per face (falling back to `color`) because (a) a colour set only on a
    COMPOUND shape's label is silently dropped by the writer, and (b) the source
    may style individual faces, which a single shape-level colour cannot express.
    """
    import OCC.Extend.DataExchange  # noqa: F401 — registers XCAF drivers
    from OCC.Core.IFSelect import IFSelect_RetDone
    from OCC.Core.Quantity import Quantity_Color, Quantity_TOC_RGB
    from OCC.Core.STEPCAFControl import STEPCAFControl_Writer
    from OCC.Core.STEPControl import STEPControl_AsIs
    from OCC.Core.TDocStd import TDocStd_Document
    from OCC.Core.TopAbs import TopAbs_FACE
    from OCC.Core.TopExp import TopExp_Explorer
    from OCC.Core.TopoDS import topods
    from OCC.Core.XCAFDoc import XCAFDoc_ColorGen, XCAFDoc_ColorSurf, XCAFDoc_DocumentTool

    with tempfile.TemporaryDirectory() as tmp:
        path = str(pathlib.Path(tmp) / "part.stp")
        doc = TDocStd_Document("XmlXCAF")
        shape_tool = XCAFDoc_DocumentTool.ShapeTool(doc.Main())
        ct = XCAFDoc_DocumentTool.ColorTool(doc.Main())
        label = shape_tool.AddShape(shape, False)
        shape_color = Quantity_Color(*color, Quantity_TOC_RGB) if color is not None else None
        if shape_color is not None:
            ct.SetColor(label, shape_color, XCAFDoc_ColorSurf)
            ct.SetColor(label, shape_color, XCAFDoc_ColorGen)
        if source_colors is not None or shape_color is not None:
            probe = Quantity_Color()
            ex = TopExp_Explorer(shape, TopAbs_FACE)
            while ex.More():
                face = topods.Face(ex.Current())
                ex.Next()
                face_color = None
                if source_colors is not None:
                    for kind in (XCAFDoc_ColorSurf, XCAFDoc_ColorGen):
                        if source_colors.GetColor(face, kind, probe):
                            face_color = Quantity_Color(
                                probe.Red(), probe.Green(), probe.Blue(), Quantity_TOC_RGB
                            )
                            break
                if face_color is None:
                    face_color = shape_color
                if face_color is None:
                    continue
                sub = shape_tool.AddSubShape(label, face)
                if not sub.IsNull():
                    ct.SetColor(sub, face_color, XCAFDoc_ColorSurf)
        writer = STEPCAFControl_Writer()
        writer.SetColorMode(True)
        writer.Transfer(doc, STEPControl_AsIs)
        if writer.Write(path) != IFSelect_RetDone:
            raise RuntimeError("per-part STEP export failed")
        return pathlib.Path(path).read_bytes()


# --- assembly technical-data aggregation (D3) -------------------------------


def _aggregate_props(root: PartNode) -> PartNode:
    """D3: assembly TechnicalData with summed volume/area over all occurrences."""
    total_vol = 0.0
    total_area = 0.0
    have_vol = have_area = False
    for leaf in _iter_leaves(root):
        if leaf.props.volume_mm3 is not None:
            total_vol += leaf.props.volume_mm3
            have_vol = True
        if leaf.props.surface_area_mm2 is not None:
            total_area += leaf.props.surface_area_mm2
            have_area = True
    return PartNode(
        name=root.name,
        product_id=root.product_id,
        ref_key=root.ref_key,
        children=root.children,  # keeps is_assembly True → ObjectType "Assembly"
        props=PhysicalProps(
            volume_mm3=total_vol if have_vol else None,
            surface_area_mm2=total_area if have_area else None,
            provenance=Provenance(source="computed"),
        ),
    )


# --- helpers ----------------------------------------------------------------


def _unique_leaf_prototypes(root: PartNode) -> dict[str, PartNode]:
    """Unique non-assembly parts keyed by ref_key (D2: duplicates collapse to one)."""
    out: dict[str, PartNode] = {}
    for node in _iter_leaves(root):
        out.setdefault(node.ref_key, node)
    return out


def _unique_assemblies(root: PartNode) -> dict[str, PartNode]:
    """Unique assembly nodes (incl. root) keyed by ref_key — one AAS each (D2)."""
    out: dict[str, PartNode] = {}

    def walk(node: PartNode) -> None:
        if node.is_assembly:
            out.setdefault(node.ref_key, node)
            for child in node.children:
                walk(child)

    walk(root)
    return out


def _iter_leaves(node: PartNode):
    if node.is_assembly:
        for child in node.children:
            yield from _iter_leaves(child)
    else:
        yield node


class _Ids:
    """Deterministic urn:stp2aas ids seeded by file hash + ref_key (§0, D5)."""

    def __init__(self, file_hash: str) -> None:
        self._hash = file_hash

    def _uid(self, key: str) -> str:
        return str(uuid.uuid5(_NS, f"{self._hash}:{key}"))

    def aas(self, key: str) -> str:
        return f"urn:stp2aas:aas:{self._uid(key)}"

    def asset(self, key: str) -> str:
        return f"urn:stp2aas:asset:{self._uid(key)}"

    def sm(self, key: str, kind: str) -> str:
        return f"urn:stp2aas:sm:{kind}:{self._uid(key)}"


class _IdShortAllocator:
    """Sanitize product names into unique, valid idShorts (D1)."""

    def __init__(self) -> None:
        self._used: set[str] = set()

    def allocate(self, name: str) -> str:
        base = _safe(name) or "Part"
        if not base[0].isalpha() and base[0] != "_":
            base = f"P_{base}"
        # idShort is a NameType: max 128 chars (found in the wild — a 154-char part
        # name). Truncate with room for the collision suffix below.
        base = base[:120]
        candidate = base
        i = 1
        while candidate in self._used:
            i += 1
            candidate = f"{base}_{i}"
        self._used.add(candidate)
        return candidate


def _safe(text: str) -> str:
    # ASCII letters/digits/underscore only — AAS idShort (AASd-002) rejects other
    # characters, and OPC part names should stay ASCII. Unicode letters (which
    # str.isalnum() accepts) are therefore mapped to '_'.
    return "".join(c if (c.isascii() and c.isalnum()) else "_" for c in (text or "")).strip("_")


def _display_name(name: str) -> dict:
    """Human-readable AAS displayName (unicode allowed, unlike idShort). Max 128
    chars per the AAS MultiLanguageNameType constraint."""
    return {"en": clean_mlp(name)[:128]}


_GENERIC_NAMES = {
    "",
    "part",
    "assembly",
    "model",
    "solid",
    "shape",
    "open cascade shape model",
    "unnamed",
    "compound",
}


_AUTO_NAME_MARKERS = ("open cascade", "step translator", "step processor", "step file")


def _weak_name(name: str) -> bool:
    """True if a name is empty, a generic placeholder, an auto-generated translator
    string, or corrupt (control chars) — in which case the file name is a better
    human-readable title."""
    if not name or not name.strip():
        return True
    if any(ord(c) < 32 and c not in "\t\n\r" for c in name):  # corrupt CAD name
        return True
    low = name.strip().lower()
    return low in _GENERIC_NAMES or any(m in low for m in _AUTO_NAME_MARKERS)
