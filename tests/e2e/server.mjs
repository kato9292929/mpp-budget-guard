// Local MPP 402 server on Tempo TESTNET (moderato, 42431), same shape as
// mpp-agent-run/lab/server.mjs:  /cheap 0.005 pathUSD, /expensive 0.25 pathUSD.
// Usage: node server.mjs <merchant-address> [port]
import * as crypto from 'node:crypto'
import * as http from 'node:http'
import { Mppx, tempo } from 'mppx/server'

const [recipient, port = '8410'] = process.argv.slice(2)
if (!/^0x[0-9a-fA-F]{40}$/.test(recipient ?? '')) throw new Error('usage: node server.mjs <merchant-address> [port]')
const pay = Mppx.create({
  methods: [tempo({ currency: '0x20c0000000000000000000000000000000000000', recipient, testnet: true })],
  secretKey: crypto.randomBytes(32).toString('base64'),
})
const routes = { '/cheap': '0.005', '/expensive': '0.25' }

http.createServer(async (req, res) => {
  const amount = routes[new URL(req.url, 'http://x').pathname]
  if (!amount) return res.writeHead(404).end()
  const result = await Mppx.toNodeListener(pay.charge({ amount, description: `e2e ${req.url}` }))(req, res)
  if (result.status === 402) return
  res.setHeader('content-type', 'application/json')
  res.end(JSON.stringify({ ok: true, route: req.url }))
}).listen(Number(port), '127.0.0.1', () => console.log(`e2e 402 server http://127.0.0.1:${port}`))
