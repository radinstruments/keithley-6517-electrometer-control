"""Build high-density PNG variants from the vendored Codicon SVG sources.

Run only when the SVG set or palette changes. svglib/reportlab are build-time
tools; the application itself needs only Pillow, which CustomTkinter also uses.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image
from reportlab.graphics import renderPM
from svglib.svglib import svg2rlg


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "assets" / "icons" / "codicons" / "svg"
OUTPUT_DIR = ROOT / "assets" / "icons" / "codicons" / "png"
VARIANTS = {
    "light": "#4D5C6A",
    "light_active": "#17212B",
    "dark": "#A6A6A6",
    "dark_active": "#FFFFFF",
}
CANVAS_SIZE = 48
GLYPH_SIZE = 40


def build() -> None:
    for variant, color in VARIANTS.items():
        destination = OUTPUT_DIR / variant
        destination.mkdir(parents=True, exist_ok=True)
        for source in sorted(SOURCE_DIR.glob("*.svg")):
            svg = source.read_text(encoding="utf-8").replace("currentColor", color)
            drawing = svg2rlg(BytesIO(svg.encode("utf-8")))
            if drawing is None:
                raise RuntimeError(f"Could not parse {source}")
            drawing.scale(GLYPH_SIZE / drawing.width, GLYPH_SIZE / drawing.height)
            drawing.width = GLYPH_SIZE
            drawing.height = GLYPH_SIZE
            glyph = renderPM.drawToPIL(
                drawing,
                dpi=72,
                bg=None,
                backendFmt="RGBA",
            ).convert("RGBA")
            canvas = Image.new("RGBA", (CANVAS_SIZE, CANVAS_SIZE), (0, 0, 0, 0))
            offset = (CANVAS_SIZE - GLYPH_SIZE) // 2
            canvas.alpha_composite(glyph, (offset, offset))
            canvas.save(destination / f"{source.stem}.png", optimize=True)


if __name__ == "__main__":
    build()
