from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse, unquote
import re, json

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "CUSTOMER_VOICE_AUDIT.md"

class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(); self.skip=0; self.text=[]; self.urls=[]; self.attrs=[]
    def handle_starttag(self, tag, attrs):
        if tag in ('script','style','template'): self.skip += 1
        data=dict(attrs)
        for key in ('href','src'):
            if data.get(key): self.urls.append(data[key])
        for key in ('alt','title','aria-label','placeholder'):
            if data.get(key): self.attrs.append(data[key])
    def handle_endtag(self, tag):
        if tag in ('script','style','template') and self.skip: self.skip -= 1
    def handle_data(self, data):
        if not self.skip and data.strip(): self.text.append(' '.join(data.split()))

patterns = {
    "production note": r"conceptual visual|generated for|product story|public page|source deck|source material|internal",
    "review disclaimer": r"should be treated as|unless explicitly|does not represent|client-identifying|details removed|hypotheses",
    "internal planning": r"roadmap|go/no-go|scale / no-scale|current product|planned capability|future state",
    "delivery language": r"pilot|lighthouse|shadow mode|evaluation set|hidden set",
    "anonymization language": r"anonymized|anonymous case|client-identifying",
    "technical-first language": r"runtime-neutral|control plane|idempotency|compensation path|task graph",
}

pages=sorted(p for p in ROOT.rglob('*.html') if '/_next/' not in str(p))
rows=[]
for path in pages:
    rel=path.relative_to(ROOT)
    text=path.read_text(encoding='utf-8',errors='ignore')
    parser=PageParser(); parser.feed(text)
    visible=' '.join(parser.text + parser.attrs)
    hits=[]
    for label,pat in patterns.items():
        for m in re.finditer(pat, visible, re.I):
            s=max(0,m.start()-100); e=min(len(visible),m.end()+180)
            hits.append((label,visible[s:e]))
    cjk=len(re.findall(r'[\u3400-\u9fff]',visible))
    rows.append((str(rel),len(visible),cjk,hits))

out=["# Customer-facing language audit","","This report inventories visible text and accessibility metadata across all HTML files in the website folder. Matches are review candidates, not automatic errors.",""]
out.append("## Inventory")
out.append("")
out.append("| Page | Visible chars | CJK chars | Review hits |")
out.append("|---|---:|---:|---:|")
for rel,n,cjk,hits in rows: out.append(f"| `{rel}` | {n} | {cjk} | {len(hits)} |")
out.append("")
out.append("## Review candidates")
for rel,n,cjk,hits in rows:
    if not hits and not cjk: continue
    out.append(f"\n### `{rel}`")
    if cjk: out.append(f"- Rendered/source-visible CJK character count: {cjk}")
    seen=set()
    for label,snippet in hits:
        key=(label,snippet)
        if key in seen: continue
        seen.add(key)
        out.append(f"- **{label}:** {snippet}")
REPORT.write_text('\n'.join(out),encoding='utf-8')
print(f"Audited {len(rows)} HTML files; report={REPORT}")
print(f"Pages with review candidates: {sum(bool(h) or c>0 for _,_,c,h in rows)}")
