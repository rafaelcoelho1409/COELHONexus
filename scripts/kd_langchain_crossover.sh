#!/usr/bin/env bash
# Fire a Knowledge Distiller study on the DeepAgents + LangChain + LangGraph
# crossover against PRODUCTION (port 23000).
#
# The crossover resolver (POST /studies/resolve, 2026-04-23) coalesced the
# 3 frameworks into a single study because they share `docs.langchain.com`.
# primary_docs_url and tier are pinned here to skip a second resolver call.
#
# Expected behavior (commit e1ef7d4, pre-Tier-0 vault):
#   - Ingest: Tier 1 fast-path, ~5s (https://docs.langchain.com/llms-full.txt)
#   - MAP + REDUCE: cache HIT if manifest_hash matches a prior run (else ~25 min)
#   - Synth: N chapters in parallel, ~13 min each on NIM reasoning models
#   - Curator + Critic + Assembler: ~15 min combined
#   - Total: ~60-90 min end-to-end (plan-cache hit reduces this significantly)
#
# Known risk on this commit:
#   Chapters assigned many large files can push the synth prompt past 180K
#   chars and trigger NIM gateway timeouts + Groq 413 rate limits. The
#   2026-04-22 LangChain run hit this on ch03. Tier 0 vault wiring (dev only,
#   uncommitted) compresses prompts via code-block placeholder substitution
#   and mitigates this — but this script targets PROD, so Tier 0 is NOT active.
#
# Usage:
#   bash scripts/kd_langchain_crossover.sh
#
# After firing, check progress:
#   curl -sS http://localhost:23000/api/v1/knowledge/studies/<study_id> | python3 -m json.tool
#
# Stream live events (SSE):
#   curl -N http://localhost:23000/api/v1/knowledge/studies/<study_id>/stream
#
# To personalize the synth output, edit the `user_profile` fields below —
# `mastered_technologies` lets the synthesizer skip intros on tech you know,
# `portfolio_refs` lets it cross-reference your projects in examples.

set -euo pipefail

API="http://localhost:23000/api/v1/knowledge/studies"

read -r -d '' PAYLOAD <<'JSON' || true
{
  "framework": "DeepAgents + LangChain + LangGraph",
  "docs_url": "https://docs.langchain.com/oss/python/deepagents/overview",
  "tier": 1,
  "version": "latest",
  "user_id": "rafaelcoelho1409",
  "user_profile": {
    "level": "senior",
    "acceptance_threshold": 0.85,
    "mastered_technologies": [
      "FastAPI",
      "Python",
      "Celery",
      "Redis",
      "LangChain",
      "Next.js",
      "React",
      "Kubernetes"
    ],
    "portfolio_refs": [
      "COELHO Nexus"
    ],
    "target_markets": []
  }
}
JSON

echo "[kd] firing crossover study on ${API}"
echo "[kd] payload:"
echo "${PAYLOAD}" | python3 -m json.tool
echo ""

RESPONSE=$(curl -sS -X POST "${API}" \
  -H "Content-Type: application/json" \
  -d "${PAYLOAD}" \
  --max-time 180)

echo "[kd] server response:"
echo "${RESPONSE}" | python3 -m json.tool

STUDY_ID=$(echo "${RESPONSE}" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('study_id',''))")

if [[ -n "${STUDY_ID}" ]]; then
  echo ""
  echo "[kd] study_id: ${STUDY_ID}"
  echo "[kd] study_root: $(echo "${RESPONSE}" | python3 -c "import json,sys; print(json.load(sys.stdin).get('study_root',''))")"
  echo ""
  echo "[kd] check status:"
  echo "    curl -sS http://localhost:23000/api/v1/knowledge/studies/${STUDY_ID} | python3 -m json.tool"
  echo "[kd] stream events (SSE, tail -f equivalent):"
  echo "    curl -N http://localhost:23000/api/v1/knowledge/studies/${STUDY_ID}/stream"
  echo "[kd] inspect MinIO artifacts (when complete):"
  echo "    mc ls --recursive local/knowledge-artifacts/rafaelcoelho1409/knowledge/"
fi
