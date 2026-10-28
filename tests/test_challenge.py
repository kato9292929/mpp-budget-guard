#!/usr/bin/env python3
"""Tests for the production `WWW-Authenticate: Payment` parser + budget decider
in scripts/challenge.py. Hermetic (no network).

Run: python3 -m unittest discover -s tests -v   (from mpp-agent-run/)
"""
import base64
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "mpp-budget-guard", "scripts"))

from challenge import (KNOWN_CURRENCIES, analyze, decide, ledger_append,  # noqa: E402
                       ledger_spent_usd, parse_payment_header, parse_payment_headers,
                       parse_x402, read_http_file)

FIX = os.path.join(HERE, "fixtures")
EXA = os.path.join(FIX, "exa-search-402-2026-10-20.http")
EXA_MASKED = os.path.join(FIX, "exa-search-402-2026-10-20.masked.http")
BEFORE = datetime(2026, 10, 20, 5, 43, tzinfo=timezone.utc)   # challenge expires 05:47:07Z
AFTER = datetime(2026, 10, 20, 5, 48, tzinfo=timezone.utc)
USDC = "0x20C000000000000000000000b9537d11c60E8b50"
PATHUSD = "0x20c0000000000000000000000000000000000000"


def b64(o):
    return base64.urlsafe_b64encode(json.dumps(o).encode()).rstrip(b"=").decode()


def hdr(amount="7000", currency=USDC, chain=4217, method="tempo", expires="2026-10-20T05:47:07Z",
        extra=""):
    req = b64({"amount": amount, "currency": currency, "recipient": "0xabc0000000000000000000000000000000001234",
               "methodDetails": {"chainId": chain}})
    return (f'Payment id="x1", realm="t", method="{method}", intent="charge", '
            f'request="{req}", expires="{expires}"{extra}')


class ExaFixture(unittest.TestCase):
    def setUp(self):
        self.cap = read_http_file(EXA)
        self.chs = parse_payment_headers(self.cap["headers"]["www-authenticate"])

    def test_status_and_single_challenge(self):
        self.assertEqual(self.cap["status"], 402)
        self.assertEqual(len(self.chs), 1)

    def test_auth_params(self):
        c = self.chs[0]
        self.assertEqual((c.realm, c.method, c.intent), ("api.exa.ai", "tempo", "charge"))
        self.assertTrue(c.id)
        self.assertEqual(c.expires, "2026-10-20T05:47:07.282Z")
        self.assertEqual(c.opaque, {"_mppx_scope": "POST /search"})
        self.assertEqual(c.description, "Exa /search endpoint")

    def test_request_decoded(self):
        r = self.chs[0].request
        self.assertEqual(r["amount"], "7000")
        self.assertEqual(r["currency"].lower(), USDC.lower())
        self.assertEqual(r["methodDetails"]["chainId"], 4217)

    def test_x402_side(self):
        x = parse_x402(self.cap["headers"]["payment-required"][0], None)
        self.assertEqual(x["x402Version"], 2)
        self.assertEqual(len(x["accepts"]), 7)
        self.assertEqual({a["scheme"] for a in x["accepts"]}, {"exact"})
        self.assertIn("eip155:8453", {a["network"] for a in x["accepts"]})
        self.assertTrue(any(a["network"].startswith("solana:") for a in x["accepts"]))

    def test_x402_body_fallback(self):
        x = parse_x402(None, self.cap["body"])
        self.assertEqual(x["source"], "body")
        self.assertTrue(x["accepts"])

    def test_decide_allow_before_expiry(self):
        d = decide(self.chs[0], 0.0, BEFORE)
        self.assertEqual(d["decision"], "allow")
        self.assertAlmostEqual(d["amount_usd"], 0.007)

    def test_decide_deny_after_expiry(self):
        d = decide(self.chs[0], 0.0, AFTER)
        self.assertEqual(d["decision"], "deny")
        self.assertIn("expired", d["reason"])

    def test_analyze_selects_tempo(self):
        res = analyze(self.cap, "/nonexistent/ledger.jsonl", BEFORE)
        self.assertEqual(res["selected"], 0)
        self.assertEqual(res["mpp"][0]["amount"], 0.007)

    def test_proxy_preamble_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "c.http")
            with open(EXA) as f, open(p, "w") as g:
                g.write("HTTP/1.1 200 Connection Established\n\n" + f.read())
            cap = read_http_file(p)
            self.assertEqual(cap["status"], 402)
            self.assertIn("www-authenticate", cap["headers"])

    def test_masked_fixture(self):
        cap = read_http_file(EXA_MASKED)
        with open(EXA_MASKED) as f:
            raw = f.read()
        for full in ("B98eF29eb2be19Ae646A8FC0248255B90A332dbC", "6d6E695b09861467c7d462f5AAF31cF3540B9192"):
            self.assertNotIn(full, raw)
        c = parse_payment_headers(cap["headers"]["www-authenticate"])[0]
        self.assertEqual(c.request["recipient"], "0x…2dbC")
        x = parse_x402(cap["headers"]["payment-required"][0], None)
        self.assertTrue(all("…" in a["payTo"] for a in x["accepts"]))


