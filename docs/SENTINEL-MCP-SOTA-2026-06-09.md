# Sentinel MCP — 3rd Feature SOTA + 10-Day Execution Plan (2026-06-09)

**Status:** DESIGN-LOCKED, pre-implementation.
**Scope:** A 3rd top-level feature in COELHO Nexus (replaces the "Coming Soon" tile) that simultaneously (a) demonstrates Senior AI/MLOps/LLMOps platform engineering for UAE / Singapore / USA hiring, (b) gives the COELHO Cloud its missing LangFuse + OpenTelemetry → Alloy + LGTM observability layer, (c) introduces FastMCP + DeepAgents as foundational primitives (not decoration), and (d) opens a B2B-SaaS passive-income surface.

**Cross-references:**
- [`CODE-ORGANIZATION-SOTA-2026-05-20.md`](./CODE-ORGANIZATION-SOTA-2026-05-20.md) — flat `apps/` + `domains/` layout the new feature must follow
- [`LLM-ROTATOR-SETTINGS-SOTA-2026-05-31.md`](./LLM-ROTATOR-SETTINGS-SOTA-2026-05-31.md) — the 7-provider free-tier rotator Sentinel governs
- [`KD-ROTATOR-BANDIT-SOTA-2026-05-23.md`](./KD-ROTATOR-BANDIT-SOTA-2026-05-23.md) — FGTS-VA bandit Sentinel reuses
- [`BUSINESS-PLAN-COELHONEXUS.md`](./BUSINESS-PLAN-COELHONEXUS.md) — wider business framing

---

## 1. The decision: 3rd feature, not "integrate into DD/YCS"

Two paths were considered:

| Option | Hiring-manager signal | Effort | Observability fit | Monetization |
|---|---|---|---|---|
| **A. 3rd feature "Sentinel MCP"** | "Built the platform 2 apps consume" — Senior MLOps / LLMOps positioning | ~10 days | Native (gateway = trace surface) | Clean B2B-SaaS surface |
| B. Integrate FastMCP + DeepAgents into DD + YCS | "Added a feature to my app" — mid-level positioning | ~5–7 days | Awkward (instrumentation scattered across two pipelines) | No standalone product |

**Picked A.** The asymmetry is the hiring signal. The user's GitHub portfolio already presents him as a *platform* engineer (real-time K8s + Kafka + Spark + Delta + MLflow). Sentinel reinforces that identity. Integrating into DD/YCS would dilute it to "app builder".

Secondary win: DD + YCS become **demo clients** of Sentinel (eat your own dogfood), which is the strongest possible proof-of-concept in a 5-minute recruiter video.

---

## 2. Architecture — How Sentinel slots into COELHO Nexus

### 2.1 Three top-level features after this ship

```
COELHO Nexus
├── /dd        Docs Distiller  (existing)
├── /ycs       YouTube Channel Synth  (existing)
└── /sentinel  Sentinel MCP   (NEW — 3rd tile in catalog)
```

Same shell, navbar, topbar, catalog-tile pattern. No UX divergence.

### 2.2 The 3 surfaces of the `/sentinel` page

| Surface | What it shows | Tech |
|---|---|---|
| **Control panel** (FastHTML) | Policy YAML editor; OAuth/token mgmt; "live tool calls" table; per-tenant rate limits | `apps/fasthtml/features/sentinel/` |
| **Embedded Grafana** | (a) Agent-trace explorer (Tempo), (b) Failure-cluster heatmap (Loki+Mimir) | iframe with Grafana SSO |
| **DeepAgents demo** | One-click "research → write → critic" reference flow, fully traced | Same FastHTML page |

### 2.3 Under the hood

```
DD / YCS / external clients
        │
        ▼   (MCP protocol — FastMCP server)
┌──────────────────────────┐
│  Sentinel Gateway        │
│  • OAuth2 / token auth   │
│  • Per-tool policy YAML  │
│  • PII masker middleware │
│  • Audit log (JSONL)     │  ─────► MinIO  audit/
│  • OTel spans on every   │  ─────► Alloy ─► Tempo
│    tool call             │
│  • LangFuse trace        │  ─────► LangFuse OSS
│  • Cost accounting       │  ─────► Mimir
└──────────────────────────┘
        │
        ▼   (rotator client — existing)
   LLM Rotator  (NIM · Groq · Cerebras · Gemini · DeepSeek · …)
```

The rotator stays unchanged — Sentinel sits **in front of it** as the MCP-protocol gateway. DD + YCS migrate their direct rotator calls to go through Sentinel via the MCP client.

### 2.4 New domain folders

Following [`CODE-CONVENTIONS.md`](./CODE-CONVENTIONS.md) and [`CODE-ORGANIZATION-SOTA-2026-05-20.md`](./CODE-ORGANIZATION-SOTA-2026-05-20.md):

