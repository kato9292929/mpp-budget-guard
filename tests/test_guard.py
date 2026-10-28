#!/usr/bin/env python3
"""Tests for what mpp-budget-guard adds on top of the copied mpp-agent-run parser
(tests/test_challenge.py): overall verdict, configurable caps, --cap-testnet,
exit codes, `record`, and scripts/probe.sh against a local 402 server.
Hermetic (localhost only). Run: python3 -m unittest discover -s tests -v
"""
import base64
import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "mpp-budget-guard", "scripts")
sys.path.insert(0, SCRIPTS)

from challenge import analyze, read_http_file  # noqa: E402

FIX = os.path.join(HERE, "fixtures")
EXA = os.path.join(FIX, "exa-search-402-2026-10-20.http")
CHALLENGE = os.path.join(SCRIPTS, "challenge.py")
PROBE = os.path.join(SCRIPTS, "probe.sh")
NOW = "2026-10-20T05:43:00Z"           # before the Exa fixture's expires
PATHUSD = "0x20c0000000000000000000000000000000000000"


def b64(o):
    return base64.urlsafe_b64encode(json.dumps(o).encode()).rstrip(b"=").decode()


def capture(tmp, www=None, payment_required=None, status=402, body="{}"):
    lines = [f"HTTP/1.1 {status} X"]
    if www:
        lines.append(f"WWW-Authenticate: {www}")
    if payment_required:
        lines.append(f"PAYMENT-REQUIRED: {payment_required}")
    p = os.path.join(tmp, "c.http")
    with open(p, "w") as f:
        f.write("\n".join(lines) + "\n\n" + body)
    return p


def payment(amount, chain=42431, currency=PATHUSD, method="tempo"):
    req = b64({"amount": amount, "currency": currency, "recipient": "0x" + "ab" * 20,
               "methodDetails": {"chainId": chain}})
    return f'Payment id="t1", realm="lab", method="{method}", intent="charge", request="{req}", expires="2099-01-01T00:00:00Z"'


def run(*args, state_dir=None):
    prefix = ["env", f"MPP_BUDGET_GUARD_HOME={state_dir}"] if state_dir else []
    return subprocess.run([*prefix, sys.executable, CHALLENGE, *args], capture_output=True, text=True)


class Verdict(unittest.TestCase):
    def test_exa_allow_before_expiry_deny_after(self):
        cap = read_http_file(EXA)
        before = analyze(cap, "/nonexistent", datetime(2026, 10, 20, 5, 43, tzinfo=timezone.utc))
        self.assertEqual(before["verdict"]["decision"], "allow")
        self.assertEqual(before["verdict"]["network"], "mainnet")
        after = analyze(cap, "/nonexistent", datetime(2026, 10, 20, 5, 48, tzinfo=timezone.utc))
        self.assertEqual(after["verdict"]["decision"], "deny")
        self.assertIn("expired", after["verdict"]["reason"])

    def test_x402_only_is_denied(self):
        with tempfile.TemporaryDirectory() as d:
            pr = base64.b64encode(json.dumps({"x402Version": 2, "accepts": [
                {"scheme": "exact", "network": "eip155:8453", "amount": "1000"}]}).encode()).decode()
            res = analyze(read_http_file(capture(d, payment_required=pr)), os.path.join(d, "l"))
            self.assertEqual(res["verdict"]["decision"], "deny")
            self.assertIn("no MPP", res["verdict"]["reason"])
            self.assertEqual(res["x402"]["accepts"][0]["network"], "eip155:8453")

    def test_non_tempo_only_is_denied(self):
        with tempfile.TemporaryDirectory() as d:
            res = analyze(read_http_file(capture(d, www=payment("5000", method="stripe"))), os.path.join(d, "l"))
            self.assertEqual(res["verdict"]["decision"], "deny")
            self.assertIn("no tempo", res["verdict"]["reason"])

    def test_testnet_uncapped_by_default(self):
        with tempfile.TemporaryDirectory() as d:
            res = analyze(read_http_file(capture(d, www=payment("250000"))), os.path.join(d, "l"))
            self.assertEqual(res["verdict"]["decision"], "allow")

    def test_cap_testnet_applies_caps(self):
        with tempfile.TemporaryDirectory() as d:
            res = analyze(read_http_file(capture(d, www=payment("250000"))), os.path.join(d, "l"), cap_testnet=True)
            self.assertEqual(res["verdict"]["decision"], "deny")
            self.assertIn("per-request", res["verdict"]["reason"])
            self.assertEqual(res["decisions"][0]["network"], "testnet")

    def test_cap_testnet_uses_testnet_ledger_total(self):
        with tempfile.TemporaryDirectory() as d:
            led = os.path.join(d, "l")
            with open(led, "w") as f:
                f.write(json.dumps({"amount_usd": 0.498, "network": "testnet"}) + "\n")
                f.write(json.dumps({"amount_usd": 9.0, "network": "mainnet"}) + "\n")
            res = analyze(read_http_file(capture(d, www=payment("5000"))), led, cap_testnet=True)
            self.assertEqual(res["verdict"]["decision"], "deny")
            self.assertIn("total", res["verdict"]["reason"])

    def test_custom_caps(self):
        with tempfile.TemporaryDirectory() as d:
            c = read_http_file(capture(d, www=payment("5000")))
            self.assertEqual(analyze(c, os.path.join(d, "l"), cap_testnet=True, per_cap=0.001)["verdict"]["decision"], "deny")
            self.assertEqual(analyze(c, os.path.join(d, "l"), cap_testnet=True, per_cap=0.01)["verdict"]["decision"], "allow")


