#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

ICONS_DIR="$SCRIPT_DIR/icons"
mkdir -p "$ICONS_DIR"

# ── Icon downloads ──────────────────────────────────────────
# Uses devicon CDN (jsdelivr) for standard tech icons as SVGs.
# Local icons already in diagrams/icons/ are referenced directly.
declare -A ICONS=(
  [terraform]="https://cdn.jsdelivr.net/gh/devicons/devicon/icons/terraform/terraform-original.svg"
  [docker]="https://cdn.jsdelivr.net/gh/devicons/devicon/icons/docker/docker-original.svg"
  [gitlab]="https://cdn.jsdelivr.net/gh/devicons/devicon/icons/gitlab/gitlab-original.svg"
  [helm]="https://cdn.jsdelivr.net/gh/devicons/devicon/icons/helm/helm-original.svg"
  [redis]="https://cdn.jsdelivr.net/gh/devicons/devicon/icons/redis/redis-original.svg"
  [python]="https://cdn.jsdelivr.net/gh/devicons/devicon/icons/python/python-original.svg"
  [grafana]="https://cdn.jsdelivr.net/gh/devicons/devicon/icons/grafana/grafana-original.svg"
  [postgresql]="https://cdn.jsdelivr.net/gh/devicons/devicon/icons/postgresql/postgresql-original.svg"
  [argocd]="https://cdn.jsdelivr.net/gh/devicons/devicon/icons/argocd/argocd-original.svg"
  [neo4j]="https://cdn.jsdelivr.net/gh/devicons/devicon/icons/neo4j/neo4j-original.svg"
  [elasticsearch]="https://cdn.jsdelivr.net/gh/devicons/devicon/icons/elasticsearch/elasticsearch-original.svg"
  [fasthtml]="https://www.fastht.ml/docs/logo.svg"
  [langfuse]="https://langfuse.com/langfuse-wordart.svg"
  # New icons not in devicons — sourced from project catalogs / brand CDNs
  # simpleicons version: fill="#DC244C" colored icon mark (qdrant.tech logo is white-on-white)
  [qdrant]="https://cdn.simpleicons.org/qdrant"
  [playwright]="https://cdn.jsdelivr.net/gh/devicons/devicon/icons/playwright/playwright-original.svg"
  [gemini]="https://cdn.simpleicons.org/googlegemini"
  # Standalone hexagon icon (128x129, colored) — favicon from docs.terragrunt.com
  [terragrunt]="https://docs.terragrunt.com/favicon.svg"
  # LLM provider icons — sourced from LiteLLM dashboard logos collection
  [groq]="https://raw.githubusercontent.com/BerriAI/litellm/main/ui/litellm-dashboard/public/assets/logos/groq.svg"
  [cerebras]="https://raw.githubusercontent.com/BerriAI/litellm/main/ui/litellm-dashboard/public/assets/logos/cerebras.svg"
  [nvidia_nim]="https://raw.githubusercontent.com/BerriAI/litellm/main/ui/litellm-dashboard/public/assets/logos/nvidia_nim.svg"
  [sambanova]="https://raw.githubusercontent.com/BerriAI/litellm/main/ui/litellm-dashboard/public/assets/logos/sambanova.svg"
  [mistral]="https://cdn.simpleicons.org/mistralai"             # orange #FA520F
  [deepseek]="https://cdn.simpleicons.org/deepseek"             # blue #5786FE
  # MCP tool source icons
  [arxiv]="https://cdn.simpleicons.org/arxiv"                   # dark red #B31B1B
  [semanticscholar]="https://cdn.simpleicons.org/semanticscholar"  # blue #1857B6
  [ycombinator]="https://cdn.simpleicons.org/ycombinator"       # orange #F0652F (HN)
  [huggingface]="https://huggingface.co/front/assets/huggingface_logo-noborder.svg"
  # DeepAgents wordmark logo (sources.yaml)
  [deepagents]="https://raw.githubusercontent.com/langchain-ai/deepagents/main/.github/images/logo-dark.svg"
)

