#!/bin/bash
set -euo pipefail

REDIS_HOST="${REDIS_HOST:-localhost}"
REDIS_PORT="${REDIS_PORT:-6379}"
REDIS_PASSWORD="${REDIS_PASSWORD:-}"

usage() {
    echo "Usage: $0 <key> [options]"
    echo ""
    echo "Options:"
    echo "  -h, --host      Redis host (default: \$REDIS_HOST or localhost)"
    echo "  -p, --port      Redis port (default: \$REDIS_PORT or 6379)"
    echo "  -a, --auth      Redis password (default: \$REDIS_PASSWORD)"
    echo "  -t, --type      Show only key type"
    echo "  -l, --len       Show only length (for arrays/lists)"
    echo "  --keys          List all keys matching pattern (use * for all)"
    echo ""
    echo "Examples:"
    echo "  $0 'coelhonexus:youtube:channel:datena:videos'"
    echo "  $0 'coelhonexus:*' --keys"
    echo "  $0 'mykey' -h redis.local -p 6379 -a secret"
    exit 1
}

[[ $# -eq 0 ]] && usage

KEY=""
TYPE_ONLY=false
LEN_ONLY=false
LIST_KEYS=false

while [[ $# -gt 0 ]]; do
    case $1 in
        -h|--host) REDIS_HOST="$2"; shift 2 ;;
        -p|--port) REDIS_PORT="$2"; shift 2 ;;
        -a|--auth) REDIS_PASSWORD="$2"; shift 2 ;;
        -t|--type) TYPE_ONLY=true; shift ;;
        -l|--len) LEN_ONLY=true; shift ;;
        --keys) LIST_KEYS=true; shift ;;
        -*) echo "Unknown option: $1"; usage ;;
        *) KEY="$1"; shift ;;
    esac
done

[[ -z "$KEY" ]] && usage

AUTH_ARGS=()
[[ -n "$REDIS_PASSWORD" ]] && AUTH_ARGS=(-a "$REDIS_PASSWORD" --no-auth-warning)

rcli() {
    redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" "${AUTH_ARGS[@]}" "$@"
}

if $LIST_KEYS; then
    echo "Keys matching '$KEY':"
    rcli KEYS "$KEY"
    exit 0
fi

KEY_TYPE=$(rcli TYPE "$KEY" | tr -d '\r')

if [[ "$KEY_TYPE" == "none" ]]; then
    echo "Key not found: $KEY"
    exit 1
fi

if $TYPE_ONLY; then
    echo "$KEY_TYPE"
    exit 0
fi

echo "Key: $KEY"
echo "Type: $KEY_TYPE"

case "$KEY_TYPE" in
    ReJSON-RL)
        if $LEN_ONLY; then
            rcli JSON.ARRLEN "$KEY" '$'
        else
            echo "Value:"
            rcli JSON.GET "$KEY" '$' | jq '.[0]' 2>/dev/null || rcli JSON.GET "$KEY" '$'
        fi
        ;;
    string)
        echo "Value:"
        rcli GET "$KEY"
        ;;
    list)
        LEN=$(rcli LLEN "$KEY")
        echo "Length: $LEN"
        if ! $LEN_ONLY; then
            echo "Value (first 10):"
            rcli LRANGE "$KEY" 0 9
        fi
        ;;
    hash)
        echo "Fields:"
        rcli HGETALL "$KEY"
        ;;
    set)
        LEN=$(rcli SCARD "$KEY")
        echo "Length: $LEN"
        if ! $LEN_ONLY; then
            echo "Members (first 10):"
            rcli SSCAN "$KEY" 0 COUNT 10
        fi
        ;;
    zset)
        LEN=$(rcli ZCARD "$KEY")
        echo "Length: $LEN"
        if ! $LEN_ONLY; then
            echo "Members (first 10):"
            rcli ZRANGE "$KEY" 0 9 WITHSCORES
        fi
        ;;
    *)
        echo "Unknown type: $KEY_TYPE"
        ;;
esac
