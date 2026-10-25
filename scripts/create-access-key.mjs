#!/usr/bin/env node
// mpp-budget-guard: create a Tempo access key whose limits are enforced ON-CHAIN
// (keychain precompile): a per-period spending limit for one token, a recipient
// allowlist for TIP-20 transfer/transferWithMemo, and an expiry.
//
// HUMAN-RUN ONLY. Authorizing an access key must be signed by the root account,
// so this script needs the root private key. It reads it ONLY from the file you
// pass with --root-key-file (never from env vars, never from a wallet's config),
// never prints it, and the agent must not run this script (see SKILL.md).
//
//   cd scripts && npm ci        # installs the pinned viem
//   node create-access-key.mjs --root-key-file ~/root.key \
//     --recipient 0xMerchant... --daily-limit-usd 0.50 --expiry-days 1 \
//     [--network testnet|mainnet] [--token 0x...] [--out ~/.mpp-budget-guard/access-key.json]
//
// Default network is testnet (Tempo moderato, chainId 42431). Mainnet must be
// asked for explicitly with --network mainnet.
//
// Observed on testnet (2026-10-20): the tx fee appears to count against the
// limit (a 0.005 payment debited 5048 base units and the next 0.005 payment hit
// SpendingLimitExceeded with a 0.01 limit), so leave headroom. This is an
// inference from those numbers, not a documented rule.
import * as fs from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'
import { parseArgs } from 'node:util'
import { createClient, http, parseUnits } from 'viem'
import { generatePrivateKey } from 'viem/accounts'
import { waitForTransactionReceipt } from 'viem/actions'
import { Account, Actions } from 'viem/tempo'
import { tempo, tempoModerato } from 'viem/tempo/chains'

const NETWORKS = {
  testnet: { chain: tempoModerato, token: '0x20c0000000000000000000000000000000000000', symbol: 'pathUSD' },
  mainnet: { chain: tempo, token: '0x20C000000000000000000000b9537d11c60E8b50', symbol: 'USDC' },
}
const TIP20_DECIMALS = 6

const { values: o } = parseArgs({
  options: {
    'root-key-file': { type: 'string' },
    recipient: { type: 'string', multiple: true },
    'daily-limit-usd': { type: 'string' },
    'period-seconds': { type: 'string', default: '86400' },
    'expiry-days': { type: 'string', default: '1' },
    network: { type: 'string', default: 'testnet' },
    token: { type: 'string' },
    out: { type: 'string', default: path.join(os.homedir(), '.mpp-budget-guard', 'access-key.json') },
    help: { type: 'boolean', default: false },
  },
})

function fail(msg) {
  console.error(`create-access-key: ${msg}`)
  process.exit(2)
}
if (o.help) {
  console.log(fs.readFileSync(new URL(import.meta.url), 'utf8').split('\n').filter((l) => l.startsWith('//')).join('\n'))
  process.exit(0)
}
const net = NETWORKS[o.network] ?? fail(`--network must be testnet or mainnet, got ${o.network}`)
if (!o['root-key-file']) fail('--root-key-file is required (human-run only; see SKILL.md)')
if (!o.recipient?.length) fail('at least one --recipient 0x... is required')
if (!o['daily-limit-usd']) fail('--daily-limit-usd is required (e.g. 0.50)')
for (const r of o.recipient) if (!/^0x[0-9a-fA-F]{40}$/.test(r)) fail(`bad --recipient ${r}`)
if (fs.existsSync(o.out)) fail(`${o.out} already exists; choose another --out`)

const raw = fs.readFileSync(path.resolve(o['root-key-file'].replace(/^~/, os.homedir())), 'utf8').trim()
let rootKey = raw
try { rootKey = JSON.parse(raw).privateKey ?? raw } catch { /* plain hex file */ }
if (!/^0x[0-9a-fA-F]{64}$/.test(rootKey)) fail('root key file must hold a 0x-prefixed 32-byte hex key (or JSON {"privateKey": ...})')

const token = o.token ?? net.token
const limit = parseUnits(o['daily-limit-usd'], TIP20_DECIMALS)
const period = Number(o['period-seconds'])
const expiry = Math.floor(Date.now() / 1000) + Math.round(Number(o['expiry-days']) * 86400)

const client = createClient({ chain: net.chain, transport: http() })
const root = Account.fromSecp256k1(rootKey)
const accessKeyPrivate = generatePrivateKey()
const accessKey = Account.fromSecp256k1(accessKeyPrivate, { access: root })

const hash = await Actions.accessKey.authorize(client, {
  account: root,
  accessKey,
  expiry,
  limits: [{ token, limit, period }],
  scopes: [
    { address: token, selector: 'transfer(address,uint256)', recipients: o.recipient },
    { address: token, selector: 'transferWithMemo(address,uint256,bytes32)', recipients: o.recipient },
  ],
})
const receipt = await waitForTransactionReceipt(client, { hash })
if (receipt.status !== 'success') fail(`authorize tx ${hash} reverted`)

fs.mkdirSync(path.dirname(o.out), { recursive: true })
fs.writeFileSync(o.out, JSON.stringify({
  network: o.network, chainId: net.chain.id, root: root.address,
  accessKeyAddress: accessKey.accessKeyAddress, accessKeyPrivateKey: accessKeyPrivate,
}), { mode: 0o600 })

console.log(JSON.stringify({
  network: o.network, chainId: net.chain.id, authorizeTx: hash, status: receipt.status,
  root: root.address, accessKeyAddress: accessKey.accessKeyAddress,
  limit: { token, symbol: o.token ? null : net.symbol, baseUnits: limit.toString(), periodSeconds: period },
  recipients: o.recipient, expiry: new Date(expiry * 1000).toISOString(),
  savedTo: o.out, note: 'access key private key saved with mode 0600; it was not printed',
}, null, 2))
