"""Preview fallback does not need pythonocc (D6 placeholder)."""

from pathlib import Path

from step2aas.preview import render_preview


def test_placeholder_when_no_shape(tmp_path: Path):
    out = tmp_path / "preview.png"
    render_preview(None, str(out), size=64)
    assert out.is_file()
    from PIL import Image

    img = Image.open(out)
    assert img.size == (64, 64)
