#!/usr/bin/env bash
# COELHONexus web — one-shot build pipeline.
# Generates Templ → compiles Tailwind → builds Go binary.
#
# Prereqs (install once; all are single binaries, no Node.js required):
#   go install github.com/a-h/templ/cmd/templ@latest
#   curl -Lo bin/tailwindcss \
#     https://github.com/tailwindlabs/tailwindcss/releases/latest/download/tailwindcss-linux-x64
#   chmod +x bin/tailwindcss

set -euo pipefail
cd "$(dirname "$0")/.."

echo "▶ templ generate"
templ generate

echo "▶ tailwindcss build"
./bin/tailwindcss \
  -i static/css/input.css \
  -o static/css/main.css \
  --minify

echo "▶ go build"
go build -o bin/web .

echo "✓ build complete → bin/web"
