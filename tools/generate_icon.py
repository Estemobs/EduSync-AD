"""Génère assets/icon.ico et assets/icon.png via Pillow (pas de dépendance cairo).

Dépendance : pillow
    pip install pillow
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
PNG = ROOT / "assets" / "icon.png"
ICO = ROOT / "assets" / "icon.ico"


def _ensure_pillow():
    try:
        from PIL import Image, ImageDraw, ImageFont
        return Image, ImageDraw, ImageFont
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pillow"])
        from PIL import Image, ImageDraw, ImageFont
        return Image, ImageDraw, ImageFont


def draw_icon(size: int):
    Image, ImageDraw, ImageFont = _ensure_pillow()

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Fond arrondi bleu
    r = size // 6
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=r, fill=(26, 111, 196, 255))

    # Reflet supérieur
    draw.rounded_rectangle([0, 0, size - 1, size // 2], radius=r, fill=(255, 255, 255, 30))

    cx = size // 2
    s = size / 256  # facteur d'échelle

    # Toque : polygone (chapeau plat)
    hat_y_top = int(60 * s)
    hat_y_bot = int(132 * s)
    left_x = int(44 * s)
    right_x = int(212 * s)
    draw.polygon([
        (cx, hat_y_top),
        (right_x, int(96 * s)),
        (cx, hat_y_bot),
        (left_x, int(96 * s)),
    ], fill=(255, 255, 255, 242))

    # Bord de la toque (ellipse)
    ew = int(54 * s)
    eh = int(14 * s)
    draw.ellipse([cx - ew, hat_y_bot - eh, cx + ew, hat_y_bot + eh], fill=(200, 223, 247, 255))

    # Cordon
    cord_x = int(202 * s)
    cord_y1 = int(96 * s)
    cord_y2 = int(140 * s)
    draw.rectangle([cord_x - int(4 * s), cord_y1, cord_x + int(4 * s), cord_y2],
                   fill=(255, 255, 255, 200))
    tassel_r = int(9 * s)
    draw.ellipse([cord_x - tassel_r, cord_y2 - tassel_r,
                  cord_x + tassel_r, cord_y2 + tassel_r], fill=(240, 192, 64, 255))

    # Éclair jaune
    bolt = [
        (int(108 * s), int(148 * s)),
        (int(122 * s), int(148 * s)),
        (int(114 * s), int(172 * s)),
        (int(140 * s), int(158 * s)),
        (int(126 * s), int(158 * s)),
        (int(134 * s), int(136 * s)),
    ]
    draw.polygon(bolt, fill=(240, 192, 64, 242))

    # Texte "AD" en bas
    font_size = max(6, int(38 * s))
    try:
        font = ImageFont.truetype("arial.ttf", font_size)
    except Exception:
        font = ImageFont.load_default()

    text = "AD"
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    tx = cx - tw // 2
    ty = int(200 * s) - th // 2
    draw.text((tx, ty), text, font=font, fill=(255, 255, 255, 230))

    return img


def main():
    Image, _, _ = _ensure_pillow()

    PNG.parent.mkdir(parents=True, exist_ok=True)

    img_256 = draw_icon(256)
    img_256.save(PNG)
    print(f"Generated {PNG}")

    sizes = [16, 32, 48, 64, 128, 256]
    imgs = [draw_icon(s) for s in sizes]
    imgs[0].save(ICO, format="ICO", sizes=[(s, s) for s in sizes], append_images=imgs[1:])
    print(f"Generated {ICO}")


if __name__ == "__main__":
    main()
