---
name: mpp-budget-guard
description: Before paying any HTTP 402 (MPP/x402), check caps and expiry
version: 0.1.0
author: kato9292929
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [Payments, MPP, x402, HTTP-402, Budget]
    related_skills: [mpp-agent]
---

# MPP Budget Guard

Use this before paying any HTTP 402 (MPP or x402). It checks the per-request cap,
the running total, the currency and the expiry. The payment itself is then made
with the `mpp-agent` skill.

A pre-payment check for the official `mpp-agent` skill. It reads a 402 **without
paying**, decodes the MPP `WWW-Authenticate: Payment` challenge and the x402
`PAYMENT-REQUIRED` offer in the same response, and decides **allow / deny**
against a per-request cap, a running-total cap, a known-currency list and the
challenge's `expires`. It never pays, never signs, and never reads a wallet key:
paying stays with `mpp-agent` (mppx / Tempo Wallet).

Everything below was checked in practice (mppx 0.8.13, Hermes 0.22.2, Tempo
testnet and an unpaid probe of a live mainnet 402). Where something is an
inference, it says so.

## When to Use

- Before handing any 402 to `mpp-agent` / `mppx`, whenever the user has a budget
  ("no more than $0.10 a call", "$0.50 total") or simply wants the price and
  recipient checked first.
- When a 402 must be inspected to see what it asks for (amount, token, chain,
  recipient, expiry, and which of MPP / x402 it offers) without paying.
- Not for paying: once this skill says `allow`, load `mpp-agent` and pay there.
- Not for setting up a wallet or for server-side 402s (see `mpp-agent`).

## Quick Reference

| Step | Command (run with the `terminal` tool) | Result |
|---|---|---|
| Probe (no payment) | `bash ${HERMES_SKILL_DIR}/scripts/probe.sh <url> [--method GET] [--data '<json>']` | last stdout line = capture path; exit 0 only on HTTP 402 |
| Decide | `python3 ${HERMES_SKILL_DIR}/scripts/challenge.py analyze <capture> [--per-request-cap 0.10] [--total-cap 0.50]` | JSON with `verdict`; exit 0 allow, 3 deny, 2 undecodable |
| Record a payment | `python3 ${HERMES_SKILL_DIR}/scripts/challenge.py record --amount-usd <usd> --tx <hash> [--network mainnet]` | appends to the ledger used for the total cap |
| Bundled | `scripts/probe.sh`, `scripts/challenge.py`, `references/pitfalls.md` | all under `${HERMES_SKILL_DIR}` |
| Files | `~/.mpp-budget-guard/decision-log.jsonl`, `spend-ledger.jsonl`, `probes/` | override the directory with `MPP_BUDGET_GUARD_HOME` |

Defaults: $0.10 per request, $0.50 total (mainnet ledger). Testnet challenges
are not capped unless `--cap-testnet` is passed. Known currencies: Tempo
mainnet USDC `0x20C0…8b50` (chainId 4217) and Tempo testnet pathUSD `0x20c0…0000`
(chainId 42431), both 6 decimals. Anything else is denied.

## Procedure

1. **Get the 402 without paying.**
   `bash ${HERMES_SKILL_DIR}/scripts/probe.sh <url> --data '<the JSON body the call needs>'`.
   The default is POST with a JSON body, because POST-only endpoints answer a GET with
   404 instead of 402 (seen on Exa `/search`). Use `--method GET` for GET routes.
   If the exit code is not 0, it was not a 402: report the status and stop.
2. **Decide.** `python3 ${HERMES_SKILL_DIR}/scripts/challenge.py analyze <capture path>`
   with the user's caps if they gave any. On exit 3 (deny) or 2 (undecodable), show
   `verdict.reason` to the user and **stop. Do not pay.** Raising a cap is the
   user's decision, never yours.
3. **Pay only on allow.** Load the `mpp-agent` skill and pay the same URL with the
   same method and body (for example `mppx <url> --method POST -H 'content-type: application/json' --data '<json>'`).
   Tell the user the amount and recipient from `verdict` / `mpp[selected]` first.
4. **Record.** Take the tx hash from the payment receipt (`reference` in mppx's
   receipt) and run
   `python3 ${HERMES_SKILL_DIR}/scripts/challenge.py record --amount-usd <verdict.amount_usd> --tx <hash> --challenge-id <verdict.challenge_id> --url <url>`.
   The decision itself is already in `decision-log.jsonl` from step 2.

For a limit enforced on-chain as well (Tempo access key: per-period amount,
allowed recipients, expiry), point the user to mpp.dev's guide, Managing agent
spend (https://mpp.dev/guides/managing-agent-spend). Their wallet authorizes the key. This skill does not create keys and
never asks for or handles one. See `references/pitfalls.md` §4 for what was observed with such a key.

## Pitfalls

Details and reproduction steps: `references/pitfalls.md`.

1. **mppx `maxAmount` is ignored for Tempo.** In mppx 0.8.13 `maxAmount` exists on the
   `evm` method (and x402 `exact`) only. Passed to the `tempo` method, it is silently
   ignored, and a $0.25 challenge was paid with `maxAmount: '0.10'` set.
2. **`acceptPaymentPolicy.origins` is not a spending limit.** It only controls
   whether the `Accept-Payment` header is added. A 402 from an origin outside the
   list was still paid.
3. **To stop Tempo payments in code, throw from `onChallenge`**, or run this skill's
   check first. Returning `undefined` from `onChallenge` still pays.
4. **Tempo access key: leave headroom.** The 2nd 0.005 payment under a 0.01/day
   limit was refused after the 1st debited 5048 base units. That suggests the fee
   counts toward the limit (an inference, not a documented rule).
5. **Hermes ≤ 0.19.0 caches the skill list per process.** After installing, restart
   Hermes or run `/reload-skills`. `/reset` alone did not show a newly installed
   skill (0.22.2 picks it up without a restart).
6. **Paid MCP tools (MPP over MCP, JSON-RPC `-32042`) cannot be paid from Hermes.**
   Hermes passes only the tool `arguments`, so a credential never reaches
   `params._meta`. The model also sees only the error message, not
   `error.data.challenges`.
7. **The paid request gets a new challenge.** Each 402 carries a fresh challenge
   `id`, so mppx pays the one it receives, not the probed one. This skill's
   decision assumes the price does not change between the probe and the
   payment. Compare the amount mppx reports with the approved one.
8. **x402-only 402s are denied here**, with the reason `no MPP … challenge`.
   `mpp-agent` pays MPP challenges; the x402 offer is decoded for information only.

## Verification

- The decision: `tail -n 1 ~/.mpp-budget-guard/decision-log.jsonl` shows
  `verdict.decision`, `verdict.reason`, the amount, the currency, `expires`, and
  `spent_before_usd`.
- The payment: `tail -n 1 ~/.mpp-budget-guard/spend-ledger.jsonl` shows the `tx` you
  recorded. Its total is printed as `mainnet_total_usd` / `testnet_total_usd` by
  `record`.
- The tx on chain: look the hash up on the Tempo explorer for that network.
- The balance: `mppx account view` (or your Tempo Wallet) before and after. In the
  testnet run, a 0.005 payment debited 5031 base units (the amount plus a fee).
- The skill itself: the tests are not installed with the skill. In a checkout of
  https://github.com/kato9292929/mpp-budget-guard, `python3 -m unittest discover -s tests` → 50 tests, OK.