```
apps/fastapi/domains/sentinel/
├── gateway/        # FastMCP server, OAuth, policy enforcement
├── audit/          # JSONL audit log → MinIO
├── telemetry/      # LangFuse + OTel span emitters
├── deepagents/     # Reference "research → write → critic" flow
└── eval/           # Ragas-lite eval harness (rotator-as-judge)

apps/fasthtml/features/sentinel/
├── body.py         # /sentinel route
├── controls.py     # Policy YAML editor, token mgmt
└── demo.py         # DeepAgents demo trigger UI
```

---

## 3. Why FastMCP + DeepAgents are foundational, not decorative

| Primitive | Decorative use (bad) | Foundational use (Sentinel) |
|---|---|---|
| **FastMCP** | "Expose one tool from DD" | Sentinel **IS** the MCP server every COELHO app talks through |
| **DeepAgents** | "Add a sub-agent to DD's planner" | The reference workflow demonstrates *how customers* should structure their own DeepAgents around Sentinel-governed tools |
| **LangFuse** | "Log a few prompts" | Every MCP tool call = a LangFuse trace → portfolio-grade observability story |
| **OTel + Alloy + LGTM** | "Bolt on a Prometheus scrape" | Every tool call = an OTel span → Tempo trace explorer; cost + token metrics → Mimir |

If hiring managers grep your repo for `mcp`, `deepagents`, `langfuse`, `opentelemetry` — Sentinel is where they ALL converge. Integration into DD/YCS would scatter them across 2 pipelines.

---

## 4. Market intel — June 2026 hiring signal (UAE / Singapore / USA)

- **LLMOps salary premium:** 30–50% above standard Senior dev (Optiveum, Second Talent). UAE Principal LLMOps **AED 562k–840k tax-free ≈ $220k NYC-equivalent at $150k Dubai**. USA Senior LLMOps $145k–$310k. SG SGD 130k–220k+.
- **MCP went mainstream:** Stacklok 2026 — 41% of orgs in MCP production, ~10k public servers, 97M+ monthly SDK downloads. **But only MintMCP has SOC-2 Type II.** The audit/policy/observability sub-niche is hot AND barely staffed.
- **DeepAgents shipped Mar 2026** as official LangChain harness. "Supervisor + sub-agents + async background" is now table stakes for "long-running agent" job posts.
- **Observability gap (Latitude 2026 survey):** 1/15 obs tools fully cover the 5 must-haves. Hiring managers probe specifically for **session tracing, tool-call spans, failure clustering** — exactly Sentinel's headline dashboards.
- **EU AI Act extraterritorial pull** drags SG/UAE FinTech into agentic-AI conformity. SG MAS 2026 Model Governance Framework for Agentic AI is live. Niche, well-paid.
- **Hot keywords in postings:** `LangGraph`, `MCP`, `LangFuse`, `OpenTelemetry`, `Ragas`, `multi-agent`, `guardrails`, `Kubernetes`, `vector DB`, `cost routing`.
- **SG talent shortage:** 83% of SG employers report critical AI infra talent gap (HuntingCube 2025).

---

## 5. Passive-income angle (secondary — JOB is primary)

| Tier | Price | Audience |
|---|---|---|
| Open-source single-tenant | Free | GitHub stars, hiring signal |
| Cloud starter | $49/mo | Solo devs needing MCP audit |
| Team | $149/mo | 5-seat startups |
| Compliance | $299/mo | Multi-tenant + audit export |

Realistic ramp (agentincome.io 2026 pattern):

- Month 2–3: **$500–1.5K MRR**
- Month 4–6: **$3–8K MRR**
- Month 6–12: **$8–20K+ MRR**
- Operating cost: **<$70/mo** at ~200 customers (free-tier rotator = ~97% margin)

Cited example: indie PR-description generator hit $8K MRR in 90 days at <$200/mo cost. **Bottleneck is distribution, not tech.**

**Critical framing:** the MRR is a bonus. The JOB lands first off the portfolio piece. Do not optimize Sentinel for paying customers in the 10-day window — optimize for the recruiter video.

---

## 6. 10-day execution sequence

