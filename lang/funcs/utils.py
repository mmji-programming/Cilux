from lang.io.utils import (
    base_dir,
)
from lang.io.path_op import path_check, PathOp

import math
from pathlib import Path
from PIL import Image, ImageFont, ImageDraw

BASE_DIR = base_dir()
FONTS_DIR = BASE_DIR / "share" / "fonts"
DEFAULT_FONT = FONTS_DIR / "Inconsolata-Regular.ttf"

SUPPORTED_TEXT_EXTENSIONS = {".svg", ".txt", ".pdf"}
SUPPORTED_IMAGE_EXTENSIONS = {".png"}
SUPPORTED_EXTENSIONS = SUPPORTED_TEXT_EXTENSIONS | SUPPORTED_IMAGE_EXTENSIONS


def _font_size(max_len: int) -> int:
    if max_len > 250:
        return 9
    if max_len > 150:
        return 11
    if max_len > 80:
        return 13
    if max_len > 40:
        return 15
    return 17


def to_svg(text: str, filename: str | Path) -> Path:

    path = path_check(filename, PathOp.HAS_EXTENSION)
    path = path_check(path, PathOp.MATCH_EXTENSION, ext=".svg")
    path_check(path, PathOp.PARENT_EXISTS)

    lines = text.split("\n")
    max_len = max(len(line) for line in lines)
    font_size = _font_size(max_len)
    char_w = font_size * 0.6
    line_h = font_size
    pad_x = 10
    pad_y = 10
    width = math.ceil(max_len * char_w + 2 * pad_x)
    height = math.ceil(len(lines) * line_h + 2 * pad_y)

    rows = []
    for i, line in enumerate(lines):
        y = pad_y + font_size * 0.8 + i * line_h
        escaped = (line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")) or "&#160;"
        rows.append(f'    <tspan x="{pad_x}" y="{y}">{escaped}</tspan>')

    svg = f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg"
     width="100%" height="100%"
     viewBox="0 0 {width} {height}"
     preserveAspectRatio="xMidYMid meet">
  <style>
    text {{
      font-family: 'Consolas', 'Courier New', 'Liberation Mono', monospace;
      font-size: {font_size}px;
    }}
    .wave-text {{ white-space: pre; text-rendering: geometricPrecision; }}
  </style>
  <rect width="100%" height="100%" fill="white"/>
  <text class="wave-text" fill="black" xml:space="preserve">
{chr(10).join(rows)}
  </text>
</svg>"""

    path.write_text(svg, encoding="utf-8")
    return path


def to_png(text: str, filename: str | Path, font: str | None = None) -> Path:

    path = path_check(filename, PathOp.HAS_EXTENSION)
    path = path_check(path, PathOp.MATCH_EXTENSION, ext=".png")
    path_check(path, PathOp.PARENT_EXISTS)

    font_path = DEFAULT_FONT if font is None else (FONTS_DIR / font)
    lines = text.split("\n")
    max_len = max(len(line) for line in lines)
    scale = 3
    font_size = _font_size(max_len) * scale
    pil_font = ImageFont.truetype(str(font_path), font_size)
    char_w = pil_font.getbbox("─")[2]
    line_h = font_size
    width = max_len * char_w + 20 * scale
    height = len(lines) * line_h + 10 * scale

    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)
    y = 5 * scale

    for line in lines:
        draw.text((10 * scale, y), line, font=pil_font, fill="black")
        y += line_h

    img.save(str(path), dpi=(300, 300))
    return path
