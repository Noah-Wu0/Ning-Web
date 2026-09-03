# 01.AI Interactive Website — V5

A modular, client-facing showcase for 01.AI. V5 restores the full Solar Inspection application and fixes all Leadership AI cross-page navigation.

## Run locally

The full Solar Inspection demo requires FastAPI and WebSocket support. Use the included launcher rather than a simple static file server.

```bash
cd "01AI_Interactive_Website_20260731_V5"
./run.command
```

Open:

```text
http://127.0.0.1:8774/
```

## V5 changes

### Full Solar Inspection application

- Restores the original FastAPI, WebSocket and Three.js application supplied in `solar_inspection.zip`.
- Preserves the original operating content:
  - 10 × 16 solar field with 160 panels;
  - five inspection drones;
  - five Go2 ground robots;
  - live telemetry and 3D scene;
  - drone and Go2 FPV panes;
  - active tasks, faults, fleet and task log;
  - panel monitor, analytics, AI command, safety and settings views;
  - deterministic natural-language mission control;
  - drone-to-Go2 anomaly handoff.
- Removes all customer branding and client-identifying text.
- Mounts the Solar backend under `/demos/solar-inspection/` through the unified `serve.py` server.
- Includes only runtime-required Go2 DAE assets, reducing the supplied 184 MB development package to about 26 MB while preserving the running demo.

### Leadership AI navigation

- Fixes a repeated Next.js base path that previously generated URLs such as `/demos/leadership-ai/demos/leadership-ai/meetings`.
- Forces internal product navigation to load the exported HTML page directly instead of requesting unavailable Next.js Flight resources.
- Verifies click navigation across Daily Vanguard, Smart Meetings, Execution Tracking and Strategic Analysis.

## V4 foundation

- Removes Dr. Ning Ning's portrait from both the page and the asset directory while retaining the professional profile text.
- Converts the profile into a balanced full-width text card.
- Expands Leadership AI to a near-full-browser-width, 900-pixel product stage.
- Uses an explicit static `index.html` iframe entry and eager loading.
- Adds a visible loading state, runtime content readiness check, `Product ready` status and in-place reload action.
- Preserves all Leadership AI navigation inside the parent website.

## V3 foundation

### Hero

- Replaces `SENSE. DECIDE. ACT.` with:
  - `AI TRANSFORMATION.`
  - `AI-NATIVE BY DESIGN.`
- Adds a meaningful AI Operating System visual connecting:
  - executive decisions;
  - role AI and workflows;
  - dynamic enterprise context;
  - digital twins;
  - robots and drones;
  - verified operational outcomes.

### Company

- Adds expanded profiles for:
  - Dr. Kai-Fu Lee — Founder, Chairman and Chief Executive Officer;
  - Dr. Ning Ning — Vice President, International Business and AI Consulting.
- Adds four combined team capabilities:
  - transformation and strategy;
  - AI product and technology;
  - industry and operational expertise;
  - international delivery.

### Ontology

- Translates the compiled ontology source data rather than relying only on post-render text replacement.
- English-localizes:
  - navigation and workspace UI;
  - industry domains;
  - 3,000 object types;
  - relationship types;
  - graph controls;
  - filters and legends;
  - dynamic details, provenance, schema health and dialog text.
- Removes the previous runtime translation adapter.

### Leadership AI

- Removes the former Executive Decision Room animation.
- Embeds the complete Leadership AI product directly in the website.
- Supports in-page navigation across:
  - Daily Vanguard;
  - risk alerts;
  - evidence and impact chains;
  - Execution Tracking;
  - Smart Meetings;
  - Strategy and simulation.
- Keeps the parent website URL unchanged during product navigation.
- Includes a subpath guard for static deployment under `/demos/leadership-ai/`.

### Energy System Intelligence

- Reduces the scroll track from `580vh` to `360vh`.
- Reduces each act transition from `330/540 ms` to `180/320 ms`.
- Reduces the opening sequence from `6.4 seconds` to `2.7 seconds`.
- Speeds up grouped content reveals.
- A single approximately 0.75-screen scroll now advances to the next act.

## Website structure

```text
index.html
css/
  styles.css
js/
  app.js
assets/
  images/
    01ai-logo.webp
    kai-fu-lee.webp
    energy-system-preview.jpg
    refinery-preview.jpg
    case-mining-panorama.jpg
    case-agriculture.jpg
    case-kazakhstan.jpg
demos/
  kazakhstan-energy.html
  ontology-lineage.html
  refinery.html
  solar-inspection/
  leadership-ai/
vendor/
```

## Main storyline

1. AI Transformation — AI-Native by Design
2. Company leadership and combined capabilities
3. Products, platforms and transformation services
4. Selected operating cases
5. Decision Intelligence and Physical Intelligence
6. Ontology as computable business context
7. Embedded Leadership AI
8. Physical Intelligence demos
9. Engagement path

## Demo entry points

- Kazakhstan Energy Digital Twin:
  - `demos/kazakhstan-energy.html`
- Ontology and Lineage Workbench:
  - `demos/ontology-lineage.html`
- Refinery Live Digital Twin:
  - `demos/refinery.html`
- Solar Embodied AI Inspection:
  - `demos/solar-inspection/`
- Leadership AI:
  - `demos/leadership-ai/daily-vanguard/`

## Validation completed

- Full-page desktop visual QA: passed.
- Redesigned hero visual QA: passed.
- Expanded company section visual QA: passed.
- Ontology initial and dynamic rendered CJK scan: zero.
- Parent page recursive CJK scan, including embedded Ontology and Leadership AI: zero.
- Leadership AI direct and click navigation across Daily Vanguard, Smart Meetings, Execution Tracking and Strategic Analysis: passed.
- Leadership AI risk-to-evidence-to-execution navigation: passed without leaving the parent page.
- Full Solar Inspection runtime: passed with WebSocket connected, 160 panels, five drones and five Go2 robots.
- Solar `inspect solar field` mission: passed; aerial scan completed and Go2 ground confirmation activated.
- Client-brand text scan across the V5 project: zero.
- Energy act transition test: passed.
- Local resource and key URL checks: passed.

## English-language note

All client-visible website and demo content is English. Some minified third-party or archived application bundles may contain non-rendered locale data or Unicode regular-expression ranges; these do not appear in the delivered interface.
