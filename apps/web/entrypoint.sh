#!/bin/bash

set -e

# Generate templ files on startup
templ generate

# Run with Air for hot-reload in development
exec air -c .air.toml