| Days | Ship | Concrete deliverables |
|---|---|---|
| **1–2** | Gateway skeleton | `domains/sentinel/gateway/` — FastMCP server in front of rotator. OAuth2 client-credentials flow. Per-tool policy YAML (`allowlist`, `pii_mask`, `rate_limit`). Helm chart in `infrastructure/sentinel/`. Smoke test from a Python script. |
| **3–4** | Observability stack | LangFuse OSS deployed (Helm). OpenTelemetry SDK on every MCP tool call → Alloy → Tempo. PII-masker middleware (regex + LLM-judge fallback via rotator). Two Grafana dashboards: (a) **agent-trace explorer** (Tempo waterfall), (b) **failure-cluster heatmap** (Loki LogQL). |
| **5–6** | DD + YCS migration | Migrate DD's rotator calls (`domains/llm/...`) to go through Sentinel via MCP client. Same for YCS. **This is the demo's killshot — recruiter sees 2 prod apps lighting up Sentinel dashboards in real time.** |
| **7–8** | DeepAgents reference + eval | DeepAgents 3-role workflow (research → write → critic) consuming Sentinel tools, exposed at `/sentinel/demo`. Ragas-lite eval harness (`domains/sentinel/eval/`) using rotator-as-judge. Audit-log export to MinIO `sentinel/audit/` as JSONL. |
| **9** | Launch materials | Landing page on `/sentinel` route. 5-min Loom demo: click DD → see Sentinel trace; click YCS → see another trace; click DeepAgents demo → see 3-agent flow with critic loop. GitHub README with architecture diagram (this doc's §2.3). |
| **10** | Distribution | LinkedIn post tagged for UAE/SG/USA AI/MLOps recruiters with keywords from §4. Show-HN. r/LocalLLaMA. Submit to MCP server registry. |

---

## 7. What is CUT to make 10 days

| Cut | Reason |
|---|---|
| ❌ Multi-tenancy v1 | SaaS sell, not the demo. Single-tenant + SSO stub is enough. |
| ❌ SOC-2 dashboard | MintMCP's moat. Out of scope for solo dev. |
| ❌ Stripe / billing | Post-job task. Manual onboarding for first 5 customers. |
| ❌ DD/YCS UI redesign | They get a 1-line config change to point at Sentinel. Nothing else. |
| ❌ Multi-region | One COELHO Cloud node is enough for the demo. |
| ❌ Per-tool unit tests > smoke | Smoke + e2e demo flow is enough for portfolio-grade. |
| ❌ SDK in other languages | Python-only. Use FastMCP's reference client. |

---

## 8. Open questions before day 1

1. **DD/YCS dependency on Sentinel:** hard (DD breaks if Sentinel is down) or soft (DD bypasses Sentinel on failure)? Soft is safer for the demo; hard tells a stronger "platform" story. **Recommend: soft for v1, hard once Sentinel is HA.**
2. **`/sentinel` page UI:** minimal FastHTML control panel + embedded Grafana (recommended, signals "infra"), or pure FastHTML with custom trace viewer (more work, less professional)? **Recommend: FastHTML for controls, Grafana iframes for telemetry.**
3. **Auth backend:** Keycloak (already in COELHO Cloud?), Authentik, or roll-own JWT? **Recommend: existing COELHO Cloud SSO if available, else simple JWT for v1.**

---

## 9. Success criteria for the 5-min recruiter video

- [ ] Click DD → trigger a planner run → switch tab → see Sentinel trace explorer light up with N OTel spans
- [ ] Click YCS → trigger ingest → same Sentinel trace surface
- [ ] Click `/sentinel/demo` → DeepAgents 3-role flow runs → critic rejects first draft → research sub-agent re-runs → final draft passes
- [ ] Show Grafana failure-cluster heatmap with 1–2 intentionally-failed calls (PII mask trigger, rate-limit hit)
- [ ] Show LangFuse session view with full prompt/response chain
- [ ] Show policy YAML edit → restart Sentinel → new behavior immediately applied
- [ ] README architecture diagram matches this doc's §2.3
- [ ] GitHub repo public, Helm chart published

If all 8 are demonstrable, the portfolio piece is shippable.

---

## Sources

- [Optiveum — ML/LLMOps salary bands 2025-2026](https://optiveum.com/articles/machine-learning-engineer-salaries-by-country/)
- [JobseekersAE — UAE AI Engineer Salary 2026](https://jobseekers.ae/ai-engineer-salary-uae-2026/)
- [Second Talent — LLMOps Engineer 2026](https://www.secondtalent.com/occupations/llmops-engineer/)
- [Kore1 — AI Engineer Salary Guide 2026](https://www.kore1.com/ai-engineer-salary-guide/)
- [Digital Applied — MCP Adoption Stats 2026](https://www.digitalapplied.com/blog/mcp-adoption-statistics-2026-model-context-protocol)
- [MCP 2026 Roadmap](https://blog.modelcontextprotocol.io/posts/2026-mcp-roadmap/)
- [CData — 2026: The Year of Enterprise-Ready MCP](https://www.cdata.com/blog/2026-year-enterprise-ready-mcp-adoption)
- [MintMCP — Enterprise Gateway Patterns](https://www.mintmcp.com/blog/gateways-enterprise-engineering-with-mcp)
- [LangChain — Deep Agents](https://www.langchain.com/deep-agents)
- [DeepAgents — GitHub](https://github.com/langchain-ai/deepagents)
- [Latitude — Agent Observability Platforms 2026](https://latitude.so/blog/15-ai-agent-observability-platforms-2026-agentic-complexity)
- [AgentIncome — 2026 Playbook](https://agentincome.io/blog/make-money-with-ai-agents-2026/)
- [CallSphere — SG/SEA EU AI Act for Agents 2026](https://callsphere.ai/blog/agentic-ai-eu-ai-act-for-agents-in-singapore-southeast-asia-2026)
- [HuntingCube — Singapore Tech Hiring 2025](https://blog.huntingcube.ai/singapore-tech-hiring-2025-navigating-the-fintech-hft-and-ai-talent-landscape/)
- [FintechCareers — AML/Compliance 2026](https://www.fintechcareers.com/blog/aml-and-compliance-jobs-in-fintech-2026-salaries-demand-and-career-paths/)
