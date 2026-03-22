#!/bin/sh

set -e

# Ensure tmp directory exists
mkdir -p /app/tmp

# Regenerate templ files on startup (if they exist)
if [ -d "templates" ] && command -v templ &> /dev/null; then
    templ generate
fi

exec air -c .air.toml
