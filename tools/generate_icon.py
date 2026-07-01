"""Génère assets/icon.ico et assets/icon.png depuis assets/icon.svg.

Dépendances : pillow, cairosvg
    pip install pillow cairosvg
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
SVG = ROOT / "assets" / "icon.svg"
PNG = ROOT / "assets" / "icon.png"
ICO = ROOT / "assets" / "icon.ico"


def main():
    try:
        import cairosvg
        from PIL import Image
    except ImportError:
        print("Installing cairosvg and pillow...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "cairosvg", "pillow"])
        import cairosvg
        from PIL import Image

    # SVG → PNG 256x256
    cairosvg.svg2png(url=str(SVG), write_to=str(PNG), output_width=256, output_height=256)
    print(f"Generated {PNG}")

    # PNG → ICO multi-taille
    img = Image.open(PNG).convert("RGBA")
    sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    imgs = [img.resize(s, Image.LANCZOS) for s in sizes]
    imgs[0].save(ICO, format="ICO", sizes=sizes, append_images=imgs[1:])
    print(f"Generated {ICO}")


if __name__ == "__main__":
    main()
