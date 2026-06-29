"""Generate Hermes Voice icon raster assets from the app icon geometry."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "assets" / "icons"
CANVAS = 1024
RENDER_SCALE = 4

VOID_BLACK = (5, 5, 5, 255)
GRAPHITE = (17, 17, 19, 255)
SURFACE = (20, 20, 32, 255)
STARLIGHT = (245, 242, 234, 255)
WHITE = (255, 255, 255, 255)
IGNITION = (243, 154, 66, 255)
IGNITION_LIGHT = (255, 179, 106, 170)
MINT = (22, 230, 176, 255)


def _s(value: float) -> int:
    return round(value * RENDER_SCALE)


def _box(values: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
    return tuple(_s(value) for value in values)


def _circle(draw: ImageDraw.ImageDraw, cx: float, cy: float, radius: float, fill) -> None:
    draw.ellipse(_box((cx - radius, cy - radius, cx + radius, cy + radius)), fill=fill)


def _round_line(
    draw: ImageDraw.ImageDraw,
    points: list[tuple[float, float]],
    width: float,
    fill,
    joint: str | None = "curve",
) -> None:
    scaled = [(_s(x), _s(y)) for x, y in points]
    draw.line(scaled, fill=fill, width=_s(width), joint=joint)
    radius = width / 2
    _circle(draw, points[0][0], points[0][1], radius, fill)
    _circle(draw, points[-1][0], points[-1][1], radius, fill)


def _rounded_rect(
    draw: ImageDraw.ImageDraw,
    box: tuple[float, float, float, float],
    radius: float,
    fill,
) -> None:
    draw.rounded_rectangle(_box(box), radius=_s(radius), fill=fill)


def draw_icon() -> Image.Image:
    size = CANVAS * RENDER_SCALE
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))

    shadow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    _rounded_rect(shadow_draw, (58, 68, 966, 986), 220, (0, 0, 0, 185))
    shadow = shadow.filter(ImageFilter.GaussianBlur(_s(28)))
    image.alpha_composite(shadow)

    mask = Image.new("L", (size, size), 0)
    mask_draw = ImageDraw.Draw(mask)
    _rounded_rect(mask_draw, (48, 48, 976, 976), 220, 255)

    base = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    base_draw = ImageDraw.Draw(base)
    _rounded_rect(base_draw, (48, 48, 976, 976), 220, VOID_BLACK)
    _circle(base_draw, 512, 512, 405, GRAPHITE)
    _circle(base_draw, 512, 512, 318, SURFACE)
    tint = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    tint_draw = ImageDraw.Draw(tint)
    _circle(tint_draw, 696, 300, 260, (243, 154, 66, 22))
    _circle(tint_draw, 390, 700, 285, (22, 230, 176, 16))
    base.alpha_composite(tint)
    base.putalpha(mask)
    image.alpha_composite(base)

    art = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    art_draw = ImageDraw.Draw(art)

    trajectory_glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    trajectory_draw = ImageDraw.Draw(trajectory_glow)
    _round_line(trajectory_draw, [(245, 744), (790, 199)], 48, (245, 242, 234, 70))
    trajectory_glow = trajectory_glow.filter(ImageFilter.GaussianBlur(_s(10)))
    art.alpha_composite(trajectory_glow)
    _round_line(art_draw, [(245, 744), (790, 199)], 38, STARLIGHT)

    gate_glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gate_draw = ImageDraw.Draw(gate_glow)
    _round_line(gate_draw, [(342, 784), (342, 270), (682, 270), (682, 784)], 68, (255, 255, 255, 48))
    gate_glow = gate_glow.filter(ImageFilter.GaussianBlur(_s(12)))
    art.alpha_composite(gate_glow)
    _round_line(art_draw, [(342, 784), (342, 270), (682, 270), (682, 784)], 58, WHITE)

    bars_glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    bars_draw = ImageDraw.Draw(bars_glow)
    for box, color in (
        ((423, 414, 471, 610), STARLIGHT),
        ((488, 344, 536, 680), WHITE),
        ((553, 444, 601, 580), STARLIGHT),
    ):
        _rounded_rect(bars_draw, box, 24, (*color[:3], 120))
    bars_glow = bars_glow.filter(ImageFilter.GaussianBlur(_s(12)))
    art.alpha_composite(bars_glow)
    _rounded_rect(art_draw, (423, 414, 471, 610), 24, STARLIGHT)
    _rounded_rect(art_draw, (488, 344, 536, 680), 24, WHITE)
    _rounded_rect(art_draw, (553, 444, 601, 580), 24, STARLIGHT)

    ignition_glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ignition_draw = ImageDraw.Draw(ignition_glow)
    _circle(ignition_draw, 675, 314, 88, (243, 154, 66, 105))
    ignition_glow = ignition_glow.filter(ImageFilter.GaussianBlur(_s(18)))
    art.alpha_composite(ignition_glow)
    _circle(art_draw, 675, 314, 68, IGNITION)
    _circle(art_draw, 675, 314, 28, IGNITION_LIGHT)
    _circle(art_draw, 286, 274, 18, STARLIGHT)
    _circle(art_draw, 755, 718, 12, MINT)

    art.putalpha(Image.composite(art.getchannel("A"), Image.new("L", (size, size), 0), mask))
    image.alpha_composite(art)
    return image.resize((CANVAS, CANVAS), Image.Resampling.LANCZOS)


def save_assets() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    icon = draw_icon()
    icon.save(OUT_DIR / "app-icon.png")

    sizes = [16, 24, 32, 48, 64, 128, 256, 512]
    resized = []
    for size in sizes:
        item = icon.resize((size, size), Image.Resampling.LANCZOS)
        item.save(OUT_DIR / f"app-icon-{size}.png")
        if size <= 256:
            resized.append(item)

    resized[-1].save(
        OUT_DIR / "app-icon.ico",
        sizes=[(image.width, image.height) for image in resized],
        append_images=resized[:-1],
    )


if __name__ == "__main__":
    save_assets()
