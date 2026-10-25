# Pitfalls: details and how to reproduce them

All of these were observed between 2026-10-20 and 2026-10-25 with mppx 0.8.13,
viem 2.57.7 and Hermes 0.22.2 (0.19.0 for comparison), on Tempo **testnet**
(moderato, chainId 42431), plus an unpaid probe of `https://api.exa.ai/search`.
Nothing here was run with real money. The original experiment scripts are in the
author's `mpp-agent-run/lab` (gates.mjs, accesskey.mjs, mcp-server.mjs,
hermes/skill_cache_repro.py, hermes/mcp_call.py). Minimal versions are below.

Common setup for 1–4: a local mppx server on testnet that issues `tempo` charges
(this repo's `tests/e2e/server.mjs`, routes `/cheap` 0.005 and `/expensive` 0.25),
and a payer funded from the testnet faucet (`tests/e2e/wallet.mjs payer`).

## 1. `maxAmount` is ignored for the Tempo method

```js
import { Mppx, tempo } from 'mppx/client'
const mppx = Mppx.create({ polyfill: false, methods: [tempo({ account, maxAmount: '0.10' })] })
await mppx.fetch('http://127.0.0.1:8410/expensive')   // 0.25 → status 200, paid
```

Observed: paid, 250034 base units debited. In mppx 0.8.13 `maxAmount` is checked
only in `evm/client/Charge.ts` and `x402/client/Exact.ts`. With the `evm` method,
`maxAmount: '0.10'` plus currency metadata refused a 0.25 challenge before signing
(`EVM charge amount exceeds maxAmount.`). Without currency metadata it refused for
a different reason (`EVM charge maxAmount requires currency decimals.`).

## 2. `acceptPaymentPolicy.origins` is not a spending limit

```js
Mppx.create({ polyfill: false, methods: [tempo({ account })],
              acceptPaymentPolicy: { origins: ['https://api.exa.ai'] } })
// fetch http://127.0.0.1:8410/expensive  → paid 0.25
```

Observed: paid. The only difference was that the `Accept-Payment` request header
was not sent. In the source, the policy is used only to decide whether to add that
header (`client/internal/Fetch.ts`, `shouldInjectForPolicy`).

## 3. Stopping a Tempo payment in code: throw from `onChallenge`

```js
Mppx.create({ polyfill: false, methods: [tempo({ account })],
  onChallenge: async (c) => {
    if (BigInt(c.request.amount) > 100000n) throw new Error('over cap')
    return undefined            // falls through to createCredential → pays
  } })
```

Observed: `/cheap` was paid; `/expensive` threw before anything was signed (0 debited).
Returning `undefined` does not refuse; mppx then creates the credential and pays.
`tests/e2e/pay.mjs` uses this to cap the payment at the amount `challenge.py` approved.

## 4. Tempo access key: the fee appears to count toward the limit

`scripts/create-access-key.mjs` calls viem `Actions.accessKey.authorize` with
`limits: [{ token, limit, period }]`, a `scopes` recipient allowlist for TIP-20
`transfer` / `transferWithMemo`, and `expiry`. No login is needed, only the root
key's signature, which is why the user runs it, not the agent.

Observed on testnet with limit 0.01 pathUSD per 86400 s and a single allowed recipient:

| payment via mppx with the access key | result |
|---|---|
| 0.005 → allowed recipient | paid; root debited **5048** base units |
| 0.005 → allowed recipient (2nd) | reverted `Account keychain error: SpendingLimitExceeded` |
| 0.005 → another recipient | reverted `Account keychain error: CallNotAllowed` |

0.005 + 0.005 equals the limit, yet the 2nd payment was refused, and the 1st
debited 5048 rather than 5000. So the fee most likely counts toward the limit.
This is **an inference** from these numbers. Size limits with headroom.

The access key is used by the mppx **client library**
(`tempo({ account: Account.fromSecp256k1(key, { access: root }) })`). The mppx
**CLI** 0.8.13 has no access-key option (no `accessKey` in `src/cli`).

## 5. Hermes ≤ 0.19.0: a newly installed skill is invisible until restart

Run a long-lived process that builds the skills index, install a skill from a
second process, then build the index again in the first process (which is what
`/reset` does):

| | 0.19.0 | 0.22.2 |
|---|---|---|
| in the index in the same process after install (= `/reset`) | **no** | yes |
| after a restart | yes | yes |

The cause: 0.19.0 keys its in-process skills-prompt cache on configuration only;
0.22.2 adds a manifest of the SKILL.md files to the key (`agent/prompt_builder.py`).
Fix: restart Hermes, or run `/reload-skills`, or upgrade. Also install into the
`HERMES_HOME` the chat actually uses (`hermes -p <profile>` means
`~/.hermes/profiles/<profile>`).

## 6. Paid MCP tools cannot be paid from Hermes

A local MCP server with an mppx `Transport.mcpSdk()` paid tool (testnet), registered in
Hermes 0.22.2 as `mcp_servers.<name>.url`:

- What the model receives for the tool call:
  `{"error": "MCP call failed: MCPError: MCP error -32042: Payment is required (paid_search)."}`.
  `error.data.challenges` (amount, recipient, currency, expiry) is dropped.
- Hermes calls `session.call_tool(tool_name, arguments=args)`. A valid credential
  passed as an argument named `_meta` arrives as `params.arguments._meta`, while
  `params._meta` stays `{}`. The server answers `-32042` again, and nothing is paid.
- The same tool called through mppx `McpClient.wrap` paid in about 0.9 s and
  returned `result._meta["org.paymentauth/receipt"]`.
- The mcpSdk transport reports a failed verification as `-32042` (problem type
  `verification-failed`) with a replacement challenge, not as `-32043`.

## 7. The paid request is answered with a new challenge

Each 402 has a fresh challenge `id` (every probe of Exa and of the local server
returned a different `id`; Exa's `expires` is the issue time + 5 min). The probe
and the mppx payment are separate requests, so the decision is about the probed
price, and mppx pays whatever challenge it receives. In code, enforce the approved
amount in `onChallenge` (see 3). With the mppx CLI, compare the amount it reports.

## 8. What a real dual 402 looks like (Exa `/search`, 2026-10-20)

- MPP: one `WWW-Authenticate: Payment` with `method="tempo"`, `intent="charge"`,
  `request` = base64url JSON `{amount:"7000", currency:"0x20C0…8b50",
  methodDetails:{chainId:4217, feePayer:true, supportedModes:["pull"]}, recipient}`,
  `expires` = +5 min, `opaque` = `{"_mppx_scope":"POST /search"}`.
- x402: `PAYMENT-REQUIRED` (v2, base64) with 7 `exact` offers of 7000
  ($0.007) on Base, Solana, World Chain and chain 5042.
- `GET /search` returns 404, not 402. That is why `probe.sh` defaults to POST.

Fixtures: `tests/fixtures/exa-search-402-2026-10-20.http` (raw) and `.masked.http`
(recipients cut to their last 4 characters).
