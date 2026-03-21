#!/bin/sh

set -e

# Ensure tmp directory exists
mkdir -p /app/tmp

# Regenerate templ files on startup
templ generate

exec air -c .air.toml
