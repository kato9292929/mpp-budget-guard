#!/usr/bin/env bash
# End-to-end on Tempo TESTNET, no LLM: the SKILL.md Procedure, step by step,
# against a local 402 server.  probe.sh -> challenge.py analyze -> (allow) pay ->
# challenge.py record.  Two routes: /cheap (0.005, expect allow) and /expensive
# (0.25, expect deny by the $0.10 per-request cap; --cap-testnet applies caps).
# Usage: tests/e2e/run.sh        (needs `npm ci` in tests/e2e first)
set -euo pipefail
E2E="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$E2E/../.." && pwd)"
export E2E_VAR="${E2E_VAR:-$E2E/var}"
export MPP_BUDGET_GUARD_HOME="${MPP_BUDGET_GUARD_HOME:-$E2E_VAR/guard}"
PORT="${PORT:-8410}"
mkdir -p "$MPP_BUDGET_GUARD_HOME"

json() { python3 -c "import json,sys;d=json.load(sys.stdin);print($1)"; }
MERCHANT="$(node "$E2E/wallet.mjs" merchant --balance-only | json 'd["address"]')"
PAYER_JSON="$(node "$E2E/wallet.mjs" payer)"
PAYER_KEY="$(printf '%s' "$PAYER_JSON" | json 'd["keyFile"]')"

node "$E2E/server.mjs" "$MERCHANT" "$PORT" > "$E2E_VAR/server.log" 2>&1 &
SERVER_PID=$!
trap 'kill $SERVER_PID 2>/dev/null || true' EXIT
for _ in $(seq 1 50); do curl -s -o /dev/null "http://127.0.0.1:$PORT/" && break; sleep 0.2; done

for ROUTE in /cheap /expensive; do
    URL="http://127.0.0.1:$PORT$ROUTE"
    echo "=== $ROUTE"
    BEFORE="$(node "$E2E/wallet.mjs" payer --balance-only | json 'd["pathUSD"]')"
    # 1. probe (default POST + JSON)
    CAPTURE="$(NO_PROXY=127.0.0.1 no_proxy=127.0.0.1 "$ROOT/mpp-budget-guard/scripts/probe.sh" "$URL" | tail -1)"
    # 2. decide
    set +e
    DECISION="$(python3 "$ROOT/mpp-budget-guard/scripts/challenge.py" analyze "$CAPTURE" --cap-testnet)"; RC=$?
    set -e
    printf '%s' "$DECISION" | json 'json.dumps(d["verdict"])'
    if [ "$RC" -ne 0 ]; then
        echo "STOP (exit $RC): not paying"
    else
        # 3. hand off to the payer with the approved amount as a hard ceiling
        APPROVED="$(printf '%s' "$DECISION" | json 'd["decisions"][d["selected"]]["amount_base_units"]')"
        AMOUNT_USD="$(printf '%s' "$DECISION" | json 'd["verdict"]["amount_usd"]')"
        CID="$(printf '%s' "$DECISION" | json 'd["verdict"]["challenge_id"]')"
        PAID="$(NO_PROXY=127.0.0.1 no_proxy=127.0.0.1 node "$E2E/pay.mjs" "$URL" "$PAYER_KEY" --max-base-units "$APPROVED")"
        echo "$PAID"
        TX="$(printf '%s' "$PAID" | json 'd["receipt"]["reference"]')"
        # 4. record
        python3 "$ROOT/mpp-budget-guard/scripts/challenge.py" record --network testnet --amount-usd "$AMOUNT_USD" \
            --tx "$TX" --challenge-id "$CID" --url "$URL"
    fi
    AFTER="$(node "$E2E/wallet.mjs" payer --balance-only | json 'd["pathUSD"]')"
    echo "payer pathUSD base units: $BEFORE -> $AFTER (debited $((BEFORE - AFTER)))"
done
echo "decision log: $MPP_BUDGET_GUARD_HOME/decision-log.jsonl"
echo "ledger:       $MPP_BUDGET_GUARD_HOME/spend-ledger.jsonl"
