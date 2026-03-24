# COELHONexus: Business Plan & Go-to-Market Strategy

> **Founder**: Rafael Coelho
> **Location**: Curitiba, Paraná, Brazil
> **Target**: Global AI Search Platform
> **Last Updated**: March 2026

---

## Executive Summary

**COELHONexus** is an AI-powered semantic search platform for YouTube content, leveraging Agentic RAG + GraphRAG architecture to unlock the world's largest video database.

**The Opportunity**: No tool exists that enables semantic search across YouTube transcripts. This is a $133B market by 2030.

**The Founder Advantage**: Rafael Coelho brings 6+ years of MLOps/ML engineering expertise, with production experience in Kubernetes, real-time systems, and AI agents—the exact skillset required to build and scale this platform.

**The Strategy**: Launch with zero infrastructure cost using free tiers, monetize in Brazil first (Pix payments, local pricing), then scale globally.

---

## Founder Profile & Competitive Advantage

### Rafael Coelho - Senior ML/MLOps Engineer

| Attribute | Details |
|-----------|---------|
| **Experience** | 6+ years designing production ML systems |
| **Education** | Bachelor's in Mathematics (UFPR) |
| **Location** | Curitiba, Paraná, Brazil |
| **Expertise** | MLOps, Kubernetes, AI Agents, GraphRAG |

### Technical Expertise (Directly Applicable)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     FOUNDER SKILLS → PRODUCT FIT                            │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  MLOps & Kubernetes          →  Production-grade infrastructure             │
│  Terraform & Helm            →  Zero-cost Oracle/K8s deployment            │
│  LangChain & LangGraph       →  Agentic RAG implementation                 │
│  Neo4j & GraphRAG            →  Knowledge graph architecture               │
│  Kafka & Real-time Systems   →  Live video indexing pipeline               │
│  Prometheus & Grafana        →  Production observability                   │
│  MLflow                      →  Model versioning & experimentation         │
│                                                                             │
│  RESULT: Can build entire platform solo, no external engineering needed    │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Competitive Moat from Day 1

| Advantage | Why It Matters |
|-----------|----------------|
| **Full-stack ML/MLOps** | No need to hire expensive engineers early |
| **Production experience** | Skip amateur mistakes, ship reliable product |
| **Kubernetes native** | Scale from 0 to millions without rewrite |
| **Real-time systems** | Future feature: live video indexing |
| **Domain diversity** | Can pivot to Logistics, Finance, Real Estate verticals |

---

## Product Vision

### Phase 1: YouTube Content Search (MVP)

**Core Features:**
- Semantic search across video transcripts
- Knowledge graph of entities (people, topics, channels)
- Timestamp-linked results (jump to exact moment)
- Multi-hop queries ("Videos where Elon Musk discusses AI with engineers")

### Phase 2: Platform Expansion

**Additional Data Sources:**
- Podcasts (Spotify, Apple Podcasts)
- Educational platforms (Coursera, Udemy)
- Conference talks (TED, tech conferences)
- Corporate video libraries (enterprise)

### Phase 3: Vertical SaaS

**Industry-Specific Solutions:**
- **Legal**: Evidence discovery in video depositions
- **Finance**: Earnings call analysis (your domain expertise)
- **Real Estate**: Property video search (your domain expertise)
- **Education**: Lecture content search

---

## Technical Architecture

### Production Stack (Zero Cost)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         COELHONEXUS ARCHITECTURE                            │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                      ORACLE CLOUD (FREE TIER)                        │   │
│  │                                                                       │   │
│  │   ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                 │   │
│  │   │   FastAPI   │  │   Qdrant    │  │    Neo4j    │                 │   │
│  │   │   Backend   │  │  (Vectors)  │  │   (Graph)   │                 │   │
│  │   └─────────────┘  └─────────────┘  └─────────────┘                 │   │
│  │          │                │                │                         │   │
│  │          └────────────────┼────────────────┘                         │   │
│  │                           │                                           │   │
│  │   ┌─────────────────────────────────────────────────────────────┐   │   │
│  │   │                  LANGGRAPH AGENTIC RAG                       │   │   │
│  │   │                                                               │   │   │
│  │   │   Retrieve → Grade → Generate → Verify → [Retry if needed]  │   │   │
│  │   │                                                               │   │   │
│  │   └─────────────────────────────────────────────────────────────┘   │   │
│  │                                                                       │   │
│  └───────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                        EXTERNAL SERVICES                             │   │
│  │                                                                       │   │
│  │   ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                 │   │
│  │   │    Groq     │  │  HuggingFace│  │   Vercel    │                 │   │
│  │   │  (LLM API)  │  │ (Embeddings)│  │ (Frontend)  │                 │   │
│  │   │    FREE     │  │    FREE     │  │    FREE     │                 │   │
│  │   └─────────────┘  └─────────────┘  └─────────────┘                 │   │
│  │                                                                       │   │
│  └───────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  MONTHLY COST: R$0                                                          │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Technology Choices (Aligned with Founder Expertise)

