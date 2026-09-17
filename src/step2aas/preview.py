"""S15 / D6: offscreen preview rendering with graceful fallback.

Must never crash the pipeline: on any rendering failure, emit a solid-color
placeholder PNG (512x512) and log a warning (D6/D7).

Default pythonocc `DisplayShape` uses Graphic3d_NOM_NEON_GNC (ionized/green)
and ignores STEP colours, so assemblies collapse to a muddy olive blob. We
override that: Plastic material, STEP RGB when present, and face-boundary
edges so adjacent parts stay readable even when they share a colour.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

logger = logging.getLogger("step2aas.preview")

# CAD-neutral grey when a shape has no STEP colour (S9).
_FALLBACK_RGB = (0.76, 0.78, 0.82)

ColoredShape = tuple[object, tuple[float, float, float] | None]


def render_preview(
    shape,
    out_png: str,
    size: int = 512,
    *,
    color: tuple[float, float, float] | None = None,
    colored_shapes: Sequence[ColoredShape] | None = None,
) -> str:
    """Render `shape` to a PNG (C11). Falls back to a placeholder on any failure.

    `colored_shapes` is an optional list of (TopoDS_Shape, RGB|None) so an
    assembly thumbnail can show each leaf in its STEP colour. When omitted,
    `shape` is drawn once with `color`.

    Returns the written path (always `out_png`).
    """
    items: list[ColoredShape] = list(colored_shapes) if colored_shapes else []
    if not items and shape is not None:
        items = [(shape, color)]
    if items:
        try:
            _render_offscreen(items, out_png, size)
            return out_png
        except Exception as exc:  # noqa: BLE001 — headless OpenGL can fail many ways
            logger.warning("offscreen render failed (%s); using placeholder for %s", exc, out_png)
    else:
        logger.warning("no shape to render; using placeholder for %s", out_png)
    _placeholder(out_png, size)
    return out_png


_VIEWER = None  # reused across parts so the GL pipe is initialised only once


def _get_viewer(size: int):
    global _VIEWER
    if _VIEWER is None:
        from OCC.Core.GeomAbs import GeomAbs_G2
        from OCC.Core.Graphic3d import Graphic3d_TOSM_FRAGMENT
        from OCC.Core.Quantity import Quantity_Color, Quantity_TOC_RGB
        from OCC.Display.OCCViewer import Viewer3d

        viewer = Viewer3d()
        viewer.InitOffscreen(size, size)  # offscreen framebuffer (no on-screen window)
        # InitOffscreen skips Viewer3d.Create(), so lights / edges / Phong must be
        # set here — otherwise the default material + no boundaries hide part splits.
        viewer.Viewer.SetDefaultLights()
        viewer.Viewer.SetLightOn()
        viewer.SetModeShaded()
        viewer.SetOrthographicProjection()
        viewer.View.SetShadingModel(Graphic3d_TOSM_FRAGMENT)
        drawer = viewer.Context.DefaultDrawer()
        viewer.default_drawer = drawer
        drawer.SetFaceBoundaryDraw(True)
        drawer.SetFaceBoundaryUpperContinuity(GeomAbs_G2)
        edge = drawer.FaceBoundaryAspect()
        edge.SetColor(Quantity_Color(0.16, 0.18, 0.22, Quantity_TOC_RGB))
        edge.SetWidth(1.4)
        try:
            viewer.EnableAntiAliasing()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Preview antialiasing unavailable: %s", exc)
        viewer.set_bg_gradient_color([235, 238, 242], [200, 210, 220])
        _VIEWER = viewer
    return _VIEWER


def _quantity_color(rgb: tuple[float, float, float] | None):
    from OCC.Core.Quantity import Quantity_Color, Quantity_TOC_RGB

    r, g, b = rgb if rgb is not None else _FALLBACK_RGB
    # part.color is XCAF Red/Green/Blue (linear). TOC_RGB keeps OCC Phong from
    # treating those components as sRGB (which shifts yellow toward olive).
    return Quantity_Color(float(r), float(g), float(b), Quantity_TOC_RGB)


def _render_offscreen(items: Sequence[ColoredShape], out_png: str, size: int) -> None:
    """D6: pythonocc offscreen renderer (OpenGL). Raises on headless failure."""
    from OCC.Core.Graphic3d import Graphic3d_NOM_PLASTIC

    viewer = _get_viewer(size)
    viewer.EraseAll()
    # Plastic instead of pythonocc's default NEON_GNC (ionized green).
    for shp, rgb in items:
        if shp is None:
            continue
        viewer.DisplayShape(
            shp,
            material=Graphic3d_NOM_PLASTIC,
            color=_quantity_color(rgb),
            update=False,
        )
    viewer.View_Iso()
    viewer.FitAll()
    viewer.ExportToImage(out_png)
    _normalize_size(out_png, size)


def _normalize_size(png_path: str, size: int) -> None:
    """Force exactly size×size (C11 "PNG 512²"); ExportToImage may use window size."""
    from PIL import Image

    with Image.open(png_path) as img:
        if img.size != (size, size):
            img.convert("RGB").resize((size, size)).save(png_path, format="PNG")


def _placeholder(out_png: str, size: int) -> None:
    """D6/D7: solid-color placeholder so PreviewFile[1] is always satisfiable."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (size, size), (230, 232, 236))
    draw = ImageDraw.Draw(img)
    # A simple framed box with a label so the placeholder is visually obvious.
    draw.rectangle([8, 8, size - 8, size - 8], outline=(120, 130, 140), width=3)
    draw.text((size // 2 - 60, size // 2 - 8), "no preview", fill=(90, 100, 110))
    img.save(out_png, format="PNG")
