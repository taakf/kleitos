"""
Generate Axion app icons (.ico for Windows, .icns for macOS, .png for all platforms).

Recreates the branded SVG design using pure Pillow:
  - Dark blue gradient rounded rectangle
  - Teal-to-blue gradient "K" letterform
  - Subtle chart line with dot accents

Usage:  python scripts/generate-icons.py
Output: assets/kleitos.ico, assets/kleitos-1024.png, assets/kleitos-256.png, etc.
"""

import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "Pillow", "-q"])
    from PIL import Image, ImageDraw, ImageFont

PROJECT_DIR = Path(__file__).resolve().parent.parent
ASSETS_DIR = PROJECT_DIR / "assets"
RESOURCES_DIR = PROJECT_DIR / "Kleitos.app" / "Contents" / "Resources"


def lerp_color(c1, c2, t):
    """Linear interpolate between two RGB tuples."""
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))


def draw_rounded_rect(draw, xy, radius, fill):
    """Draw a filled rounded rectangle."""
    x0, y0, x1, y1 = xy
    r = radius
    draw.rectangle([x0 + r, y0, x1 - r, y1], fill=fill)
    draw.rectangle([x0, y0 + r, x1, y1 - r], fill=fill)
    draw.pieslice([x0, y0, x0 + 2 * r, y0 + 2 * r], 180, 270, fill=fill)
    draw.pieslice([x1 - 2 * r, y0, x1, y0 + 2 * r], 270, 360, fill=fill)
    draw.pieslice([x0, y1 - 2 * r, x0 + 2 * r, y1], 90, 180, fill=fill)
    draw.pieslice([x1 - 2 * r, y1 - 2 * r, x1, y1], 0, 90, fill=fill)


def draw_gradient_bg(img, radius):
    """Draw the dark blue gradient background."""
    w, h = img.size
    pixels = img.load()
    mask = Image.new("L", (w, h), 0)
    mask_draw = ImageDraw.Draw(mask)
    draw_rounded_rect(mask_draw, (0, 0, w - 1, h - 1), radius, fill=255)
    mask_pixels = mask.load()

    c1 = (26, 26, 46)    # #1a1a2e
    c2 = (22, 33, 62)    # #16213e

    for y in range(h):
        for x in range(w):
            if mask_pixels[x, y] > 0:
                t = (x + y) / (w + h)
                r, g, b = lerp_color(c1, c2, t)
                pixels[x, y] = (r, g, b, 255)


def draw_accent_gradient_text(img, text, bbox, font):
    """Draw text with teal-to-blue gradient fill."""
    x0, y0, x1, y1 = bbox
    tw = x1 - x0
    th = y1 - y0
    if tw <= 0 or th <= 0:
        return

    text_img = Image.new("L", img.size, 0)
    text_draw = ImageDraw.Draw(text_img)
    text_draw.text((x0, y0), text, fill=255, font=font)

    c1 = (0, 212, 170)   # #00d4aa
    c2 = (0, 153, 255)   # #0099ff

    pixels = img.load()
    text_pixels = text_img.load()
    w, h = img.size

    for y in range(h):
        for x in range(w):
            if text_pixels[x, y] > 0:
                t = (x - x0 + y - y0) / max(tw + th, 1)
                t = max(0.0, min(1.0, t))
                r, g, b = lerp_color(c1, c2, t)
                alpha = text_pixels[x, y]
                er, eg, eb, ea = pixels[x, y]
                blend = alpha / 255.0
                pixels[x, y] = (
                    int(er * (1 - blend) + r * blend),
                    int(eg * (1 - blend) + g * blend),
                    int(eb * (1 - blend) + b * blend),
                    max(ea, alpha),
                )


