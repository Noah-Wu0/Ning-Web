from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.request import urlopen
import re, sys

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8765/"
PAGES = ["index.html", "boss-ai.html", "investor-ai.html", "topsales-ai.html", "worldwise.html", "fde.html", "mining-case.html"]
DEMO_ENTRIES = [
    "demos/kazakhstan-energy.html",
    "demos/refinery.html",
    "demos/ontology-lineage.html",
    "demos/solar-inspection/index.html",
    "demos/leadership-ai/daily-vanguard/index.html",
    "demos/leadership-ai/demo-flow.html",
]

class Collector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls=[]
        self.ids=set()
    def handle_starttag(self, tag, attrs):
        data=dict(attrs)
        if data.get('id'): self.ids.add(data['id'])
        for key in ('href','src'):
            if data.get(key): self.urls.append(data[key])

errors=[]
checked=set()
for page in PAGES:
    path=ROOT/page
    text=path.read_text(encoding='utf-8')
    parser=Collector(); parser.feed(text)
    if page!='index.html' and re.search(r'[\u3400-\u9fff]', text):
        errors.append(f"{page}: contains CJK characters")
    page_url=urljoin(BASE,page)
    try:
        with urlopen(page_url,timeout=5) as response:
            if response.status!=200: errors.append(f"{page}: HTTP {response.status}")
    except Exception as exc: errors.append(f"{page}: {exc}")
    for raw in parser.urls:
        if raw.startswith(('mailto:','tel:','javascript:','data:')): continue
        parsed=urlparse(raw)
        if parsed.scheme in ('http','https') and parsed.netloc!='127.0.0.1:8765': continue
        if raw.startswith('#'):
            if raw[1:] not in parser.ids: errors.append(f"{page}: missing anchor {raw}")
            continue
        target=urljoin(page_url, raw)
        key=target.split('#')[0]
        if key in checked: continue
        checked.add(key)
        try:
            with urlopen(key,timeout=10) as response:
                if response.status!=200: errors.append(f"{page}: {raw} -> HTTP {response.status}")
        except Exception as exc: errors.append(f"{page}: {raw} -> {exc}")

for page in DEMO_ENTRIES:
    page_url=urljoin(BASE,page)
    try:
        with urlopen(page_url,timeout=10) as response:
            if response.status!=200: errors.append(f"{page}: HTTP {response.status}")
    except Exception as exc: errors.append(f"{page}: {exc}")

if errors:
    print("QA FAIL")
    print("\n".join(errors))
    sys.exit(1)
print(f"QA PASS: {len(PAGES)} customer pages, {len(DEMO_ENTRIES)} demo entries, {len(checked)} linked local resources, no CJK in customer child pages")
