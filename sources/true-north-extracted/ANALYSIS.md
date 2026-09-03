# TrueNorth source-deck analysis

## Source handling

All three source decks are image-native. Each slide image was extracted at original resolution, OCR was run with macOS Vision in Simplified Chinese, Traditional Chinese and English, and each deck was visually reviewed as a contact sheet. Raw slide images, OCR JSON and slide-by-slide OCR Markdown are stored in this directory.

- `investor-ai/` — 14 slides
- `boss-ai/` — 18 slides
- `topsales-ai/` — 15 slides

## Boss AI — source storyline

1. Cover: an operating-decision system for the enterprise leader.
2. Enterprise AI is still focused on tool efficiency.
3. The category moves from tools and assistants to operating intelligence.
4. CEO operating loop: see reality, judge risk, drive execution, verify results.
5. Boss AI as the executive judgment hub.
6. Six capability layers.
7. Full-domain sensing across meetings, messages, documents and business systems.
8. Management-meeting workflow demo.
9. Company Map / enterprise Ontology.
10. Company GPS / continuously updated business state.
11. Company GPS demo.
12. Executive risk signals.
13. Risk-monitoring demo.
14. Security, compliance and trust.
15. Commitment Ledger.
16. Roadmap from risk warning to opportunity discovery.
17. Ninety-day value validation.
18. Closing: connect operating state to results.

### Public-page decisions

Retained: Company Map, dynamic context, meeting-to-action, risk sensing, Commitment Ledger, governance and staged lighthouse.

Removed or qualified: named or real-looking demo data; generic “two weeks early” and continuous-service claims; roadmap commitments; any implication that AI has final executive authority.

## Investor AI — source storyline

1. Cover: AI-enabled investment decision system for the Chief Investment Officer.
2. Move from expert-only judgment to an institutional decision mechanism.
3. Later-stage transactions have richer data and higher accountability.
4. Gaps between discovered risk and transaction action.
5. CIO investment-decision advisory system.
6. Multi-agent roles for analytical diversity and challenge.
7. Investment ontology plus specialist-agent architecture.
8. Source-anchored investment ontology.
9. Financial, legal, commercial, technical, valuation, exit and reporting agents.
10. Investment-committee simulation before recommendation.
11. Evidence-linked investment decision memorandum.
12. Named Beijing state-owned capital case with institution scale figures.
13. Translate risk into valuation, terms and decision.
14. Four-week pilot and product roadmap.

### Public-page decisions

Retained: investment ontology, source anchors, specialist diligence, challenge path, decision memo, risk-to-price/terms/decision and four-week pilot structure.

Removed: named customer, emblem and RMB 3.58 trillion figure; roadmap commitments; any implication that multi-agent debate is automatically independent judgment; any autonomous fiduciary or legal authority.

## TopSales AI — source storyline

1. Cover: strategic-opportunity decision and coordination hub.
2. A strategic opportunity is an organizational resource investment.
3. High-cost opportunities advance with incomplete information.
4. Assumptions defer risk into contract, delivery and cash.
5. Without a quality view, resources follow opportunities that merely look important.
6. Person-driven Lead-to-Cash lengthens cycle time and uncertainty.
7. Growth requires systematic improvement in conversion quality.
8. Agent + Ontology operating hub.
9. CRM records facts; Ontology connects the value-judgment chain.
10. A strategic opportunity is a changing operating environment.
11. Lead-to-Cash moves from linear handoffs to organizational coordination.
12. Next Best Action creates one shared priority.
13. Human and agent feedback continuously recalibrate context.
14. Sales management moves from progress chasing to quality control.
15. Close: opportunities become managed objects of growth quality.

### Public-page decisions

Retained: living opportunity context, quality model, CRM/Ontology distinction, Lead-to-Cash collaboration, Next Best Action and human-agent feedback.

Removed or qualified: any unsupported impact on win rate, cycle time or forecast accuracy; real-looking CRM data; any implication that agents replace account ownership.

## Generated visual QA

The original six-page visual set went through multiple OCR and independent visual-QA cycles because early image generations contained pseudo-text. After executive review, the three TrueNorth hero assets and the mining operating-chain asset were regenerated through the OpenAI Codex image backend.

### OpenAI GPT Image refresh

- Provider: `openai-codex`
- Model: `gpt-image-2-medium`
- `boss-ai-hero.png` — concrete executive decision-to-execution story; OCR 0 lines; independent visual QA pass.
- `investor-ai-hero.png` — evidence, specialist diligence and human investment-committee story; OCR 0 lines; independent visual QA pass.
- `topsales-ai-hero.png` — strategic-opportunity context and cross-functional action story; OCR 0 lines; independent visual QA pass.
- `case-mining-coordinated-operations.png` / `mining-case/pit-to-port.png` — one source image copied to both locations; mine, constrained rail corridor and port stockpile/ship-loading chain. OCR found no readable text; one candidate was adjudicated as industrial geometry. The final page uses a complete-image desktop treatment and image-above-copy mobile treatment to preserve the full chain.

The three homepage product cards use dedicated focus crops and brightness treatment so their role-specific subjects survive card-size rendering. Source images on the child pages remain uncropped.

### Secondary assets

- `boss-ai-context.png` — OCR 0 lines; visual QA pass.
- `investor-ai-action.png` — OCR 0 lines; visual QA pass.
- `topsales-ai-loop.png` — one OCR false positive caused by blank geometric swatches; independent visual QA confirmed no text.

All business copy is rendered as native English HTML rather than generated into image pixels.

## Additional product pages

- `worldwise.html` — English product page for the governed enterprise-agent runtime: model routing, permissioned context, task graphs, tool gateway, policy, receipts, recovery, evaluation and deployment boundaries.
- `fde.html` — English capability page for forward-deployed engineering: ambiguous problem to accepted outcome, field-pod ownership, four project returns, product-asset flywheel and delivery boundaries.

Both pages use native HTML/CSS architecture visuals so operating logic remains readable and maintainable rather than being encoded as decorative generated imagery.