def draw_chart_line(draw, img, size):
    """Draw the subtle chart polyline with accent gradient and dot accents."""
    s = size / 1024.0
    points = [
        (180 * s, 780 * s), (300 * s, 740 * s), (420 * s, 760 * s),
        (540 * s, 700 * s), (660 * s, 720 * s), (780 * s, 680 * s),
        (850 * s, 690 * s),
    ]

    c1 = (0, 212, 170)   # #00d4aa
    c2 = (0, 153, 255)   # #0099ff
    line_width = max(2, int(12 * s))

    for i in range(len(points) - 1):
        x1, y1 = points[i]
        x2, y2 = points[i + 1]
        t = i / (len(points) - 1)
        color = lerp_color(c1, c2, t)
        color_alpha = color + (128,)
        draw.line([(x1, y1), (x2, y2)], fill=color_alpha, width=line_width)

    dot_r = max(2, int(10 * s))
    for cx, cy, color, alpha in [
        (540 * s, 700 * s, (0, 212, 170), 204),
        (780 * s, 680 * s, (0, 153, 255), 204),
    ]:
        draw.ellipse(
            [cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r],
            fill=color + (alpha,),
        )


def draw_border(draw, size, radius):
    """Draw the subtle accent border."""
    c1 = (0, 212, 170)   # #00d4aa
    c2 = (0, 153, 255)   # #0099ff
    mid = lerp_color(c1, c2, 0.5) + (77,)
    inset = int(60 * size / 1024)
    bw = max(1, int(8 * size / 1024))
    r = int(180 * size / 1024)

    for offset in range(bw):
        x0 = inset + offset
        y0 = inset + offset
        x1 = size - 1 - inset - offset
        y1 = size - 1 - inset - offset
        draw.rounded_rectangle([x0, y0, x1, y1], radius=r, outline=mid)


