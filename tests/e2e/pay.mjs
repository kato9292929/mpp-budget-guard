// Stand-in for the hand-off to mpp-agent: pays ONE request with the mppx client
// library (what the mppx CLI wraps), TESTNET only, and refuses any challenge
// whose amount exceeds what challenge.py approved (--max-base-units), because the
// paid request gets a NEW challenge from the server, not the probed one.
// Usage: node pay.mjs <url> <payer-key-file> --max-base-units 5000
import * as fs from 'node:fs'
import { Mppx, tempo } from 'mppx/client'
import { privateKeyToAccount } from 'viem/accounts'

const [url, keyFile, flag, max] = process.argv.slice(2)
if (flag !== '--max-base-units' || !max) throw new Error('usage: node pay.mjs <url> <key-file> --max-base-units N')
const { privateKey, chainId } = JSON.parse(fs.readFileSync(keyFile, 'utf8'))
if (chainId !== 42431) throw new Error('e2e pays on testnet only')
const mppx = Mppx.create({
  polyfill: false,
  methods: [tempo({ account: privateKeyToAccount(privateKey) })],
  onChallenge: async (challenge) => {
    if (challenge.request.methodDetails?.chainId !== 42431) throw new Error('refusing non-testnet challenge')
    if (BigInt(challenge.request.amount) > BigInt(max)) throw new Error(`amount ${challenge.request.amount} > approved ${max}`)
    return undefined
  },
})
const res = await mppx.fetch(url)
const receipt = res.headers.get('payment-receipt')
console.log(JSON.stringify({
  status: res.status, body: await res.text(),
  receipt: receipt ? JSON.parse(Buffer.from(receipt, 'base64url').toString()) : null,
}))