# PNG and custom icons
declare -A PNG_ICONS=(
  [rancher]="https://raw.githubusercontent.com/rancher/docs/refs/heads/main/static/img/icon-rancher.svg"
  [minio]="https://raw.githubusercontent.com/simple-icons/simple-icons/refs/heads/develop/icons/minio.svg"
  [celery]="https://docs.celeryq.dev/en/stable/_static/celery_512.png"
  # Square logo from GoogleContainerTools repo (avoids white-on-white skaffold.dev version)
  [skaffold]="https://raw.githubusercontent.com/GoogleContainerTools/skaffold/main/logo/skaffold.png"
  [fastmcp]="https://mintcdn.com/fastmcp/Lu2sdJVHDyHdvswk/assets/brand/wordmark.png?fit=max&auto=format&n=Lu2sdJVHDyHdvswk&q=85&s=67680e9b1c641023511881a24f296077"
  [alloy]="https://grafana.com/media/docs/alloy/alloy_icon.png"
  [loki]="https://grafana.com/media/docs/loki/logo-grafana-loki.png"
  [mimir]="https://grafana.com/media/docs/mimir/GrafanaLogo_Mimir_icon.png"
  [tempo]="https://grafana.com/static/assets/img/blog/tempo.png"
  [fastapi]="https://fastapi.tiangolo.com/img/logo-margin/logo-teal.png"
  # Square gear icon instead of the 1972×692 horizontal wordmark
  [opentelemetry]="https://opentelemetry.io/img/logos/opentelemetry-icon-color.png"
  # LiteLLM + LangGraph — from LiteLLM dashboard logos collection
  [litellm_logo]="https://raw.githubusercontent.com/BerriAI/litellm/main/ui/litellm-dashboard/public/assets/logos/litellm_logo.jpg"
  [langgraph]="https://raw.githubusercontent.com/BerriAI/litellm/main/ui/litellm-dashboard/public/assets/logos/langgraph.png"
)

echo "Downloading SVG icons..."
for name in "${!ICONS[@]}"; do
  dest="$ICONS_DIR/${name}.svg"
  if [[ -f "$dest" ]]; then
    echo "  --  ${name}.svg (cached)"
  else
    if curl -sfL -o "$dest" "${ICONS[$name]}"; then
      echo "  OK  ${name}.svg"
    else
      echo "  FAIL ${name}.svg"
    fi
  fi
done

echo "Downloading PNG/custom icons..."
for name in "${!PNG_ICONS[@]}"; do
  url="${PNG_ICONS[$name]}"
  url_path="${url%%\?*}"
  ext="${url_path##*.}"
  dest="$ICONS_DIR/${name}.${ext}"
  if [[ -f "$dest" ]]; then
    echo "  --  ${name}.${ext} (cached)"
  else
    if curl -sfL -o "$dest" "$url"; then
      echo "  OK  ${name}.${ext}"
    else
      echo "  FAIL ${name}.${ext}"
    fi
  fi
done

# ── Generate diagrams ────────────────────────────────────────
echo ""
echo "Generating D2 diagrams..."

DIAGRAMS=(
  "coelho_nexus_architecture"
  "coelho_nexus_ai_domains"
)

for diagram in "${DIAGRAMS[@]}"; do
  echo ""
  echo "  → ${diagram}.d2"

  d2 --layout=elk \
     --scale=0.6 \
     --pad=40 \
     "${diagram}.d2" \
     "${diagram}.svg" 2>&1

  echo "  Diagram generated: ${diagram}.svg"

  # ── Convert SVG to PNG ──────────────────────────────────────
  if command -v rsvg-convert &>/dev/null; then
    rsvg-convert -d 300 -p 300 \
      "${diagram}.svg" \
      -o "${diagram}.png" 2>&1
    echo "  Diagram converted: ${diagram}.png"
  else
    echo "  SKIP PNG conversion (rsvg-convert not found)"
  fi
done

# ── Download PanZoom JS Library ──────────────────────────────
echo ""
echo "Checking PanZoom library..."
PANZOOM_DEST="$SCRIPT_DIR/panzoom.min.js"
if [[ ! -f "$PANZOOM_DEST" ]]; then
  echo "  Downloading panzoom.min.js..."
  curl -sfL -o "$PANZOOM_DEST" "https://unpkg.com/panzoom@9.4.0/dist/panzoom.min.js"
  echo "  OK panzoom.min.js"
else
  echo "  -- panzoom.min.js (cached)"
fi

# ── Compile Unified Viewer ──────────────────────────────────
echo ""
if command -v python3 &>/dev/null; then
  python3 "$SCRIPT_DIR/compile_viewer.py"
else
  echo "  FAIL: python3 not found, skipping index.html compilation."
fi

echo ""
echo "All done!"