def generate_icon(size):
    """Generate a single icon at the given size."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    radius = int(220 * size / 1024)

    draw_gradient_bg(img, radius)
    draw = ImageDraw.Draw(img)
    draw_border(draw, size, radius)

    font_size = int(560 * size / 1024)
    font = None
    font_candidates = [
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/calibri.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/SFNSDisplay.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    for fp in font_candidates:
        try:
            font = ImageFont.truetype(fp, font_size)
            break
        except (OSError, IOError):
            continue
    if font is None:
        font = ImageFont.load_default()

    bbox = font.getbbox("K")
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    tx = (size - tw) // 2 - bbox[0]
    ty = int(size * 0.08) - bbox[1]

    draw_accent_gradient_text(img, "K", (tx, ty, tx + tw, ty + th), font)
    draw_chart_line(draw, img, size)

    return img


def generate_tray_icon(size=64, status="stopped"):
    """Generate a small tray icon with status color indicator."""
    img = generate_icon(size)
    draw = ImageDraw.Draw(img)

    colors = {
        "healthy": (34, 197, 94),     # green
        "unhealthy": (251, 191, 36),  # amber
        "stopped": (180, 180, 190),   # brighter gray for visibility
        "starting": (96, 165, 250),   # blue
    }
    dot_color = colors.get(status, colors["stopped"])
    dot_r = size // 6
    cx = size - dot_r - 2
    cy = size - dot_r - 2

    # Dark border around dot for contrast on any background
    draw.ellipse(
        [cx - dot_r - 3, cy - dot_r - 3, cx + dot_r + 3, cy + dot_r + 3],
        fill=(30, 30, 46, 255),
    )
    # White ring
    draw.ellipse(
        [cx - dot_r - 1, cy - dot_r - 1, cx + dot_r + 1, cy + dot_r + 1],
        fill=(255, 255, 255, 255),
    )
    # Status dot
    draw.ellipse(
        [cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r],
        fill=dot_color + (255,),
    )

    return img


def generate_icns(img_1024):
    """Generate macOS .icns file. Uses sips+iconutil on macOS, saves PNGs otherwise."""
    if platform.system() != "Darwin":
        print("    (Not on macOS — skipping .icns generation)")
        print("    Run this script on macOS to generate the .icns file")
        return

    RESOURCES_DIR.mkdir(parents=True, exist_ok=True)
    iconset_dir = Path(tempfile.mkdtemp()) / "kleitos.iconset"
    iconset_dir.mkdir()

    # macOS iconset requires specific filenames and sizes
    # icon_NxN.png and icon_NxN@2x.png
    icon_sizes = {
        "icon_16x16.png": 16,
        "icon_16x16@2x.png": 32,
        "icon_32x32.png": 32,
        "icon_32x32@2x.png": 64,
        "icon_128x128.png": 128,
        "icon_128x128@2x.png": 256,
        "icon_256x256.png": 256,
        "icon_256x256@2x.png": 512,
        "icon_512x512.png": 512,
        "icon_512x512@2x.png": 1024,
    }

    for filename, size in icon_sizes.items():
        resized = img_1024.resize((size, size), Image.LANCZOS)
        resized.save(iconset_dir / filename, "PNG")

    # Use iconutil to create .icns
    icns_path = RESOURCES_DIR / "kleitos.icns"
    try:
        subprocess.run(
            ["iconutil", "-c", "icns", str(iconset_dir), "-o", str(icns_path)],
            check=True, capture_output=True, text=True,
        )
        print(f"    Generated {icns_path}")
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"    iconutil failed: {e}")
        print("    Saving individual PNGs to Resources/ instead")
        for filename, size in icon_sizes.items():
            src = iconset_dir / filename
            if src.exists():
                shutil.copy2(src, RESOURCES_DIR / filename)
    finally:
        shutil.rmtree(iconset_dir.parent, ignore_errors=True)


def main():
    ASSETS_DIR.mkdir(exist_ok=True)

    print("Generating Axion icons...")

    # 1. Main 1024x1024 PNG
    print("  [1/7] 1024x1024 PNG...")
    img_1024 = generate_icon(1024)
    img_1024.save(ASSETS_DIR / "kleitos-1024.png", "PNG")

    # 2. Additional PNG sizes
    print("  [2/7] 512x512, 256x256, 128x128, 64x64 PNGs...")
    for size in [512, 256, 128, 64]:
        img = generate_icon(size)
        img.save(ASSETS_DIR / f"kleitos-{size}.png", "PNG")

    # 3. Windows .ico (multi-size: 16, 24, 32, 48, 64, 256)
    print("  [3/7] Windows .ico (multi-size)...")
    img_256 = Image.open(ASSETS_DIR / "kleitos-256.png")
    img_256.save(
        ASSETS_DIR / "kleitos.ico",
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (256, 256)],
    )

    # 4. Tray icons (64x64, all statuses)
    print("  [4/7] Tray status icons...")
    for status in ["healthy", "unhealthy", "stopped", "starting"]:
        tray_img = generate_tray_icon(64, status)
        tray_img.save(ASSETS_DIR / f"tray-{status}.png", "PNG")

    # 5. macOS .icns
    print("  [5/7] macOS .icns...")
    generate_icns(img_1024)

    # 6. Dashboard favicon
    print("  [6/7] Dashboard favicon...")
    dashboard_dir = PROJECT_DIR / "dashboard"
    if dashboard_dir.exists():
        shutil.copy2(ASSETS_DIR / "kleitos.ico", dashboard_dir / "favicon.ico")
        print(f"    Copied favicon to {dashboard_dir / 'favicon.ico'}")
    else:
        print("    dashboard/ directory not found, skipping favicon")

    # 7. Summary
    print("  [7/7] Done!")
    print()
    print(f"  Icons saved to: {ASSETS_DIR}")
    print(f"    kleitos-1024.png  — macOS / high-res")
    print(f"    kleitos-512.png   — large icon")
    print(f"    kleitos-256.png   — general use")
    print(f"    kleitos-128.png   — medium icon")
    print(f"    kleitos-64.png    — small icon")
    print(f"    kleitos.ico       — Windows app icon (16-256px)")
    print(f"    tray-*.png        — system tray status icons")
    if platform.system() == "Darwin":
        print(f"    kleitos.icns      — macOS app icon")


if __name__ == "__main__":
    main()
