#!/usr/bin/env bash
# =============================================================================
# resolve_all_frameworks.sh
# =============================================================================
# Calls ecosyste.ms /packages/lookup?name={name} DIRECTLY (not via our
# resolver) for every framework listed in apps/fastapi/files/frameworks.txt,
# and saves the raw JSON response per-framework.
#
# This is a pure ecosyste.ms audit — no smart ranker, no D0 liveness, no
# catalog override. The output lets you see exactly what ecosyste.ms returns
# for each name so you can decide which frameworks need catalog overrides.
#
# Usage:
#   ./scripts/resolve_all_frameworks.sh
#   FRAMEWORKS_FILE=path OUTPUT_DIR=/tmp/x PARALLEL=8 ./resolve_all_frameworks.sh
#   FORCE=1 ./resolve_all_frameworks.sh   # ignore cached files
#
# Output:
#   {OUTPUT_DIR}/{slug}.json   raw ecosyste.ms array per framework
#   {OUTPUT_DIR}/_summary.csv  aggregated table: name, hits, has_doc_url, top_repo
#   {OUTPUT_DIR}/_failed.txt   names that returned HTTP error
#
# Dependencies: bash >= 4, curl, jq.
# =============================================================================

set -euo pipefail

# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

FRAMEWORKS_FILE="${FRAMEWORKS_FILE:-${REPO_ROOT}/apps/fastapi/files/frameworks.txt}"
OUTPUT_DIR="${OUTPUT_DIR:-${REPO_ROOT}/apps/fastapi/files/ecosystems_raw}"
ECOSYSTEMS_BASE="${ECOSYSTEMS_BASE:-https://packages.ecosyste.ms/api/v1/packages/lookup}"
PARALLEL="${PARALLEL:-4}"
FORCE="${FORCE:-0}"
TIMEOUT_SEC="${TIMEOUT_SEC:-30}"

# -----------------------------------------------------------------------------
# Pre-flight
# -----------------------------------------------------------------------------
for cmd in curl jq; do
  if ! command -v "${cmd}" >/dev/null 2>&1; then
    echo "ERROR: required command '${cmd}' not found" >&2
    exit 1
  fi
done

if [[ ! -f "${FRAMEWORKS_FILE}" ]]; then
  echo "ERROR: ${FRAMEWORKS_FILE} not found" >&2
  exit 1
fi

mkdir -p "${OUTPUT_DIR}"

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
slugify() {
  echo "$1" \
    | tr '[:upper:]' '[:lower:]' \
    | sed -e 's/#/_sharp/g' \
          -e 's/[^a-z0-9]\+/_/g' \
          -e 's/^_*//' -e 's/_*$//'
}