| Component | Technology | Why (Founder Fit) |
|-----------|------------|-------------------|
| **Infrastructure** | Oracle Cloud + K8s | Kubernetes native expertise |
| **IaC** | Terraform + Helm | Already using in COELHOCloud |
| **Backend** | FastAPI | Already using in COELHONexus |
| **Agent Framework** | LangGraph | Already experienced |
| **Vector DB** | Qdrant | Already integrated |
| **Graph DB** | Neo4j | Already using for GraphRAG |
| **Observability** | Prometheus + Grafana | 50+ metrics experience |
| **MLOps** | MLflow | Already part of stack |

---

## Go-to-Market Strategy

### Phase 1: Brazil Launch (Months 1-6)

**Why Brazil First:**
- Home market advantage (language, payments, network)
- Lower CAC (customer acquisition cost)
- Pix payments = 0% fees, instant settlement
- Validate product before international expansion
- Government funding available (FINEP, BNDES)

**Target Customers (Brazil):**

| Segment | Size | Pain Point | Willingness to Pay |
|---------|------|------------|-------------------|
| Marketing agencies | 5,000+ | Competitor content analysis | R$149-399/mo |
| EdTech companies | 500+ | Searchable video courses | R$399-999/mo |
| Content creators | 100,000+ | Find collaboration opportunities | R$49-149/mo |
| Researchers | 10,000+ | Academic video analysis | R$49-149/mo |

**Pricing (Brazil):**

| Tier | Price | Features | Target |
|------|-------|----------|--------|
| **Free** | R$0 | 100 searches/mo | Lead generation |
| **Starter** | R$49/mo | 1,000 searches, basic API | Individual creators |
| **Pro** | R$149/mo | 10,000 searches, full API | Agencies, researchers |
| **Business** | R$399/mo | 50,000 searches, team features | Companies |
| **Enterprise** | Custom | Unlimited, private deployment | Large organizations |

**Payment Integration:**
- Pix (primary) - 61% of Brazil SaaS revenue
- Boleto (fallback) - for those without cards
- Credit card (international) - Stripe

### Phase 2: LATAM Expansion (Months 7-12)

**Target Markets:**
- Mexico (130M population, growing tech scene)
- Colombia (50M, strong startup ecosystem)
- Argentina (45M, tech talent hub)
- Chile (19M, highest GDP per capita in LATAM)

**Localization:**
- Spanish interface
- Local currency pricing
- Regional payment methods

### Phase 3: Global Scale (Year 2+)

**Target Markets:**
- USA (highest willingness to pay)
- Europe (GDPR-compliant deployment)
- Southeast Asia (emerging market)
- Middle East (Dubai, UAE - tax-free, high spending)

**Pricing Adjustment:**
- USA/Europe: 2-3x Brazil pricing
- Enterprise focus in developed markets

---

## Revenue Projections

### Conservative Scenario

| Period | Customers | MRR (BRL) | MRR (USD) | Notes |
|--------|-----------|-----------|-----------|-------|
| Month 6 | 50 | R$5,000 | $1,000 | First paying customers |
| Month 9 | 150 | R$15,000 | $3,000 | PMF validated |
| Month 12 | 400 | R$40,000 | $8,000 | Brazil market established |
| Month 18 | 1,000 | R$120,000 | $24,000 | LATAM expansion |
| Month 24 | 2,500 | R$350,000 | $70,000 | Global presence |

### Optimistic Scenario (with funding)

| Period | Customers | MRR (BRL) | MRR (USD) | Notes |
|--------|-----------|-----------|-----------|-------|
| Month 6 | 100 | R$12,000 | $2,400 | Aggressive marketing |
| Month 12 | 800 | R$100,000 | $20,000 | FINEP/BNDES funding |
| Month 18 | 3,000 | R$400,000 | $80,000 | International push |
| Month 24 | 10,000 | R$1,500,000 | $300,000 | Series A ready |

---

## Cost Structure

### Phase 1: Bootstrap (Months 1-6)

