from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
source = ROOT / "sources" / "true-north-extracted"
for slug in ["investor-ai", "boss-ai", "topsales-ai"]:
    data = json.loads((source / f"{slug}-ocr.json").read_text(encoding="utf-8"))
    parts = [f"# {slug} OCR"]
    for idx, item in enumerate(data, start=1):
        parts.append(f"\n## Slide {idx:02d}\n\n{item['text']}")
    (source / f"{slug}-ocr.md").write_text("\n".join(parts), encoding="utf-8")
    print(slug, len(data))