class CliExitCodes(unittest.TestCase):
    def test_allow_exit_0_and_log_written(self):
        with tempfile.TemporaryDirectory() as d:
            log = os.path.join(d, "log.jsonl")
            r = run("analyze", EXA, "--ledger", os.path.join(d, "l"), "--log", log, "--now", NOW)
            self.assertEqual(r.returncode, 0, r.stderr)
            with open(log) as f:
                self.assertEqual(json.loads(f.readline())["verdict"]["decision"], "allow")

    def test_deny_exit_3(self):
        with tempfile.TemporaryDirectory() as d:
            r = run("analyze", EXA, "--ledger", os.path.join(d, "l"), "--log", "", "--now", NOW,
                    "--per-request-cap", "0.001")
            self.assertEqual(r.returncode, 3)
            self.assertIn("per-request", json.loads(r.stdout)["verdict"]["reason"])

    def test_undecodable_exit_2(self):
        with tempfile.TemporaryDirectory() as d:
            p = capture(d, www='Payment id="x", method="tempo", request="!!!"')
            r = run("analyze", p, "--ledger", os.path.join(d, "l"), "--log", "")
            self.assertEqual(r.returncode, 2)

    def test_default_state_dir_env(self):
        with tempfile.TemporaryDirectory() as d:
            r = run("analyze", EXA, "--now", NOW, state_dir=d)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(os.path.exists(os.path.join(d, "decision-log.jsonl")))

    def test_record_then_total_cap(self):
        with tempfile.TemporaryDirectory() as d:
            led = os.path.join(d, "l")
            r = run("record", "--ledger", led, "--amount-usd", "0.497", "--tx", "0xabc")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertAlmostEqual(json.loads(r.stdout)["mainnet_total_usd"], 0.497)
            r = run("analyze", EXA, "--ledger", led, "--log", "", "--now", NOW)
            self.assertEqual(r.returncode, 3)
            self.assertIn("total", json.loads(r.stdout)["verdict"]["reason"])


class SkillFrontmatter(unittest.TestCase):
    """Hermes drops a skill whose frontmatter is not valid YAML; a plain scalar must not
    contain ': ', and the index truncates descriptions past 60 characters."""

    def test_frontmatter(self):
        with open(os.path.join(HERE, "..", "mpp-budget-guard", "SKILL.md"), encoding="utf-8") as f:
            text = f.read()
        self.assertTrue(text.startswith("---\n"))
        fm = dict(line.split(": ", 1) for line in text.split("---\n")[1].splitlines()
                  if line and not line.startswith(" ") and ": " in line)
        self.assertEqual(fm["name"], "mpp-budget-guard")
        self.assertNotIn(": ", fm["description"])
        self.assertLessEqual(len(fm["description"]), 60)
        self.assertEqual((fm["version"], fm["license"], fm["platforms"]), ("0.1.0", "MIT", "[linux, macos]"))
        self.assertIn("related_skills: [mpp-agent]", text.split("---\n")[1])


class _Handler(http.server.BaseHTTPRequestHandler):
    seen = []
    auth = []

    def _reply(self):
        n = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(n).decode() if n else ""
        _Handler.seen.append((self.command, self.headers.get("content-type"), body))
        _Handler.auth.append(self.headers.get("authorization"))
        if self.command == "GET":
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(402)
        self.send_header("WWW-Authenticate", payment("5000"))
        self.end_headers()
        self.wfile.write(b"{}")

    do_GET = do_POST = _reply

    def log_message(self, *a):
        pass


class Probe(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.srv.server_port}/paid"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def probe(self, *args):
        return subprocess.run(["bash", PROBE, self.url, *args], capture_output=True, text=True)

    def test_default_post_json_402(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "p.http")
            r = self.probe("--data", '{"query":"t"}', "--out", out)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.strip().splitlines()[-1], out)
            self.assertEqual(_Handler.seen[-1], ("POST", "application/json", '{"query":"t"}'))
            res = analyze(read_http_file(out), os.path.join(d, "l"))
            self.assertEqual(res["mpp"][0]["amount_base_units"], "5000")

    def test_get_non_402_exit_1(self):
        with tempfile.TemporaryDirectory() as d:
            r = self.probe("--method", "GET", "--out", os.path.join(d, "p.http"))
            self.assertEqual(r.returncode, 1)
            self.assertIn("status: 404", r.stdout)

    def test_never_sends_authorization(self):
        with tempfile.TemporaryDirectory() as d:
            self.probe("--out", os.path.join(d, "p.http"))
        self.assertTrue(_Handler.auth)
        self.assertEqual(set(_Handler.auth), {None})


if __name__ == "__main__":
    unittest.main()
