# mpp-budget-guard

A [Hermes Agent](https://github.com/NousResearch/hermes-agent) skill that checks an
HTTP 402 **before** anything is paid: the per-request cap, the running total, the
currency and the expiry. It reads both the MPP `WWW-Authenticate: Payment`
challenge and the x402 `PAYMENT-REQUIRED` offer, and never pays, signs or reads a
wallet key. Paying stays with the official `mpp-agent` skill
(`official/payments/mpp-agent`).

## Install

```bash
hermes skills install official/payments/mpp-agent     # the payer (official)
hermes skills install <identifier>                    # this guard; see docs/results.md D5
```

Then restart Hermes, or run `/reload-skills` (needed on Hermes ≤ 0.19.0).

## What's inside

| path | what |
|---|---|
| `SKILL.md` | When to Use / Quick Reference / Procedure / Pitfalls / Verification |
| `scripts/probe.sh` | fetch a 402 without paying (default POST + JSON) |
| `scripts/challenge.py` | decode MPP + x402, decide allow/deny, write the decision log and ledger (stdlib only) |
| `references/pitfalls.md` | what was verified, with reproduction steps |
| `tests/` | `python3 -m unittest discover -s tests` (50 tests); `tests/e2e/run.sh` is the testnet end-to-end run |
| `docs/results.md` | test and install results (D1–D5) |

This README lives in `docs/` because Hermes's skill linter flags a `README.md` shipped at the skill root.

## Requirements

`bash`, `curl` and Python 3.10+ for `probe.sh` and `challenge.py`. Node.js 20+ only
for the e2e harness. The skill reads no key and needs no environment variables or
secrets; `MPP_BUDGET_GUARD_HOME` (default `~/.mpp-budget-guard`)
only moves the log directory.

For on-chain limits (Tempo access keys), see mpp.dev, Managing agent spend: https://mpp.dev/guides/managing-agent-spend

## License

MIT
