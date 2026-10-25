#!/usr/bin/env bash
# mpp-budget-guard: fetch a 402 WITHOUT paying and save it as a `curl -i` capture
# for scripts/challenge.py. Plain curl: no wallet, no key, no Authorization header.
#
#   probe.sh <url> [--method POST] [--data '{"query":"test"}'] [-H 'k: v']... [--out FILE]
#
# Default is POST with a JSON body ('{}'), because POST-only endpoints (e.g. Exa
# /search) answer a GET with 404 instead of 402. Use --method GET for GET routes.
# Prints the capture path on the last line of stdout. Exit 0 on HTTP 402,
# 1 on any other status (capture still written), 2 on usage/transport error.
set -euo pipefail

URL=""; METHOD="POST"; DATA="{}"; OUT=""; HEADERS=()
while [ "$#" -gt 0 ]; do
    case "$1" in
        --method|-X) METHOD="$2"; shift 2;;
        --data|-d) DATA="$2"; shift 2;;
        --header|-H) HEADERS+=(-H "$2"); shift 2;;
        --out) OUT="$2"; shift 2;;
        http://*|https://*) URL="$1"; shift;;
        *) echo "unknown arg: $1" >&2; exit 2;;
    esac
done
[ -n "$URL" ] || { echo "usage: probe.sh <url> [--method M] [--data JSON] [-H 'k: v'] [--out FILE]" >&2; exit 2; }

if [ -z "$OUT" ]; then
    DIR="${MPP_BUDGET_GUARD_HOME:-$HOME/.mpp-budget-guard}/probes"
    mkdir -p "$DIR"
    OUT="$DIR/$(date -u +%Y%m%dT%H%M%SZ)-$$.http"
fi

ARGS=(-sS -i --max-time 30 -X "$METHOD")
if [ "$METHOD" != "GET" ] && [ "$METHOD" != "HEAD" ]; then
    ARGS+=(-H 'content-type: application/json' --data "$DATA")
fi
curl "${ARGS[@]}" ${HEADERS[@]+"${HEADERS[@]}"} "$URL" > "$OUT" || { echo "curl failed for $URL" >&2; exit 2; }

# Last status line wins: a proxy's "200 Connection Established" can precede the real one.
STATUS="$(grep -E '^HTTP/[0-9.]+ [0-9]{3}' "$OUT" | grep -v 'Connection Established' | tail -1 | awk '{print $2}')"
echo "status: ${STATUS:-unknown}  method: $METHOD  url: $URL"
echo "$OUT"
[ "$STATUS" = "402" ]
