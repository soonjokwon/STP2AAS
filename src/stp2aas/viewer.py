"""Render an .aasx package to a self-contained HTML viewer.

Shows the AAS structure (submodel tree, BOM, TechnicalData, preview thumbnails)
next to an interactive WebGL 3D viewport. The 3D geometry is produced by
re-reading each part's embedded STEP from the package and tessellating it with
pythonocc (BRepMesh) — no external tool, and the output is a single self-contained
.html (a small hand-written WebGL renderer, no third-party JS).

    python -m stp2aas.viewer --input out/suspension.aasx [-o out.html] [--open] [--no-3d]
"""

from __future__ import annotations

import argparse
import array
import base64
import html
import io
import json
import pathlib
import sys
import tempfile

from basyx.aas import model
from basyx.aas.adapter.aasx import AASXReader, DictSupplementaryFileContainer

_STEP_EXT = (".stp", ".step")


# --- package loading --------------------------------------------------------


def load(aasx_path: str):
    store = model.DictObjectStore()
    files = DictSupplementaryFileContainer()
    with AASXReader(aasx_path) as reader:
        reader.read_into(store, files)
    return store, files


def read_supp(files: DictSupplementaryFileContainer, name: str) -> bytes | None:
    if not name or name not in files:  # __contains__ is O(1); list(files) is not
        return None
    buf = io.BytesIO()
    files.write_file(name, buf)
    return buf.getvalue()


# --- small helpers ----------------------------------------------------------


def esc(x) -> str:
    return html.escape(str(x)) if x is not None else ""


def sem_of(elem) -> str:
    try:
        return elem.semantic_id.key[0].value if elem.semantic_id else ""
    except Exception:  # noqa: BLE001
        return ""


def children_of(elem):
    if isinstance(elem, model.Submodel):
        return list(elem.submodel_element)
    val = getattr(elem, "value", None)
    if isinstance(elem, model.File):
        return []
    if isinstance(val, (list, set)) or (hasattr(val, "__iter__") and not isinstance(val, str)):
        try:
            return [c for c in val if isinstance(c, model.SubmodelElement)]
        except TypeError:
            return []
    if isinstance(elem, model.Entity):
        return list(elem.statement)
    return []


def _walk(elem):
    yield elem
    for c in children_of(elem):
        yield from _walk(c)


def anchor(aas_id: str) -> str:
    return "aas_" + str(abs(hash(aas_id)))


def file_key(supp_name: str) -> str:
    """Mesh-dict key for a part mesh identified by its embedded STEP file (single
    mode: the parts have no AAS of their own to anchor a mesh to)."""
    return "f_" + str(abs(hash(supp_name)))


def _elem_disp(obj) -> str | None:
    """displayName of any referable ('en' preferred), or None."""
    dn = getattr(obj, "display_name", None)
    if dn:
        try:
            return dn.get("en") or next(iter(dn.values()))
        except Exception:  # noqa: BLE001
            pass
    return None


def disp_name(shell) -> str:
    """Human-readable AAS name: displayName (unicode) if present, else id_short."""
    return _elem_disp(shell) or shell.id_short or shell.id


# --- tessellation (pythonocc) -----------------------------------------------


def load_colored_shape(path):
    """Load a STEP into an XCAF doc so per-face colours are available (STP2X3D-style).

    Returns (shape, color_tool, doc). The doc must be kept alive by the caller.
    """
    import OCC.Extend.DataExchange  # noqa: F401 — registers XCAF drivers
    from OCC.Core.STEPCAFControl import STEPCAFControl_Reader
    from OCC.Core.TDF import TDF_LabelSequence
    from OCC.Core.TDocStd import TDocStd_Document
    from OCC.Core.XCAFDoc import XCAFDoc_DocumentTool

    reader = STEPCAFControl_Reader()
    reader.SetColorMode(True)
    reader.SetNameMode(True)
    reader.ReadFile(path)
    doc = TDocStd_Document("XmlXCAF")
    reader.Transfer(doc)
    shape_tool = XCAFDoc_DocumentTool.ShapeTool(doc.Main())
    color_tool = XCAFDoc_DocumentTool.ColorTool(doc.Main())
    roots = TDF_LabelSequence()
    shape_tool.GetFreeShapes(roots)
    if roots.Length() == 1:
        shape = shape_tool.GetShape(roots.Value(1))
    else:
        from OCC.Core.BRep import BRep_Builder
        from OCC.Core.TopoDS import TopoDS_Compound

        shape = TopoDS_Compound()
        b = BRep_Builder()
        b.MakeCompound(shape)
        for i in range(1, roots.Length() + 1):
            b.Add(shape, shape_tool.GetShape(roots.Value(i)))
    return shape, color_tool, doc


def _face_rgb(color_tool, face, shape, fallback):
    """Face colour → shape colour → fallback (STP2X3D priority).

    Keeps the STEP's real colour even when it is dark/near-black — those are genuine
    material colours (STP2X3D renders them as-is). The neutral fallback is used only
    when the model assigns no colour at all."""
    from OCC.Core.Quantity import Quantity_Color
    from OCC.Core.XCAFDoc import XCAFDoc_ColorGen, XCAFDoc_ColorSurf

    c = Quantity_Color()
    for target in (face, shape):
        for kind in (XCAFDoc_ColorSurf, XCAFDoc_ColorGen):
            if color_tool.GetColor(target, kind, c):
                # Same values STP2X3D writes to X3D diffuseColor (raw OCCT components).
                return [round(c.Red() * 255), round(c.Green() * 255), round(c.Blue() * 255)]
    return fallback