| Category | Monthly Cost | Notes |
|----------|--------------|-------|
| Infrastructure | R$0 | Oracle Free Tier |
| LLM APIs | R$0 | Groq Free Tier |
| Vector/Graph DB | R$0 | Free tiers |
| Frontend hosting | R$0 | Vercel Free |
| Domain | R$5 | Annual ~R$60 |
| Email | R$0 | SendGrid free tier |
| **Total** | **R$5/mo** | |

### Phase 2: Growth (Months 7-12)

| Category | Monthly Cost | Notes |
|----------|--------------|-------|
| Infrastructure | R$250 | Upgraded Oracle |
| LLM APIs | R$500 | Groq + OpenAI backup |
| Vector DB | R$75 | Qdrant paid |
| Graph DB | R$325 | Neo4j Aura |
| Frontend | R$100 | Vercel Pro |
| Marketing | R$500 | Content, ads |
| **Total** | **R$1,750/mo** | |

### Break-Even Analysis

| Scenario | Break-Even Point | Customers Needed |
|----------|------------------|------------------|
| Bootstrap | Month 6-7 | ~50 at R$100 avg |
| Growth | Month 9-10 | ~150 at R$120 avg |

---

## Marketing Strategy

### Content Marketing (Zero Cost)

**Platforms:**
- LinkedIn (professional audience)
- Twitter/X (tech community)
- YouTube (demo videos, tutorials)
- Medium/Dev.to (technical articles)

**Content Types:**
1. **Technical blogs**: "How I Built a YouTube Search Engine with GraphRAG"
2. **Demo videos**: Product walkthroughs
3. **Open source contributions**: Build community
4. **Case studies**: Early customer success stories

### SEO Strategy

**Target Keywords (Portuguese):**
- "busca em vídeos do YouTube"
- "pesquisa em transcrições YouTube"
- "ferramenta IA para YouTube"

**Target Keywords (English):**
- "YouTube transcript search"
- "semantic video search"
- "AI video content search"

### Community Building

1. **GitHub**: Open source core components (1,000+ stars target)
2. **Discord**: Community for users and developers
3. **Newsletter**: Weekly AI/video insights

### Partnerships

| Partner Type | Value | Approach |
|--------------|-------|----------|
| YouTube creators | Distribution | Offer free Pro tier |
| Marketing agencies | Customers | White-label option |
| EdTech platforms | Integration | API partnerships |
| AI newsletters | Exposure | Guest posts, features |

---

## Funding Strategy

### Phase 1: Bootstrap (Months 1-6)

**Sources:**
- Personal savings / side income
- Zero-cost infrastructure
- Early customer revenue

**Target:** R$0 external funding needed

### Phase 2: Government Funding (Months 3-9)

**FINEP Mais Inovação:**
- Amount: Up to R$10 million
- Type: Non-reimbursable (grant)
- Timeline: 6-12 months approval
- Status: Apply in Month 3

**BNDES Garagem:**
- Amount: R$500K - R$5M
- Type: Accelerator + funding
- Timeline: Annual cycles
- Status: Apply when open

### Phase 3: Angel/Pre-Seed (Month 9-12)

**Target:** R$500K - R$1M

**Use of Funds:**
- Hire 1-2 engineers (R$15-25K/mo each)
- Marketing budget (R$10K/mo)
- Infrastructure scaling (R$5K/mo)

**Potential Investors:**
- Canary (Brazil-focused VC)
- Monashees (LATAM)
- KPTL (Brazil)
- Valor Capital (Brazil-US)

### Phase 4: Seed Round (Month 18-24)

**Target:** $1-3M USD

**Use of Funds:**
- Team expansion (10+ people)
- International expansion
- Enterprise sales team

**Target Investors:**
- Y Combinator (apply when >$10K MRR)
- a]16z (if US traction)
- Sequoia LATAM

---

## Milestones & Timeline

### Year 1 Milestones

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           YEAR 1 ROADMAP                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  Q1 (Jan-Mar 2026)                                                          │
│  ├─ Month 1: MVP complete, deployed on Oracle Free                          │
│  ├─ Month 2: Beta launch, 50 users                                          │
│  └─ Month 3: Apply to FINEP, iterate on feedback                            │
│                                                                             │
│  Q2 (Apr-Jun 2026)                                                          │
│  ├─ Month 4: Paid launch, Pix integration                                   │
│  ├─ Month 5: First 30 paying customers                                      │
│  └─ Month 6: R$5,000 MRR, product-market fit validated                      │
│                                                                             │
│  Q3 (Jul-Sep 2026)                                                          │
│  ├─ Month 7: R$15,000 MRR, hire first contractor                            │
│  ├─ Month 8: LATAM soft launch (Mexico)                                     │
│  └─ Month 9: R$25,000 MRR, angel round conversations                        │
│                                                                             │
│  Q4 (Oct-Dec 2026)                                                          │
│  ├─ Month 10: Close angel round (R$500K-1M)                                 │
│  ├─ Month 11: Hire 2 engineers, scale marketing                             │
│  └─ Month 12: R$50,000 MRR, 500+ customers                                  │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Key Performance Indicators (KPIs)

