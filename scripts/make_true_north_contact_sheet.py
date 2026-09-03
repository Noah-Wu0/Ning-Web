from pathlib import Path
from PIL import Image, ImageOps, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
source = ROOT / "assets" / "images" / "truenorth"
files = [
    "boss-ai-hero.png", "boss-ai-context.png",
    "investor-ai-hero.png", "investor-ai-action.png",
    "topsales-ai-hero.png", "topsales-ai-loop.png",
]
labels = [
    "Boss AI / Hero", "Boss AI / Context",
    "Investor AI / Hero", "Investor AI / Risk to action",
    "TopSales AI / Hero", "TopSales AI / Closed loop",
]
cell_w, cell_h, label_h, cols = 640, 360, 48, 2
rows = 3
canvas = Image.new("RGB", (cell_w * cols, (cell_h + label_h) * rows), "#e8ebe6")
draw = ImageDraw.Draw(canvas)
for i, (name, label) in enumerate(zip(files, labels)):
    img = Image.open(source / name).convert("RGB")
    fitted = ImageOps.fit(img, (cell_w, cell_h), method=Image.Resampling.LANCZOS)
    x, y = (i % cols) * cell_w, (i // cols) * (cell_h + label_h)
    canvas.paste(fitted, (x, y))
    draw.rectangle((x, y + cell_h, x + cell_w, y + cell_h + label_h), fill="#10251d")
    draw.text((x + 20, y + cell_h + 15), label, fill="#f4f7f6")
canvas.save(source / "generated-contact-sheet.jpg", quality=92)
print(source / "generated-contact-sheet.jpg")
