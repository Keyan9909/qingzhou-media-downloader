"""Generate the product ICO without downloading external assets."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = ROOT / "assets"
ASSET_DIR.mkdir(parents=True, exist_ok=True)
SIZE = 512
image = Image.new("RGBA", (SIZE, SIZE), "#4F6EF7")
draw = ImageDraw.Draw(image)

# Soft diagonal "sail" and download arrow.
draw.rounded_rectangle((38, 38, 474, 474), radius=112, fill="#4F6EF7")
draw.polygon([(126, 342), (238, 102), (386, 342)], fill="#FFFFFF")
draw.polygon([(218, 122), (386, 342), (270, 302)], fill="#DCE5FF")
draw.rounded_rectangle((226, 280, 286, 403), radius=26, fill="#172033")
draw.polygon([(171, 355), (341, 355), (256, 435)], fill="#172033")
draw.rounded_rectangle((130, 422, 382, 450), radius=14, fill="#172033")

png = ASSET_DIR / "app-icon.png"
ico = ASSET_DIR / "app-icon.ico"
image.save(png)
image.save(ico, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print(ico)