| KPI | Month 6 | Month 12 | Month 24 |
|-----|---------|----------|----------|
| MRR | R$5,000 | R$50,000 | R$350,000 |
| Customers | 50 | 500 | 2,500 |
| Free users | 500 | 5,000 | 50,000 |
| Churn rate | <10% | <8% | <5% |
| CAC payback | 3 months | 2 months | 1.5 months |
| NPS | 40+ | 50+ | 60+ |

---

## Risk Mitigation

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| YouTube API changes | Medium | High | Diversify data sources early |
| Competition from Google | Low | High | Build community moat, enterprise focus |
| LLM cost increases | Medium | Medium | Self-hosted fallback (Ollama) |
| Slow customer acquisition | Medium | High | Content marketing, open source |
| Technical debt | Medium | Medium | Your MLOps expertise |
| Burnout (solo founder) | High | High | Hire early, automate everything |

---

## Exit Scenarios

### Scenario 1: Acquisition (Most Likely)

| Acquirer Type | Valuation Multiple | Target ARR | Exit Value |
|---------------|-------------------|------------|------------|
| Video platform | 8-12x ARR | $3M | $24-36M |
| AI company | 10-15x ARR | $5M | $50-75M |
| Enterprise SaaS | 8-10x ARR | $10M | $80-100M |

**Potential Acquirers:**
- VidIQ, TubeBuddy (strategic)
- HubSpot, Salesforce (marketing stack)
- Notion, Coda (knowledge management)
- Google, Microsoft (big tech)

### Scenario 2: IPO (Long-term)

- Target: $100M+ ARR
- Timeline: 7-10 years
- Market: B3 (Brazil) or NASDAQ

### Scenario 3: Lifestyle Business

- Target: $1M ARR
- Team: 5-10 people
- Profit: R$500K+/year personal income
- Timeline: 3-5 years

---

## Immediate Action Items

### This Week

- [ ] Set up Oracle Cloud Free account
- [ ] Deploy MVP to production (R$0 cost)
- [ ] Create landing page on Vercel
- [ ] Set up Groq API key
- [ ] Index first 1,000 YouTube videos

### This Month

- [ ] Launch beta to 50 users
- [ ] Collect feedback and iterate
- [ ] Write first technical blog post
- [ ] Start FINEP application research
- [ ] Set up Pix payment integration

### This Quarter

- [ ] Reach 50 paying customers
- [ ] Hit R$5,000 MRR
- [ ] Submit FINEP application
- [ ] Open source core components
- [ ] Build GitHub community (500+ stars)

---

## Conclusion

**COELHONexus** is uniquely positioned to capture a massive market opportunity:

| Factor | Assessment |
|--------|------------|
| **Market size** | $133B by 2030 |
| **Competition** | None (gap confirmed) |
| **Founder-market fit** | Perfect (6+ years MLOps, AI agents) |
| **Initial investment** | R$0 (free tiers) |
| **Time to revenue** | 6 months |
| **Path to R$1M ARR** | 24 months |

**The founder's unique combination of:**
- MLOps expertise (production-ready from day 1)
- AI agent experience (LangGraph, GraphRAG)
- Infrastructure skills (Kubernetes, Terraform)
- Domain knowledge (Finance, Real Estate)

**Makes this a high-probability success with minimal capital requirements.**

---

## Appendices

### A. Technical Documentation
- See: `docs/ARCHITECTURE-AGENTIC-RAG.md`

### B. Market Research
- See: `docs/BUSINESS-OPPORTUNITY-YOUTUBE-SEARCH.md`

### C. Infrastructure Cost Analysis
- Oracle Cloud Free: https://www.oracle.com/cloud/free/
- Qdrant Pricing: https://qdrant.tech/pricing/
- Groq Console: https://console.groq.com/

### D. Brazil Funding Resources
- FINEP: https://www.gov.br/finep/
- BNDES: https://www.bndes.gov.br/
- SEBRAE: https://sebrae.com.br/

---

*Last updated: 2026-03-23*
*Author: Rafael Coelho*
*Contact: rafaelcoelho1409.github.io*
