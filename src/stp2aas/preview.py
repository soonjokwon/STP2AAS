"""S15 / D6: offscreen preview rendering with graceful fallback.

Must never crash the pipeline: on any rendering failure, emit a solid-color
placeholder PNG (512x512) and log a warning. (CLAUDE.md gotchas, D6/D7.)
"""

from __future__ import annotations

import logging

logger = logging.getLogger("stp2aas.preview")


def render_preview(shape, out_png: str, size: int = 512) -> str:
    """Render `shape` to a PNG (C11). Falls back to a placeholder on any failure.

    Returns the written path (always `out_png`).
    """
    if shape is not None:
        try:
            _render_offscreen(shape, out_png, size)
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
        from OCC.Display.OCCViewer import Viewer3d

        viewer = Viewer3d()
        viewer.InitOffscreen(size, size)  # offscreen framebuffer (no on-screen window)
        viewer.SetModeShaded()
        viewer.set_bg_gradient_color([235, 238, 242], [200, 210, 220])
        _VIEWER = viewer
    return _VIEWER


def _render_offscreen(shape, out_png: str, size: int) -> None:
    """D6: pythonocc offscreen renderer (OpenGL). Raises on headless failure."""
    viewer = _get_viewer(size)
    viewer.EraseAll()
    viewer.DisplayShape(shape, update=True)
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