resolve_one() {
  local name="$1"
  local idx="$2"
  local total="$3"
  local slug
  slug="$(slugify "${name}")"
  local out_file="${OUTPUT_DIR}/${slug}.json"

  if [[ "${FORCE}" != "1" && -s "${out_file}" ]]; then
    local cached_hits
    cached_hits="$(jq 'length' "${out_file}" 2>/dev/null || echo "?")"
    printf '[%4d/%d] %-40s [CACHED] hits=%s\n' \
      "${idx}" "${total}" "${name}" "${cached_hits}"
    return 0
  fi

  # ecosyste.ms /packages/lookup?name= is CASE-SENSITIVE and matches
  # registry slugs literally — spaces never work. We try multiple
  # variants in order and keep the first non-empty response:
  #   1. lowercased original                  ('pydantic')
  #   2. spaces → hyphens                     ('apache airflow' → 'apache-airflow')
  #   3. last whitespace-token only           ('apache kafka' → 'kafka')
  #   4. strip ALL hyphens                    ('shap-iq' → 'shapiq')
  # Variant 3 recovers projects whose canonical registry slug omits a
  # vendor prefix (Apache, NVIDIA, etc.). Variant 4 recovers projects
  # whose registry slug joins multi-token names with no separator.
  local lc_name
  lc_name="$(printf '%s' "${name}" | tr '[:upper:]' '[:lower:]')"
  local hyphenated="${lc_name// /-}"
  local last_token="${lc_name##* }"
  local no_hyphens="${lc_name//-/}"
  no_hyphens="${no_hyphens// /}"   # also drop spaces from the no-hyphens variant

  # Build deduplicated variant list (preserve order, drop empties + dupes).
  local variants=()
  local seen_csv=","
  for v in "${lc_name}" "${hyphenated}" "${last_token}" "${no_hyphens}"; do
    [[ -z "${v}" ]] && continue
    [[ "${seen_csv}" == *",${v},"* ]] && continue
    variants+=("${v}")
    seen_csv="${seen_csv}${v},"
  done

  local tmp http_code matched_variant=""
  tmp="$(mktemp)"
  for variant in "${variants[@]}"; do
    local encoded="$(printf '%s' "${variant}" | jq -sRr @uri)"
    local url="${ECOSYSTEMS_BASE}?name=${encoded}"

    http_code="$(curl -sS "${url}" \
      -H 'User-Agent: COELHONexus-resolver/1.0' \
      -H 'Accept: application/json' \
      --max-time "${TIMEOUT_SEC}" \
      -o "${tmp}" -w '%{http_code}' || echo '000')"

    if [[ "${http_code}" != "200" ]]; then
      continue
    fi

    # Stop on the first variant that returns a non-empty array.
    local count
    count="$(jq 'length' "${tmp}" 2>/dev/null || echo "0")"
    matched_variant="${variant}"
    if [[ "${count}" -gt 0 ]]; then
      break
    fi
  done

  if [[ "${http_code}" != "200" ]]; then
    rm -f "${tmp}"
    printf '[%4d/%d] %-40s [FAIL HTTP %s]\n' \
      "${idx}" "${total}" "${name}" "${http_code}"
    {
      flock -x 200
      echo -e "${name}\t${http_code}" >> "${OUTPUT_DIR}/_failed.txt"
    } 200>"${OUTPUT_DIR}/.failed.lock"
    return 0
  fi

  mv "${tmp}" "${out_file}"

  # Concise progress with the matched variant when it differs from the original.
  local hits has_doc top_repo top_eco top_vers variant_tag=""
  hits="$(jq 'length' "${out_file}" 2>/dev/null || echo "0")"
  has_doc="$(jq '[.[] | select(.documentation_url != null)] | length' "${out_file}" 2>/dev/null || echo "0")"
  top_eco="$(jq -r '.[0].ecosystem // "—"' "${out_file}" 2>/dev/null || echo "—")"
  top_vers="$(jq -r '.[0].versions_count // "—"' "${out_file}" 2>/dev/null || echo "—")"
  top_repo="$(jq -r '.[0].repository_url // "∅"' "${out_file}" 2>/dev/null || echo "∅")"
  if [[ -n "${matched_variant}" && "${matched_variant}" != "${lc_name}" && "${hits}" -gt 0 ]]; then
    variant_tag=" via='${matched_variant}'"
  fi

  printf '[%4d/%d] %-40s hits=%-3s has_doc=%-3s top=[%s,v=%s]%s %s\n' \
    "${idx}" "${total}" "${name}" "${hits}" "${has_doc}" "${top_eco}" "${top_vers}" "${variant_tag}" "${top_repo:0:60}"
}

export -f resolve_one slugify
export OUTPUT_DIR ECOSYSTEMS_BASE TIMEOUT_SEC FORCE

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------
mapfile -t NAMES < <(grep -vE '^\s*$|^\s*#' "${FRAMEWORKS_FILE}" | sed 's/[[:space:]]*$//')
TOTAL="${#NAMES[@]}"