class Grammar(unittest.TestCase):
    def test_multiple_challenges_one_header(self):
        v = hdr() + ', ' + hdr(method="stripe", extra=', description="a, b, c"')
        chs = parse_payment_header(v)
        self.assertEqual([c.method for c in chs], ["tempo", "stripe"])
        self.assertEqual(chs[1].description, "a, b, c")

    def test_multiple_headers_and_other_schemes(self):
        chs = parse_payment_headers(['Bearer realm="api"', hdr(), hdr(chain=42431, currency=PATHUSD)])
        self.assertEqual(len(chs), 2)
        self.assertEqual(chs[1].request["methodDetails"]["chainId"], 42431)

    def test_other_scheme_before_payment_in_same_header(self):
        chs = parse_payment_header('Bearer realm="x", error="y", ' + hdr())
        self.assertEqual(len(chs), 1)

    def test_escaped_quote(self):
        c = parse_payment_header(hdr(extra=r', description="say \"hi\""'))[0]
        self.assertEqual(c.description, 'say "hi"')

    def test_digest_param(self):
        c = parse_payment_header(hdr(extra=', digest="sha-256=abc="'))[0]
        self.assertEqual(c.digest, "sha-256=abc=")

    def test_no_payment_raises(self):
        with self.assertRaises(ValueError):
            parse_payment_headers(['Bearer realm="api"'])

    def test_missing_request_raises(self):
        with self.assertRaises(ValueError):
            parse_payment_header('Payment id="a", method="tempo"')

    def test_bad_base64_raises(self):
        with self.assertRaises(ValueError):
            parse_payment_header('Payment id="a", method="tempo", request="!!!"')

    def test_uppercase_method_raises(self):
        with self.assertRaises(ValueError):
            parse_payment_header(hdr(method="Tempo"))


class Decide(unittest.TestCase):
    def one(self, **kw):
        return parse_payment_header(hdr(**kw))[0]

    def test_unknown_currency_denied(self):
        d = decide(self.one(currency="0x" + "11" * 20), 0.0, BEFORE)
        self.assertEqual(d["decision"], "deny")
        self.assertIn("unknown currency", d["reason"])

    def test_known_token_wrong_chain_denied(self):
        d = decide(self.one(chain=1), 0.0, BEFORE)
        self.assertEqual(d["decision"], "deny")

    def test_per_request_cap(self):
        d = decide(self.one(amount="100001"), 0.0, BEFORE)   # $0.100001
        self.assertEqual(d["decision"], "deny")
        self.assertIn("per-request", d["reason"])

    def test_exactly_at_per_request_cap_allowed(self):
        self.assertEqual(decide(self.one(amount="100000"), 0.0, BEFORE)["decision"], "allow")

    def test_total_cap(self):
        d = decide(self.one(), 0.497, BEFORE)                  # 0.497 + 0.007 > 0.50
        self.assertEqual(d["decision"], "deny")
        self.assertIn("total", d["reason"])

    def test_testnet_uncapped(self):
        d = decide(self.one(amount="5000000", chain=42431, currency=PATHUSD), 0.49, BEFORE)
        self.assertEqual((d["decision"], d["network"]), ("allow", "testnet"))

    def test_testnet_still_checks_expiry(self):
        d = decide(self.one(chain=42431, currency=PATHUSD), 0.0, AFTER)
        self.assertEqual(d["decision"], "deny")

    def test_unparseable_expires_denied(self):
        self.assertEqual(decide(self.one(expires="soon"), 0.0, BEFORE)["decision"], "deny")

    def test_known_currency_table(self):
        self.assertEqual(KNOWN_CURRENCIES[(4217, USDC.lower())]["decimals"], 6)


class Ledger(unittest.TestCase):
    def test_sum_mainnet_only_ignores_dry_run_and_testnet(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "l.jsonl")
            ledger_append(p, {"amount_usd": 0.007, "network": "mainnet"})
            ledger_append(p, {"amount_usd": 0.007})                       # legacy row -> mainnet
            ledger_append(p, {"amount_usd": 9.0, "network": "testnet"})
            ledger_append(p, {"amount_usd": 0.25, "dry_run": True})
            self.assertAlmostEqual(ledger_spent_usd(p), 0.014)

    def test_corrupt_line_raises(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "l.jsonl")
            with open(p, "w") as f:
                f.write("{not json\n")
            with self.assertRaises(ValueError):
                ledger_spent_usd(p)

    def test_missing_ledger_is_zero(self):
        self.assertEqual(ledger_spent_usd("/nonexistent/x.jsonl"), 0.0)

    def test_analyze_uses_ledger_total(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "l.jsonl")
            ledger_append(p, {"amount_usd": 0.497, "network": "mainnet"})
            res = analyze(read_http_file(EXA), p, BEFORE)
            self.assertEqual(res["decisions"][0]["decision"], "deny")
            self.assertIsNone(res["selected"])


class Cli(unittest.TestCase):
    def test_legacy_cli_accepts_payment_form(self):
        out = subprocess.run([sys.executable, os.path.join(HERE, "..", "mpp-budget-guard", "scripts", "challenge.py"),
                              hdr(expires="2099-01-01T00:00:00Z"), "0"],
                             capture_output=True, text=True, check=True).stdout
        j = json.loads(out)
        self.assertEqual((j["client"], j["budget"]["decision"]), ("mppx", "allow"))

    def test_analyze_cli_writes_log(self):
        with tempfile.TemporaryDirectory() as d:
            log = os.path.join(d, "decisions.jsonl")
            subprocess.run([sys.executable, os.path.join(HERE, "..", "mpp-budget-guard", "scripts", "challenge.py"),
                            "analyze", EXA, "--ledger", os.path.join(d, "none.jsonl"),
                            "--now", "2026-10-20T05:43:00Z", "--log", log, "--mask"],
                           capture_output=True, text=True, check=True)
            with open(log) as f:
                row = json.loads(f.readline())
            self.assertEqual(row["decisions"][0]["decision"], "allow")
            self.assertEqual(row["mpp"][0]["recipient"], "0x…2dbC")


if __name__ == "__main__":
    unittest.main()
