// TESTNET-only helper: create (or load) a named key under $E2E_VAR, faucet-fund it,
// print {name, address, pathUSD}. Usage: node wallet.mjs <name> [--balance-only]
import * as fs from 'node:fs'
import * as path from 'node:path'
import { createClient, http } from 'viem'
import { generatePrivateKey, privateKeyToAccount } from 'viem/accounts'
import { waitForTransactionReceipt } from 'viem/actions'
import { Actions } from 'viem/tempo'
import { tempoModerato } from 'viem/tempo/chains'

const [name, flag] = process.argv.slice(2)
const dir = process.env.E2E_VAR ?? path.join(import.meta.dirname, 'var')
fs.mkdirSync(dir, { recursive: true })
const file = path.join(dir, `${name}.testnet-key.json`)
if (!fs.existsSync(file)) fs.writeFileSync(file, JSON.stringify({ privateKey: generatePrivateKey(), chainId: 42431 }), { mode: 0o600 })
const acct = privateKeyToAccount(JSON.parse(fs.readFileSync(file, 'utf8')).privateKey)
const client = createClient({ chain: tempoModerato, transport: http() })
const bal = async () => (await Actions.token.getBalance(client, { account: acct.address, token: '0x20c0000000000000000000000000000000000000' })).amount
if (flag !== '--balance-only' && (await bal()) === 0n) {
  const hashes = await Actions.faucet.fund(client, { account: acct })
  await Promise.all(hashes.map((hash) => waitForTransactionReceipt(client, { hash })))
}
console.log(JSON.stringify({ name, address: acct.address, keyFile: file, pathUSD: (await bal()).toString() }))