if [[ "${TOTAL}" -eq 0 ]]; then
  echo "No framework names in ${FRAMEWORKS_FILE}" >&2
  exit 1
fi

if [[ "${FORCE}" == "1" ]]; then
  : > "${OUTPUT_DIR}/_failed.txt"
fi

echo "==================================================================="
echo " ecosyste.ms direct audit — ${TOTAL} frameworks"
echo " Endpoint:    ${ECOSYSTEMS_BASE}"
echo " Output dir:  ${OUTPUT_DIR}"
echo " Parallelism: ${PARALLEL}"
echo "==================================================================="

START_TS="$(date +%s)"

# Sequential-with-batching: launch up to PARALLEL background jobs, wait
# for the batch, then start the next. Avoids xargs IFS-escaping
# nightmares with multi-word framework names.
pids=()
for ((i=0; i<TOTAL; i++)); do
  resolve_one "${NAMES[$i]}" "$((i+1))" "${TOTAL}" &
  pids+=($!)
  if (( ${#pids[@]} >= PARALLEL )); then
    wait "${pids[@]}"
    pids=()
  fi
done
[[ ${#pids[@]} -gt 0 ]] && wait "${pids[@]}"

# -----------------------------------------------------------------------------
# Aggregate CSV
# -----------------------------------------------------------------------------
SUMMARY="${OUTPUT_DIR}/_summary.csv"
{
  echo "framework,total_hits,hits_with_doc_url,registries_seen,top_ecosystem,top_versions,top_documentation_url,top_homepage,top_repository_url"
  for name in "${NAMES[@]}"; do
    slug="$(slugify "${name}")"
    f="${OUTPUT_DIR}/${slug}.json"
    [[ -s "${f}" ]] || continue
    jq -r --arg name "${name}" '
      [
        $name,
        (length | tostring),
        ([.[] | select(.documentation_url != null)] | length | tostring),
        ([.[].ecosystem] | unique | join(";")),
        (.[0].ecosystem // ""),
        ((.[0].versions_count // "") | tostring),
        (.[0].documentation_url // ""),
        (.[0].homepage // ""),
        (.[0].repository_url // "")
      ] | @csv
    ' "${f}" 2>/dev/null
  done
} > "${SUMMARY}"

# -----------------------------------------------------------------------------
# Report
# -----------------------------------------------------------------------------
END_TS="$(date +%s)"
ELAPSED="$((END_TS - START_TS))"

ZERO_HITS_N="$(awk -F, 'NR>1 {gsub(/"/,"",$2); if ($2=="0") c++} END {print c+0}' "${SUMMARY}")"
HAS_DOC_N="$(awk -F, 'NR>1 {gsub(/"/,"",$3); if ($3+0>0) c++} END {print c+0}' "${SUMMARY}")"
NO_DOC_N="$(awk -F, 'NR>1 {gsub(/"/,"",$2); gsub(/"/,"",$3); if ($2+0>0 && $3=="0") c++} END {print c+0}' "${SUMMARY}")"

FAILED_N=0
[[ -s "${OUTPUT_DIR}/_failed.txt" ]] && FAILED_N="$(wc -l < "${OUTPUT_DIR}/_failed.txt")"

echo
echo "==================================================================="
echo " DONE — ${TOTAL} frameworks in ${ELAPSED}s"
echo "==================================================================="
echo " ecosyste.ms results:"
echo "   has_documentation_url : ${HAS_DOC_N}    (cleanest path)"
echo "   hits but no doc url   : ${NO_DOC_N}    (will use repository_url fallback)"
echo "   zero hits             : ${ZERO_HITS_N}    (catalog override required)"
echo
echo " HTTP failures: ${FAILED_N}  (see ${OUTPUT_DIR}/_failed.txt)"
echo
echo " Per-framework JSON: ${OUTPUT_DIR}/{slug}.json"
echo " Aggregated CSV:     ${SUMMARY}"
echo "==================================================================="
