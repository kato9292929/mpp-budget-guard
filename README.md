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
hermes skills install kato9292929/mpp-budget-guard/mpp-budget-guard   # this guard
```

Then restart Hermes, or run `/reload-skills` (needed on Hermes ≤ 0.19.0).

## What's inside

| path | what |
|---|---|
| `mpp-budget-guard/SKILL.md` | the skill (this directory is what gets installed): When to Use / Quick Reference / Procedure / Pitfalls / Verification |
| `mpp-budget-guard/scripts/probe.sh` | fetch a 402 without paying (default POST + JSON) |
| `mpp-budget-guard/scripts/challenge.py` | decode MPP + x402, decide allow/deny, write the decision log and ledger (stdlib only) |
| `mpp-budget-guard/references/pitfalls.md` | what was verified, with reproduction steps |
| `tests/` | not installed; `python3 -m unittest discover -s tests` (50 tests); `tests/e2e/run.sh` is the testnet end-to-end run |
| `docs/results.md` | test and install results (D1–D5) |

The skill lives in the `mpp-budget-guard/` subdirectory, so it installs on older Hermes versions too. Hermes 0.19.0 cannot install a skill whose SKILL.md is at the repository root.

## Requirements

`bash`, `curl` and Python 3.10+ for `probe.sh` and `challenge.py`. Node.js 20+ only
for the e2e harness. The skill reads no key and needs no environment variables or
secrets; `MPP_BUDGET_GUARD_HOME` (default `~/.mpp-budget-guard`)
only moves the log directory.

For on-chain limits (Tempo access keys), see mpp.dev, Managing agent spend: https://mpp.dev/guides/managing-agent-spend

## License

MIT