def tessellate(shape, color_tool=None, fallback=(180, 185, 195)):
    """Return (positions, normals, colors, indices, line_pos, line_col, center, radius).

    Faces are tessellated and filled (per-vertex colours from the STEP). A shape that
    has NO faces (an edge-only / wireframe body, e.g. a sketch or construction curve)
    is instead returned as line segments in `line_pos` so it renders as lines rather
    than nothing — mirroring how STP2X3D emits IndexedLineSet for such geometry.
    Solid/shell parts return empty line data (no wireframe overlay).
    """
    from OCC.Core.Bnd import Bnd_Box
    from OCC.Core.BRep import BRep_Tool
    from OCC.Core.BRepBndLib import brepbndlib
    from OCC.Core.BRepMesh import BRepMesh_IncrementalMesh
    from OCC.Core.TopAbs import TopAbs_FACE, TopAbs_REVERSED, TopAbs_SHELL
    from OCC.Core.TopExp import TopExp_Explorer
    from OCC.Core.TopLoc import TopLoc_Location
    from OCC.Core.TopoDS import topods

    box = Bnd_Box()
    brepbndlib.Add(shape, box)
    if box.IsVoid():  # empty shape (e.g. a 2D sketch OCCT could not translate)
        return [], [], [], [], [], [], [0.0, 0.0, 0.0], 1.0
    xmin, ymin, zmin, xmax, ymax, zmax = box.Get()
    diag = ((xmax - xmin) ** 2 + (ymax - ymin) ** 2 + (zmax - zmin) ** 2) ** 0.5 or 1.0
    center = [(xmin + xmax) / 2, (ymin + ymax) / 2, (zmin + zmax) / 2]
    radius = diag / 2 or 1.0

    BRepMesh_IncrementalMesh(shape, max(diag * 0.015, 1e-4), False, 0.6, True)
    edefl = max(diag * 0.01, 1e-4)

    positions: list[float] = []
    colors: list[int] = []
    indices: list[int] = []
    line_pos: list[float] = []
    line_col: list[int] = []

    def add_faces(sub) -> None:
        exp = TopExp_Explorer(sub, TopAbs_FACE)
        while exp.More():
            face = topods.Face(exp.Current())
            loc = TopLoc_Location()
            tri = BRep_Tool.Triangulation(face, loc)
            exp.Next()
            if tri is None:
                continue
            rgb = (
                _face_rgb(color_tool, face, shape, list(fallback)) if color_tool else list(fallback)
            )
            trsf = loc.Transformation()
            base = len(positions) // 3
            for i in range(1, tri.NbNodes() + 1):
                p = tri.Node(i).Transformed(trsf)
                positions.extend((p.X(), p.Y(), p.Z()))
                colors.extend(rgb)
            reverse = face.Orientation() == TopAbs_REVERSED
            for i in range(1, tri.NbTriangles() + 1):
                n1, n2, n3 = tri.Triangle(i).Get()
                if reverse:
                    n1, n3 = n3, n1
                indices.extend((base + n1 - 1, base + n2 - 1, base + n3 - 1))

    def add_lines(sub) -> None:
        segs = _edge_segments(sub, edefl)
        if not segs:
            return
        rgb = _face_rgb(color_tool, sub, shape, [70, 80, 95]) if color_tool else [70, 80, 95]
        line_pos.extend(segs)
        line_col.extend(rgb * (len(segs) // 3))

    def is_sheet(sub) -> bool:
        b = Bnd_Box()
        brepbndlib.Add(sub, b)
        try:
            x0, y0, z0, x1, y1, z1 = b.Get()
        except Exception:  # noqa: BLE001 — void box
            return False
        dims = sorted((x1 - x0, y1 - y0, z1 - z0))
        return dims[2] > 1e-9 and dims[0] < dims[2] * 0.02  # near-zero-thickness sheet

    # Per shell: fill 3D shells, but draw zero-thickness "sheet" shells (datum/PMI
    # planes, construction surfaces) as edge outlines only — not filled.
    shells = []
    shx = TopExp_Explorer(shape, TopAbs_SHELL)
    while shx.More():
        shells.append(topods.Shell(shx.Current()))
        shx.Next()
    if shells:
        for sh in shells:
            if is_sheet(sh):
                add_lines(sh)
            else:
                add_faces(sh)
    else:
        add_faces(shape)
    if not indices and not line_pos:  # face-less body (pure wireframe) → lines
        add_lines(shape)
    if not indices and not line_pos:
        # Vertex-only shape (datum/reference points, e.g. NX sketch points): draw
        # each point as a small 3-axis cross so the AAS is not invisible when
        # selected in the viewer.
        from OCC.Core.TopAbs import TopAbs_VERTEX

        # A lone vertex's bbox is only its tolerance (~1e-7), so scale off an
        # absolute floor (mm) or the cross — and the camera radius — would be ~0.
        arm = max(diag * 0.02, 2.0)
        rgb = [70, 80, 95]
        vx = TopExp_Explorer(shape, TopAbs_VERTEX)
        while vx.More():
            p = BRep_Tool.Pnt(topods.Vertex(vx.Current()))
            x, y, z = p.X(), p.Y(), p.Z()
            for dx, dy, dz in ((arm, 0, 0), (0, arm, 0), (0, 0, arm)):
                line_pos.extend((x - dx, y - dy, z - dz, x + dx, y + dy, z + dz))
                line_col.extend(rgb * 2)
            vx.Next()
        if line_pos:
            radius = max(radius, arm * 1.5)

    normals = _vertex_normals(positions, indices)
    return positions, normals, colors, indices, line_pos, line_col, center, radius


def _edge_segments(shape, deflection: float) -> list[float]:
    """Feature edges as GL_LINES endpoints (each consecutive pair = one segment)."""
    from OCC.Core.BRepAdaptor import BRepAdaptor_Curve
    from OCC.Core.GCPnts import GCPnts_UniformDeflection
    from OCC.Core.TopAbs import TopAbs_EDGE
    from OCC.Core.TopExp import TopExp_Explorer
    from OCC.Core.TopoDS import topods

    out: list[float] = []
    exp = TopExp_Explorer(shape, TopAbs_EDGE)  # shared edges may repeat — harmless
    while exp.More():
        edge = topods.Edge(exp.Current())
        exp.Next()
        try:
            curve = BRepAdaptor_Curve(edge)
            disc = GCPnts_UniformDeflection(curve, deflection)
            if not disc.IsDone() or disc.NbPoints() < 2:
                continue
            pts = [curve.Value(disc.Parameter(j)) for j in range(1, disc.NbPoints() + 1)]
            for a, b in zip(pts[:-1], pts[1:]):
                out.extend((a.X(), a.Y(), a.Z(), b.X(), b.Y(), b.Z()))
        except Exception:  # noqa: BLE001
            continue
    return out


def _vertex_normals(positions, indices):
    n = [0.0] * len(positions)
    for k in range(0, len(indices), 3):
        a, b, c = indices[k] * 3, indices[k + 1] * 3, indices[k + 2] * 3
        ux, uy, uz = (
            positions[b] - positions[a],
            positions[b + 1] - positions[a + 1],
            positions[b + 2] - positions[a + 2],
        )
        vx, vy, vz = (
            positions[c] - positions[a],
            positions[c + 1] - positions[a + 1],
            positions[c + 2] - positions[a + 2],
        )
        nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        for idx in (a, b, c):
            n[idx] += nx
            n[idx + 1] += ny
            n[idx + 2] += nz
    for i in range(0, len(n), 3):
        mag = (n[i] ** 2 + n[i + 1] ** 2 + n[i + 2] ** 2) ** 0.5 or 1.0
        n[i], n[i + 1], n[i + 2] = n[i] / mag, n[i + 1] / mag, n[i + 2] / mag
    return n


def _b64(typecode: str, seq) -> str:
    return base64.b64encode(array.array(typecode, seq).tobytes()).decode()


def build_meshes(store, files, enable: bool) -> dict:
    """anchor -> packed mesh dict.

    Parts are tessellated from their embedded STEP; assemblies are *composed* by
    instancing the part meshes at the world transforms recorded in the embedded
    placement scene (``*__scene.json``), so the top-level model is also viewable.
    """
    if not enable:
        return {}

    shells = [o for o in store if isinstance(o, model.AssetAdministrationShell)]
    asset_to_anchor = {
        s.asset_information.global_asset_id: anchor(s.id)
        for s in shells
        if s.asset_information.global_asset_id
    }

    raw: dict[str, dict] = {}  # anchor -> {pos, nrm, col, idx, center, radius}
    asset_raw: dict[str, dict] = {}  # part_asset_id -> raw mesh
    file_raw: dict[str, dict | None] = {}  # supp .stp name -> raw mesh (None = failed)

    # Assemblies with a placement scene get their mesh *composed* from part meshes
    # in phase 2 — tessellating their own DigitalFile first (for the root that is
    # the entire source model) would be pure wasted work, so skip those in phase 1
    # and only fall back to it if the composition yields nothing.
    scenes = _load_scenes(files)
    scene_anchors = {asset_to_anchor.get(s.get("assembly_asset_id")) for s in scenes}
    scene_anchors.discard(None)

    def tess_shell(shell) -> None:
        stp_name = _digital_file_name(shell, store)
        if not stp_name or not stp_name.lower().endswith(_STEP_EXT):
            return
        try:
            m = _tessellate_supp_step(files, stp_name)
        except Exception as exc:  # noqa: BLE001
            print(f"  (3D skip {shell.id_short}: {exc})", file=sys.stderr)
            return
        if m is None:
            return  # neither faces nor edges — nothing to show
        raw[anchor(shell.id)] = m
        file_raw[stp_name] = m
        aid = shell.asset_information.global_asset_id
        if aid:
            asset_raw[aid] = m

    # Phase 1: tessellate each part's embedded STEP (with per-face colours).
    for shell in shells:
        if anchor(shell.id) not in scene_anchors:
            tess_shell(shell)

    # Phase 2: compose assembly meshes from placement scenes. Instances resolve by
    # part asset id (hierarchical/flat: a part AAS exists) or by embedded file name
    # (single mode: parts have no AAS of their own).
    for scene in scenes:
        asm_anchor = asset_to_anchor.get(scene.get("assembly_asset_id"))
        if not asm_anchor:
            continue
        for inst in scene["instances"]:
            fname = inst.get("file")
            if fname and fname not in file_raw and inst.get("asset") not in asset_raw:
                try:
                    file_raw[fname] = _tessellate_supp_step(files, fname)
                except Exception as exc:  # noqa: BLE001
                    print(f"  (3D skip {fname}: {exc})", file=sys.stderr)
                    file_raw[fname] = None  # cache the failure — don't retry
        composed = _compose(scene["instances"], asset_raw, file_raw)
        if composed:
            raw[asm_anchor] = composed

    # Fallback: a scene-covered assembly whose composition produced nothing (e.g.
    # every part file missing) still gets its own DigitalFile tessellated.
    for shell in shells:
        if anchor(shell.id) in scene_anchors and anchor(shell.id) not in raw:
            tess_shell(shell)

    # Phase 3: pack everything to base64. Part meshes that no AAS anchors (single
    # mode) are packed under file keys so BOM nodes can still show their 3D.
    packed = {a: _pack(m) for a, m in raw.items()}
    anchored = {id(m) for m in raw.values()}
    for fname, m in file_raw.items():
        if m is not None and id(m) not in anchored:
            packed[file_key(fname)] = _pack(m)
    return packed


def _tessellate_supp_step(files, name: str) -> dict | None:
    """Tessellate one embedded STEP supplementary file → raw mesh dict, or None
    if the file is missing or yields no drawable geometry."""
    data = read_supp(files, name)
    if not data:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "part.stp"
        path.write_bytes(data)
        shape, color_tool, _doc = load_colored_shape(str(path))
        # Neutral default for parts the model leaves uncoloured (STP2X3D-like);
        # real STEP colours are used whenever present.
        pos, nrm, col, idx, lpos, lcol, center, radius = tessellate(
            shape, color_tool, (196, 199, 204)
        )
    if not idx and not lpos:
        return None
    return {
        "pos": pos,
        "nrm": nrm,
        "col": col,
        "idx": idx,
        "lpos": lpos,
        "lcol": lcol,
        "center": center,
        "radius": radius,
    }


def _load_scenes(files) -> list[dict]:
    scenes = []
    for name in list(files):
        if name.endswith("__scene.json"):
            data = read_supp(files, name)
            if data:
                try:
                    scenes.append(json.loads(data.decode("utf-8")))
                except Exception:  # noqa: BLE001
                    pass
    return scenes


def _compose(instances, asset_raw, file_raw=None) -> dict | None:
    """Bake instanced part meshes into one merged mesh using world 4x4 matrices.
    Per-vertex colours are carried through so each part keeps its own colour.
    Instances resolve via asset id first, then via embedded file name (single mode)."""
    pos: list[float] = []
    nrm: list[float] = []
    col: list[int] = []
    idx: list[int] = []
    lpos: list[float] = []
    lcol: list[int] = []

    def xform(w, x, y, z):
        return (
            w[0] * x + w[1] * y + w[2] * z + w[3],
            w[4] * x + w[5] * y + w[6] * z + w[7],
            w[8] * x + w[9] * y + w[10] * z + w[11],
        )

    for inst in instances:
        part = asset_raw.get(inst.get("asset")) or (file_raw or {}).get(inst.get("file"))
        if part is None:
            continue
        w = inst["world"]  # 16 floats row-major
        base = len(pos) // 3
        p = part["pos"]
        for k in range(0, len(p), 3):
            pos.extend(xform(w, p[k], p[k + 1], p[k + 2]))
        n = part["nrm"]
        for k in range(0, len(n), 3):
            x, y, z = n[k], n[k + 1], n[k + 2]
            nx = w[0] * x + w[1] * y + w[2] * z
            ny = w[4] * x + w[5] * y + w[6] * z
            nz = w[8] * x + w[9] * y + w[10] * z
            mag = (nx * nx + ny * ny + nz * nz) ** 0.5 or 1.0
            nrm.extend((nx / mag, ny / mag, nz / mag))
        col.extend(part["col"])
        idx.extend(base + i for i in part["idx"])
        lp = part.get("lpos", [])
        for k in range(0, len(lp), 3):
            lpos.extend(xform(w, lp[k], lp[k + 1], lp[k + 2]))
        lcol.extend(part.get("lcol", []))
    if not idx and not lpos:
        return None
    ref = pos or lpos
    xs, ys, zs = ref[0::3], ref[1::3], ref[2::3]
    center = [(min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, (min(zs) + max(zs)) / 2]
    radius = max((max(xs) - min(xs)), (max(ys) - min(ys)), (max(zs) - min(zs))) / 2 or 1.0
    return {
        "pos": pos,
        "nrm": nrm,
        "col": col,
        "idx": idx,
        "lpos": lpos,
        "lcol": lcol,
        "center": center,
        "radius": radius,
    }


def _pack(m: dict) -> dict:
    return {
        "p": _b64("f", m["pos"]),
        "n": _b64("f", m["nrm"]),
        "vc": _b64("B", m["col"]),  # per-vertex RGB (uint8)
        "i": _b64("I", m["idx"]),
        "lp": _b64("f", m.get("lpos", [])),  # wireframe (face-less) line endpoints
        "lc": _b64("B", m.get("lcol", [])),  # per-line-vertex RGB
        "c": [round(v, 4) for v in m["center"]],
        "r": round(m["radius"], 4),
        "tris": len(m["idx"]) // 3,
    }


def _digital_file_name(shell, store) -> str | None:
    for ref in shell.submodel:
        sm = ref.resolve(store)
        if sm is None or sm.id_short != "Models3D":
            continue
        for el in _walk(sm):
            if isinstance(el, model.File) and el.id_short == "DigitalFile" and el.value:
                return el.value
    return None


# --- element rendering ------------------------------------------------------


def render_element(elem, files, asset_to_aas, name_to_mesh=None) -> str:
    name = esc(getattr(elem, "id_short", None) or "—")
    sem = sem_of(elem)
    sem_html = (
        f'<span class="sem" title="{esc(sem)}">{esc(sem.rsplit("/", 3)[-1]) if sem else ""}</span>'
    )

    if isinstance(elem, model.Property):
        q = ""
        for qual in getattr(elem, "qualifier", []) or []:
            if qual.type == "provenance":
                q = f'<span class="prov">{esc(qual.value)}</span>'
        return f'<div class="row"><span class="k">{name}</span>{sem_html}<span class="v">{esc(elem.value)}</span>{q}</div>'
    if isinstance(elem, model.MultiLanguageProperty):
        txt = "; ".join(f"{k}: {v}" for k, v in (elem.value or {}).items())
        return f'<div class="row"><span class="k">{name}</span>{sem_html}<span class="v">{esc(txt)}</span></div>'
    if isinstance(elem, model.File):
        extra = size = ""
        data = read_supp(files, elem.value) if elem.value else None
        if data:
            size = f' <span class="sz">({len(data):,} B)</span>'
            if elem.value.lower().endswith(".png"):
                extra = f'<br><img class="thumb" src="data:image/png;base64,{base64.b64encode(data).decode()}">'
        return (
            f'<div class="row"><span class="k">{name}</span>{sem_html}'
            f'<span class="v file">{esc(elem.value) or "—"}{size}</span>{extra}</div>'
        )
    if isinstance(elem, model.ReferenceElement):
        return f'<div class="row"><span class="k">{name}</span>{sem_html}<span class="v">{esc(_ref_str(elem.value))}</span></div>'
    if isinstance(elem, model.RelationshipElement):
        second = _ref_str(elem.second)
        link = ""
        if second in asset_to_aas:
            tgt = asset_to_aas[second]
            link = f' → <a href="#{anchor(tgt)}" onclick="show3d(\'{anchor(tgt)}\')">{esc(tgt.split(":")[-1][:12])}…</a>'
        return f'<div class="row rel"><span class="k">{name}</span>{sem_html}<span class="v">{esc(second)}{link}</span></div>'

    kids = children_of(elem)
    inner = "".join(render_element(c, files, asset_to_aas, name_to_mesh) for c in kids)
    badge = f'<span class="badge">{type(elem).__name__}</span>'
    ga = ""
    disp = _elem_disp(elem)
    # BOM nodes carry the part name as displayName — show that (idShort is NodeN).
    shown = (
        f'<span class="k" title="{name}">{esc(disp)}</span>'
        if disp
        else f'<span class="k">{name}</span>'
    )
    if isinstance(elem, model.Entity) and elem.global_asset_id:
        gid = elem.global_asset_id
        tgt = (
            f' <a href="#{anchor(asset_to_aas[gid])}" onclick="show3d(\'{anchor(asset_to_aas[gid])}\')">↗</a>'
            if gid in asset_to_aas
            else ""
        )
        ga = f'<span class="ga">{esc(gid.split(":")[-1][:14])}…{tgt}</span>'
    elif isinstance(elem, model.Entity) and disp and name_to_mesh:
        # Co-managed BOM node (single mode): link its 3D via the part's file mesh.
        key3d = name_to_mesh.get(disp)
        if key3d:
            ga = f'<a class="see3d" data-k3d="{key3d}" data-l3d="{esc(disp)}">◈ 3D</a>'
    return (
        f"<details open><summary>{shown}{badge}{sem_html}{ga}"
        f'<span class="cnt">{len(kids)}</span></summary>'
        f'<div class="nest">{inner}</div></details>'
    )


def _ref_str(ref) -> str:
    try:
        return ref.key[-1].value
    except Exception:  # noqa: BLE001
        return str(ref)


# --- page assembly ----------------------------------------------------------


def build_html(aasx_path, store, files, meshes) -> str:
    shells = [o for o in store if isinstance(o, model.AssetAdministrationShell)]
    submodels = [o for o in store if isinstance(o, model.Submodel)]
    asset_to_aas = {
        s.asset_information.global_asset_id: s.id
        for s in shells
        if s.asset_information.global_asset_id
    }

    supp = list(files)
    n_stp = sum(n.endswith(".stp") for n in supp)
    n_png = sum(n.endswith(".png") for n in supp)

    def is_assembly(s):
        return any(
            (ref.resolve(store) or model.Submodel("x")).id_short == "HierarchicalStructures"
            for ref in s.submodel
        )

    # Collect every SameAs target so we can tell a root assembly (not referenced by
    # any other BOM) from a sub-assembly (referenced as a child).
    sameas_targets: set[str] = set()
    for sm in submodels:
        if sm.id_short != "HierarchicalStructures":
            continue
        for el in _walk(sm):
            if isinstance(el, model.RelationshipElement) and (el.id_short or "").startswith(
                "SameAs"
            ):
                try:
                    sameas_targets.add(el.second.key[-1].value)
                except Exception:  # noqa: BLE001
                    pass

    def level(s):  # 0 root assembly, 1 sub-assembly, 2 part
        if not is_assembly(s):
            return 2
        return 1 if s.asset_information.global_asset_id in sameas_targets else 0

    shells.sort(key=lambda s: (level(s), (disp_name(s) or "")))

    icon = {0: "⛓ ", 1: "▸ ", 2: "· "}
    nav = "".join(
        f'<a href="#{anchor(s.id)}" onclick="show3d(\'{anchor(s.id)}\')">'
        f"{icon[level(s)] if level(s) != 2 or anchor(s.id) not in meshes else '◈ '}"
        f"{esc(disp_name(s))}</a>"
        for s in shells
    )
    cards = "".join(render_aas_card(s, store, files, asset_to_aas, meshes) for s in shells)
    # Default to the assembly (composed) view if present, else the first part.
    default = next(
        (anchor(s.id) for s in shells if is_assembly(s) and anchor(s.id) in meshes), ""
    ) or next((anchor(s.id) for s in shells if anchor(s.id) in meshes), "")

    data = json.dumps({"meshes": meshes, "default": default})
    title = esc(pathlib.Path(aasx_path).name)
    head = _HEAD.replace("__TITLE__", title)
    stats = (
        f"{len(shells)} AAS · {len(submodels)} submodels · "
        f"{n_stp} STEP + {n_png} preview · {len(meshes)} 3D meshes"
    )
    body = (
        _BODY.replace("__TITLE__", title)
        .replace("__STATS__", stats)
        .replace("__NAV__", nav)
        .replace("__CARDS__", cards)
    )
    return (
        head
        + f"<script>window.VIEWER_DATA={data};</script>"
        + body
        + f"<script>{_JS}</script></body></html>"
    )


def render_aas_card(shell, store, files, asset_to_aas, meshes) -> str:
    submodels = [s for s in (ref.resolve(store) for ref in shell.submodel) if s is not None]
    thumb = ""
    for sm in submodels:
        if sm.id_short != "Models3D":
            continue
        for el in _walk(sm):
            if isinstance(el, model.File) and el.id_short == "PreviewFile" and el.value:
                data = read_supp(files, el.value)
                if data:
                    thumb = f'<img class="hero" src="data:image/png;base64,{base64.b64encode(data).decode()}">'
                break
        if thumb:
            break

    has3d = anchor(shell.id) in meshes
    btn = (
        f'<button class="btn3d" onclick="show3d(\'{anchor(shell.id)}\')">◈ 3D 보기</button>'
        if has3d
        else ""
    )

    # part name → file-mesh key, from this AAS's Model3D entries (Title ↔ DigitalFile).
    # Lets co-managed BOM nodes (single mode, no part AAS) link to their 3D.
    name_to_mesh: dict[str, str] = {}
    for sm in submodels:
        if sm.id_short != "Models3D":
            continue
        for sml in sm.submodel_element:
            # Entries live in the "Model3D" SML (structural — basyx replaces the
            # entries' empty idShorts with generated ones on AASX read).
            if not isinstance(sml, model.SubmodelElementList) or sml.id_short != "Model3D":
                continue
            for entry in sml.value:
                title = df = None
                for el in _walk(entry):
                    if isinstance(el, model.MultiLanguageProperty) and el.id_short == "Title":
                        title = (el.value or {}).get("en") if el.value else None
                    elif isinstance(el, model.File) and el.id_short == "DigitalFile" and el.value:
                        df = el.value
                if title and df and file_key(df) in meshes:
                    name_to_mesh.setdefault(title, file_key(df))

    sm_blocks = "".join(
        f'<details open class="sm"><summary><b>{esc(sm.id_short)}</b>'
        f'<span class="sem">{esc(sem_of(sm))}</span></summary>'
        f'<div class="nest">{"".join(render_element(e, files, asset_to_aas, name_to_mesh) for e in sm.submodel_element)}</div></details>'
        for sm in sorted(submodels, key=lambda s: s.id_short or "")
    )
    return (
        f'<div class="card" id="{anchor(shell.id)}">'
        f'<div class="cardhead"><div>'
        f"<h2>{esc(disp_name(shell))}</h2>"
        f'<div class="ids"><code>{esc(shell.id)}</code><br>'
        f'<span class="assetkind">{esc(shell.asset_information.asset_kind)}</span> '
        f"<code>{esc(shell.asset_information.global_asset_id)}</code></div>{btn}"
        f"</div>{thumb}</div>{sm_blocks}</div>"
    )


# --- static HTML/CSS/JS -----------------------------------------------------

_HEAD = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__ — stp2aas viewer</title>
<style>
:root{--bg:#f6f7f9;--fg:#1c2126;--mut:#6b7684;--card:#fff;--line:#e3e7ec;--acc:#2d6cdf;--prov:#8a5a00;--provbg:#fff3d6;--vp:#0e1620}
@media(prefers-color-scheme:dark){:root{--bg:#12161b;--fg:#e6e9ee;--mut:#9aa4b0;--card:#1a2027;--line:#2a323b;--acc:#5b9bff;--prov:#e7b64b;--provbg:#3a2f10;--vp:#0a0f16}}
*{box-sizing:border-box}html,body{margin:0;height:100%}body{background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,Segoe UI,sans-serif}
header{position:sticky;top:0;background:var(--card);border-bottom:1px solid var(--line);padding:10px 18px;z-index:10}
header h1{font-size:15px;margin:0 0 2px}.stats{color:var(--mut);font-size:12px}
.layout{display:flex;gap:14px;padding:14px;align-items:flex-start}
nav{position:sticky;top:64px;flex:0 0 210px;max-height:88vh;overflow:auto;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:8px}
nav a{display:block;padding:4px 8px;color:var(--fg);text-decoration:none;border-radius:6px;font-size:12.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
nav a:hover{background:var(--bg)}nav a.sel{background:var(--acc);color:#fff}
main{flex:1 1 45%;min-width:0}
.viewport{position:sticky;top:64px;flex:1 1 40%;height:88vh;background:linear-gradient(180deg,#eef2f6,#c4cfdb);border:1px solid var(--line);border-radius:12px;overflow:hidden;display:flex;flex-direction:column}
.vphead{color:#33404e;font-size:12px;padding:8px 12px;border-bottom:1px solid #ffffff55;display:flex;justify-content:space-between;background:#ffffff66}
.vptools{display:flex;flex-wrap:wrap;gap:4px;align-items:center;padding:6px 10px;background:#ffffff66;border-bottom:1px solid #ffffff55;font-size:12px;color:#33404e}
.vptools button{background:#ffffffcc;border:1px solid #adb8c6;border-radius:5px;padding:3px 8px;cursor:pointer;font-size:11.5px;color:#233}
.vptools button:hover{background:#fff}
.vptools select,.vptools input[type=range]{font-size:11px}
.vptools input[type=range]{width:90px;vertical-align:middle}
#glcanvas{flex:1;width:100%;display:block;cursor:grab}#glcanvas:active{cursor:grabbing}
.vphint{color:#4a5766;font-size:11px;padding:6px 12px;background:#ffffff55}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;margin-bottom:14px;scroll-margin-top:70px}
.cardhead{display:flex;justify-content:space-between;gap:16px;align-items:flex-start}
.card h2{margin:0 0 6px;font-size:18px;color:var(--acc)}
.ids code{font-size:11px;color:var(--mut);word-break:break-all}
.assetkind{font-size:11px;background:var(--bg);border:1px solid var(--line);border-radius:4px;padding:0 5px}
.btn3d{margin-top:8px;background:var(--acc);color:#fff;border:0;border-radius:6px;padding:5px 10px;cursor:pointer;font-size:12px}
.hero{width:110px;height:110px;object-fit:contain;border:1px solid var(--line);border-radius:8px;background:#fff}
details{margin:6px 0}summary{cursor:pointer;user-select:none}
.sm>summary{font-size:14px;padding:4px 0;border-top:1px solid var(--line);margin-top:8px}
.nest{padding-left:14px;border-left:2px solid var(--line);margin-left:4px}
.row{display:flex;gap:8px;align-items:center;padding:2px 0;flex-wrap:wrap}
.k{font-weight:600;min-width:118px}.v{color:var(--mut)}.v.file{color:var(--acc);word-break:break-all}
.sem{color:var(--mut);font-size:11px;font-family:ui-monospace,monospace}.cnt{color:var(--mut);font-size:11px;margin-left:6px}
.badge{font-size:10px;background:var(--bg);border:1px solid var(--line);border-radius:4px;padding:0 5px;margin-left:6px;color:var(--mut)}
.see3d{color:var(--acc);cursor:pointer;font-size:11px;margin-left:6px;user-select:none}
.see3d:hover{text-decoration:underline}
.prov{font-size:10px;background:var(--provbg);color:var(--prov);border-radius:4px;padding:0 5px}
.sz{color:var(--mut);font-size:11px}.thumb{max-width:170px;border:1px solid var(--line);border-radius:6px;margin:4px 0}
.rel .v{color:var(--acc)}a{color:var(--acc)}.ga{font-size:11px;color:var(--mut);font-family:ui-monospace,monospace;margin-left:8px}
@media(max-width:1100px){.viewport{position:static;height:60vh;flex-basis:100%}.layout{flex-wrap:wrap}}
</style></head><body>"""

_BODY = """<header><h1>📦 __TITLE__</h1><div class="stats">__STATS__</div></header>
<div class="layout">
<nav>__NAV__</nav>
<main>__CARDS__</main>
<div class="viewport"><div class="vphead"><span id="vptitle">3D</span><span id="vptris"></span></div>
<div class="vptools">
<button onclick="preset('iso')">Iso</button><button onclick="preset('front')">Front</button>
<button onclick="preset('top')">Top</button><button onclick="preset('right')">Right</button>
<button onclick="fitView()">Fit</button><button id="wirebtn" onclick="toggleWire()">Wire</button>
<span style="margin-left:6px">✂ Clip
<select id="clipax" onchange="updateClip()"><option value="0">Off</option><option value="1">X</option><option value="2">Y</option><option value="3">Z</option></select></span>
<input id="clipsl" type="range" min="0" max="1" step="0.005" value="0.5" oninput="updateClip()">
<label><input id="clipflip" type="checkbox" onchange="updateClip()"> flip</label>
<span style="margin-left:6px">투명도 <input id="opac" type="range" min="0.1" max="1" step="0.05" value="1" oninput="setOpac()"></span>
</div>
<canvas id="glcanvas"></canvas>
<div class="vphint">드래그: 회전 · 휠: 확대/축소 · Iso/Front/Top/Right·Fit·Wire·Clip 지원 · 좌측 목록으로 부품 선택</div></div>
</div>"""

_JS = r"""
const D=window.VIEWER_DATA, cv=document.getElementById('glcanvas');
const gl=cv.getContext('webgl',{antialias:true});
function dec(s,T){const b=atob(s),u=new Uint8Array(b.length);for(let i=0;i<b.length;i++)u[i]=b.charCodeAt(i);return new T(u.buffer);}
function prog(){const vs=`attribute vec3 p;attribute vec3 n;attribute vec3 c;uniform mat4 mvp;uniform mat4 mv;varying vec3 vn;varying vec3 vp;varying vec3 vc;varying vec3 vw;void main(){vn=mat3(mv)*n;vp=(mv*vec4(p,1.)).xyz;vc=c;vw=p;gl_Position=mvp*vec4(p,1.);}`;
const fs=`precision mediump float;varying vec3 vn;varying vec3 vp;varying vec3 vc;varying vec3 vw;uniform float clipOn;uniform vec3 clipN;uniform float clipT;uniform float clipS;uniform float wire;uniform float alpha;uniform float flatc;void main(){if(clipOn>.5&&clipS*(dot(vw,clipN)-clipT)>0.0)discard;if(flatc>.5){gl_FragColor=vec4(vc,1.0);return;}if(wire>.5){gl_FragColor=vec4(.12,.14,.16,1.);return;}vec3 N=normalize(vn);if(!gl_FrontFacing)N=-N;vec3 L=normalize(vec3(.4,.6,1.));float d=max(dot(N,L),0.)*.8+.28;vec3 c=vc*d;float rim=pow(1.-max(dot(N,normalize(-vp)),0.),3.)*.2;gl_FragColor=vec4(c+rim,alpha);}`;
function sh(t,s){const o=gl.createShader(t);gl.shaderSource(o,s);gl.compileShader(o);return o;}
const pr=gl.createProgram();gl.attachShader(pr,sh(gl.VERTEX_SHADER,vs));gl.attachShader(pr,sh(gl.FRAGMENT_SHADER,fs));gl.linkProgram(pr);return pr;}
const PR=prog();gl.useProgram(PR);gl.enable(gl.DEPTH_TEST);
const A={p:gl.getAttribLocation(PR,'p'),n:gl.getAttribLocation(PR,'n'),c:gl.getAttribLocation(PR,'c')};
const U={mvp:gl.getUniformLocation(PR,'mvp'),mv:gl.getUniformLocation(PR,'mv'),clipOn:gl.getUniformLocation(PR,'clipOn'),clipN:gl.getUniformLocation(PR,'clipN'),clipT:gl.getUniformLocation(PR,'clipT'),clipS:gl.getUniformLocation(PR,'clipS'),wire:gl.getUniformLocation(PR,'wire'),alpha:gl.getUniformLocation(PR,'alpha'),flatc:gl.getUniformLocation(PR,'flatc')};
let bp=gl.createBuffer(),bn=gl.createBuffer(),bc=gl.createBuffer(),bi=gl.createBuffer(),bw=gl.createBuffer(),blp=gl.createBuffer(),blc=gl.createBuffer(),nIdx=0,nWire=0,nLine=0,cur=null,curI=null,wireOn=false;
let clip={on:0,N:[1,0,0],T:0,S:1};
let opacity=1;
let cam={az:0.7,el:0.5,dist:3,cx:0,cy:0,cz:0,r:1};
// mat4 helpers (column-major)
function mul(a,b){const o=new Float32Array(16);for(let c=0;c<4;c++)for(let r=0;r<4;r++){let s=0;for(let k=0;k<4;k++)s+=a[k*4+r]*b[c*4+k];o[c*4+r]=s;}return o;}
function persp(f,as,n,fa){const t=1/Math.tan(f/2),o=new Float32Array(16);o[0]=t/as;o[5]=t;o[10]=(fa+n)/(n-fa);o[11]=-1;o[14]=2*fa*n/(n-fa);return o;}
function look(e,c,u){let z=nrm(sub(e,c)),x=nrm(cross(u,z)),y=cross(z,x);return new Float32Array([x[0],y[0],z[0],0,x[1],y[1],z[1],0,x[2],y[2],z[2],0,-dot(x,e),-dot(y,e),-dot(z,e),1]);}
function sub(a,b){return[a[0]-b[0],a[1]-b[1],a[2]-b[2]];}function cross(a,b){return[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];}
function dot(a,b){return a[0]*b[0]+a[1]*b[1]+a[2]*b[2];}function nrm(a){let l=Math.hypot(a[0],a[1],a[2])||1;return[a[0]/l,a[1]/l,a[2]/l];}
function resize(){const d=Math.min(devicePixelRatio||1,2);cv.width=cv.clientWidth*d;cv.height=cv.clientHeight*d;gl.viewport(0,0,cv.width,cv.height);}
function draw(){resize();gl.clearColor(0,0,0,0);gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);if(!nIdx&&!nLine)return;
if(opacity<0.999){gl.enable(gl.BLEND);gl.blendFunc(gl.SRC_ALPHA,gl.ONE_MINUS_SRC_ALPHA);gl.depthMask(false);}else{gl.disable(gl.BLEND);gl.depthMask(true);}
gl.uniform1f(U.alpha,opacity);
const ce=Math.cos(cam.el),eye=[cam.cx+cam.dist*cam.r*ce*Math.sin(cam.az),cam.cy+cam.dist*cam.r*Math.sin(cam.el),cam.cz+cam.dist*cam.r*ce*Math.cos(cam.az)];
const V=look(eye,[cam.cx,cam.cy,cam.cz],[0,1,0]);const P=persp(0.9,cv.width/cv.height,cam.r*0.05,cam.r*100);
const MV=V,MVP=mul(P,V);gl.uniformMatrix4fv(U.mvp,false,MVP);gl.uniformMatrix4fv(U.mv,false,MV);
gl.uniform1f(U.clipOn,clip.on);gl.uniform3fv(U.clipN,clip.N);gl.uniform1f(U.clipT,clip.T);gl.uniform1f(U.clipS,clip.S);
gl.bindBuffer(gl.ARRAY_BUFFER,bp);gl.enableVertexAttribArray(A.p);gl.vertexAttribPointer(A.p,3,gl.FLOAT,false,0,0);
gl.bindBuffer(gl.ARRAY_BUFFER,bn);gl.enableVertexAttribArray(A.n);gl.vertexAttribPointer(A.n,3,gl.FLOAT,false,0,0);
gl.bindBuffer(gl.ARRAY_BUFFER,bc);gl.enableVertexAttribArray(A.c);gl.vertexAttribPointer(A.c,3,gl.UNSIGNED_BYTE,true,0,0);
gl.uniform1f(U.flatc,0);
if(wireOn&&nIdx){gl.uniform1f(U.wire,1);gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER,bw);gl.drawElements(gl.LINES,nWire,gl.UNSIGNED_INT,0);}
else if(nIdx){gl.uniform1f(U.wire,0);gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER,bi);gl.drawElements(gl.TRIANGLES,nIdx,gl.UNSIGNED_INT,0);}
if(nLine){gl.uniform1f(U.flatc,1);gl.bindBuffer(gl.ARRAY_BUFFER,blp);gl.enableVertexAttribArray(A.p);gl.vertexAttribPointer(A.p,3,gl.FLOAT,false,0,0);gl.bindBuffer(gl.ARRAY_BUFFER,blc);gl.enableVertexAttribArray(A.c);gl.vertexAttribPointer(A.c,3,gl.UNSIGNED_BYTE,true,0,0);gl.disableVertexAttribArray(A.n);gl.drawArrays(gl.LINES,0,nLine);gl.uniform1f(U.flatc,0);}
}
const extU=gl.getExtension('OES_element_index_uint');
function show3d(a,lbl){const m=D.meshes[a];document.querySelectorAll('nav a').forEach(x=>x.classList.remove('sel'));
if(!m){document.getElementById('vptitle').textContent='(이 AAS에는 3D 형상이 없음)';document.getElementById('vptris').textContent='';nIdx=0;draw();return;}
const P=dec(m.p,Float32Array),N=dec(m.n,Float32Array),C=dec(m.vc,Uint8Array),I=dec(m.i,Uint32Array),LP=m.lp?dec(m.lp,Float32Array):new Float32Array(0),LC=m.lc?dec(m.lc,Uint8Array):new Uint8Array(0);
gl.bindBuffer(gl.ARRAY_BUFFER,bp);gl.bufferData(gl.ARRAY_BUFFER,P,gl.STATIC_DRAW);
gl.bindBuffer(gl.ARRAY_BUFFER,bn);gl.bufferData(gl.ARRAY_BUFFER,N,gl.STATIC_DRAW);
gl.bindBuffer(gl.ARRAY_BUFFER,bc);gl.bufferData(gl.ARRAY_BUFFER,C,gl.STATIC_DRAW);
gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER,bi);gl.bufferData(gl.ELEMENT_ARRAY_BUFFER,I,gl.STATIC_DRAW);
gl.bindBuffer(gl.ARRAY_BUFFER,blp);gl.bufferData(gl.ARRAY_BUFFER,LP,gl.STATIC_DRAW);
gl.bindBuffer(gl.ARRAY_BUFFER,blc);gl.bufferData(gl.ARRAY_BUFFER,LC,gl.STATIC_DRAW);nLine=LP.length/3;
nIdx=I.length;curI=I;wireOn=false;nWire=0;document.getElementById('wirebtn').style.background='';document.getElementById('wirebtn').style.color='';
cam.cx=m.c[0];cam.cy=m.c[1];cam.cz=m.c[2];cam.r=m.r;cam.dist=2.6;cur=a;
if(clip.on)updateClip();
document.getElementById('vptitle').textContent='◈ '+(lbl||(document.getElementById(a)?document.querySelector('#'+a+' h2').textContent:a));
document.getElementById('vptris').textContent=m.tris.toLocaleString()+' tris';draw();}
window.show3d=show3d;
function preset(v){const P={iso:[0.7,0.5],front:[0,0],top:[0,1.55],right:[1.5708,0]}[v];if(P){cam.az=P[0];cam.el=P[1];draw();}}
function fitView(){cam.dist=2.6;draw();}
function toggleWire(){wireOn=!wireOn;const b=document.getElementById('wirebtn');b.style.background=wireOn?'#2d6cdf':'';b.style.color=wireOn?'#fff':'';
if(wireOn&&!nWire&&curI){const L=new Uint32Array(curI.length*2);let j=0;for(let k=0;k<curI.length;k+=3){const a=curI[k],b2=curI[k+1],c=curI[k+2];L[j++]=a;L[j++]=b2;L[j++]=b2;L[j++]=c;L[j++]=c;L[j++]=a;}nWire=L.length;gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER,bw);gl.bufferData(gl.ELEMENT_ARRAY_BUFFER,L,gl.STATIC_DRAW);}
draw();}
function setOpac(){opacity=+document.getElementById('opac').value;draw();}
window.setOpac=setOpac;
function updateClip(){const ax=+document.getElementById('clipax').value;if(!ax){clip.on=0;draw();return;}
const t=+document.getElementById('clipsl').value,flip=document.getElementById('clipflip').checked;
const nvec=[[0,0,0],[1,0,0],[0,1,0],[0,0,1]][ax],ctr=[cam.cx,cam.cy,cam.cz][ax-1];
clip.on=1;clip.N=nvec;clip.T=(ctr-cam.r)+t*2*cam.r;clip.S=flip?-1:1;draw();}
window.preset=preset;window.fitView=fitView;window.toggleWire=toggleWire;window.updateClip=updateClip;
let drag=false,px=0,py=0;
cv.addEventListener('mousedown',e=>{drag=true;px=e.clientX;py=e.clientY;});
addEventListener('mouseup',()=>drag=false);
addEventListener('mousemove',e=>{if(!drag)return;cam.az-=(e.clientX-px)*.01;cam.el=Math.max(-1.5,Math.min(1.5,cam.el+(e.clientY-py)*.01));px=e.clientX;py=e.clientY;draw();});
cv.addEventListener('wheel',e=>{e.preventDefault();cam.dist*=(e.deltaY>0?1.1:0.9);cam.dist=Math.max(.3,Math.min(30,cam.dist));draw();},{passive:false});
addEventListener('resize',draw);
document.addEventListener('click',e=>{const t=e.target.closest('[data-k3d]');if(t)show3d(t.dataset.k3d,t.dataset.l3d);});
if(D.default)show3d(D.default);else draw();
"""


def open_in_browser(path) -> None:
    """Open a local file with its default app (os.startfile is reliable on Windows
    for spaces/unicode; webbrowser file:// URIs can fail to load)."""
    p = str(pathlib.Path(path).resolve())
    try:
        if sys.platform == "win32":
            import os

            os.startfile(p)  # noqa: S606 — our own generated html
            return
    except Exception:  # noqa: BLE001
        pass
    import webbrowser

    webbrowser.open(pathlib.Path(p).as_uri())


def render(aasx_path, out_html=None, enable_3d=True, open_browser=False):
    """Build the HTML viewer for an .aasx. Returns (html_path, mesh_count)."""
    store, files = load(aasx_path)
    meshes = build_meshes(store, files, enable=enable_3d)
    out = out_html or str(pathlib.Path(aasx_path).with_suffix(".html"))
    pathlib.Path(out).write_text(build_html(aasx_path, store, files, meshes), encoding="utf-8")
    if open_browser:
        open_in_browser(out)
    return out, len(meshes)


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):  # unicode-safe console (cp949 etc.)
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    p = argparse.ArgumentParser(
        prog="stp2aas.viewer",
        add_help=False,  # no -h/--help; running with no arguments prints this help
        description="Render an .aasx into a self-contained interactive 3D HTML viewer.",
        usage="view.bat --input <file.aasx> [--output <file.html>] [options]",
        epilog=(
            "examples (via view.bat):\n"
            "  view.bat --input out\\model.aasx --open\n"
            "  view.bat --input out\\model.aasx --output out\\view.html\n"
            "  view.bat --input out\\big.aasx --no-3d --open   (structure only, faster)\n"
            "\nnotes:\n"
            "  --output is optional; it defaults to <input>.html beside the input.\n"
            "  positional forms also work:  view.bat out\\model.aasx out\\view.html\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("-i", "--input", dest="input", help="input .aasx package")
    p.add_argument(
        "-o",
        "--output",
        dest="output",
        help="output .html (default: <input>.html beside the input)",
    )
    p.add_argument("--open", action="store_true", help="open the viewer in the default browser")
    p.add_argument("--no-3d", action="store_true", help="structure only (skip 3D tessellation)")
    p.add_argument("pos_input", nargs="?", default=None, help=argparse.SUPPRESS)
    p.add_argument("pos_output", nargs="?", default=None, help=argparse.SUPPRESS)
    args = p.parse_args(argv)

    aasx = args.input or args.pos_input
    if not aasx:
        p.print_help()  # no input → show full help
        return 0
    out, n = render(
        aasx, args.output or args.pos_output, enable_3d=not args.no_3d, open_browser=args.open
    )
    print(f"wrote {out}  ({n} 3D meshes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
