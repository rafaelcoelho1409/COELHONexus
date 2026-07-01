# COELHO Nexus — Presentation Strategy
**Date:** 2026-06-27  
**Goal:** Maximize chances of landing Senior AI/MLOps/LLMOps roles in USA/Singapore/UAE  
**Target salary:** $170K–$325K USD

---

## Canva Designs

| Design | ID | Edit URL |
|---|---|---|
| **COELHO Nexus** (active) | `DAHNth9RQRM` | https://www.canva.com/d/rmYuD8eixKhVExm |
| **COELHO RealTime** (reference) | `DAG4Y5NW95c` | https://www.canva.com/d/vU97SbOqOmoMBMH |

Both: 53 pages, 1920×1080, identical page IDs (Nexus is a direct copy of RealTime).

---

## Color Palette — Wealth, Power & Authority (2026)

### Text colors (applicable via Canva MCP API)

| Role | Hex | Usage |
|---|---|---|
| Section headers / key titles | `#D4AF37` | Muted heritage gold — NOT web gold `#FFD700` |
| Main body / title text | `#FFFFFF` | Pure white for maximum contrast on black |
| Brand logo (top-left per slide) | `#FFFFFF` | |

### Shape & background colors (manual in Canva — API cannot change these)

| Element | Hex | Notes |
|---|---|---|
| Background (all 53 slides) | `#0A0A0A` | Onyx black — use "Apply to all pages" in Canva |
| Gold blob shapes | `#D4AF37` | Cover, section openers, closing slide |
| Silver/platinum blob shapes | `#C5C5C5` | Content/detail slides (infra, features, microservices) |

**Alternation logic:** Gold blobs on the slides that matter most (cover, section headers, close). Silver on technical content slides. Creates a "precious metals" luxury rhythm across 53 slides.

**Avoid:** `#F4EADE` (warm ivory) and `#E8E0D5` (off-white) — these read as faded/dim on true black. Applied to slide 1 then revised; lesson learned.

### Applied changes so far
- **Slide 1 committed:** tagline → `#D4AF37` ✓ (title/name/features still need `#FFFFFF` update)

---

## Image Recommendations (manual in Canva)

### Slide 2 — "About the Project"
**Top pick:** Abstract dark neural network / particle visualization with gold/amber nodes  
- Canva search: `"neural network dark gold"` or `"AI network abstract dark"`  
- Look for: near-black background, glowing amber/gold nodes and thin connection lines  
- Avoid: blue-tinted networks (clash with gold palette)

**Runner-up:** Worm's-eye view of glass skyscrapers at night with gold lighting  
- Canva search: `"skyscrapers night dark gold"`

**Note:** The current city photo is a **slide background fill**, not a placed element — the MCP API cannot replace it. Must be done manually in Canva.

---

## Optimal Slide Order for Job Applications

### Core principle: "Show, then justify"
The RealTime order follows build-up logic (context → features → demo → infra). For hiring managers, that's backwards — demos are buried at slide 20. Reorder to front-load the impressive content.

### Recommended structure (53 slides)

**Section 1 — Hook & Differentiator (4 slides)**
1. Cover
2. ⭐ NEW — "Built solo, end-to-end" hero stat slide: 3 microservices · 6 data stores · 18 Terraform modules · full OTel pipeline · 0 vendor lock-in
3. About the project
4. Platform architecture (high-level visual)

**Section 2 — Platform in Action (moved from slide 20 → slide 5) (~14 slides)**
5. Three features overview
6–8. **Research Radar FIRST** — leads because it's the most impressive for 2026 AI hiring: DeepAgents + FastMCP + 8-phase orchestrator + 4 discovery sources
9–11. Docs Distiller — 5-tier ingestion, Planner-Synth, LangFuse session tracking
12–14. YouTube Content Search — Neo4j knowledge graph, Qdrant vector search, Elasticsearch hybrid

**Section 3 — Technical Architecture (~6 slides)**
15. Three microservices (FastAPI+Celery, FastHTML, FastMCP)
16. LLM Router & BYOK (bandit routing + Fernet encryption — strong LLMOps signal)
17. OTel observability pipeline (dual-export, LangFuse trace gate — strongest LLMOps slide)
18. 6 data stores
19. Core platform components
20. Python/tech ecosystem

**Section 4 — Infrastructure (~10 slides)**
21. Infrastructure section header
22–30. 5-layer deep-dive: container orchestration → GitOps/CI/CD → data/streaming → app services → observability

**Section 5 — Microservices Engine (~11 slides)**
31–41. FastAPI, FastHTML, FastMCP, microservice deep-dives

**Section 6 — Close (2 slides)**
42. Closing summary — "production-grade, solo-owned, observable"
43. Contact + website + email

### Key changes vs RealTime order

| Change | Rationale |
|---|---|
| Demo section: slide 20 → slide 5 | Hiring managers won't wait 20 slides |
| Research Radar leads the demo section | Most relevant to 2026 AI market (agentic + FastMCP) |
| New "solo ownership" hero slide added | Biggest differentiator, currently invisible in the deck |
| Infrastructure moves to slides 21–30 | It's proof, not the pitch |
| LLM Router + OTel get dedicated early slides | Direct LLMOps signal — what hiring managers probe in interviews |

---

## Slide Content Status

### Already updated (Nexus-specific content) ✓
- Slide 1: Cover
- Slide 2: About the project
- Slide 3: Section overview ("Three Microservices & Three Features / Infrastructure as Code & Solo Ownership")
- Slide 4: Platform architecture
- Slides 5–6: Main applications (Docs Distiller, YCS, Research Radar)
- Slide 7: Core platform components (LLM Router, OTel, 6 stores, Celery)
- Slide 8: Infrastructure section header
- Slide 39: "The Microservices Engine" intro (FastAPI/FastHTML/FastMCP + 6 stores)
- Slide 52: Closing summary
- Slide 53: Contact

### Still contains COELHO RealTime content — needs update ✗
- Slides 9–17: Infra deep-dive (still mentions SvelteKit, 15 services, Kafka, Spark, MLflow)
- Slide 18: Python ecosystem (wrong libraries, still says COELHO REALTIME)
- Slide 19: "Platform Architecture" header (still says COELHO REALTIME)
- Slides 20–38: Entire "Platform in Action" section — TFD/ETA/ECCI walkthroughs → needs Docs Distiller/YCS/Research Radar demos
- Slides 40–51: Microservices deep-dives (still shows RealTime services: Spark, MLflow, Alertmanager, Karma)

---

## What I Can vs Cannot Do via Canva MCP API

### Can do (programmatically)
- Replace / find-and-replace text content
- Format text: color, font size, weight, style, alignment
- Reposition and resize elements
- Delete elements
- Swap images/videos in placed image elements

### Cannot do (must be done manually in Canva)
- Change background colors
- Change shape fill colors
- Change font family
- Add new shapes or elements from scratch
- Replace background images (set as slide background fill, not a placed element)
