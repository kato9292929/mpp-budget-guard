#!/usr/bin/env python3
"""mpp-budget-guard: read an HTTP 402 (MPP `WWW-Authenticate: Payment` and the
x402 `PAYMENT-REQUIRED` offer in the same response) and decide, BEFORE anything
is paid, whether a payment may go ahead: per-request cap, running total cap
(append-only ledger), known currency, unexpired challenge. Pure standard-library
Python, no network, never touches a wallet or key. Paying is mpp-agent's job.

Copied from mpp-agent-run/scripts/challenge.py (production-format version) and
extended with configurable caps, an overall verdict / exit code and `record`.

    challenge.py analyze <capture.http> [--per-request-cap 0.10] [--total-cap 0.50]
    challenge.py record --amount-usd 0.007 --tx 0x... [--network mainnet]

Exit codes for `analyze`: 0 allow, 3 deny, 2 could not decode.
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

PER_REQUEST_CAP_USD = 0.10
TOTAL_CAP_USD = 0.50

# Currencies we can compare to a USD cap without inventing an FX rate. USDC /
# USDC.e are treated 1:1 with USD (stablecoin). Any other currency is refused
# rather than guessed.
_USD_EQUIVALENT = {"usd", "usdc", "usdc.e"}


@dataclass
class Challenge:
    scheme: str                 # e.g. "tempo", "stripe"
    amount: float | None        # numeric amount, or None if absent
    currency: str | None
    method: str | None          # value of a method="..." attribute, if present
    params: dict[str, str]
    raw: str


def _split_challenges(header_value: str) -> list[str]:
    """Split a www-authenticate value into per-scheme challenge strings.

    A challenge starts with a scheme token (a bare word) followed by
    space-separated `k=v` params. Multiple challenges are comma-separated, but
    commas also separate params, so we split on a comma that immediately
    precedes a new `scheme ` token.
    """
    value = header_value.strip()
    # Insert a sentinel before each ", <scheme> " boundary, then split on it.
    marked = re.sub(r",\s*(?=[A-Za-z][A-Za-z0-9_-]*\s+[A-Za-z])", "\x00", value)
    return [c.strip() for c in marked.split("\x00") if c.strip()]


def parse_challenge(header_value: str) -> list[Challenge]:
    """Parse a `www-authenticate` header value into Challenge objects.

    Raises ValueError on something that does not look like a scheme+params
    challenge at all (do not swallow into an empty list).
    """
    out: list[Challenge] = []
    for chunk in _split_challenges(header_value):
        m = re.match(r"^([A-Za-z][A-Za-z0-9_-]*)\s+(.*)$", chunk, re.DOTALL)
        if not m:
            # A bare scheme with no params is still a scheme.
            m2 = re.match(r"^([A-Za-z][A-Za-z0-9_-]*)\s*$", chunk)
            if not m2:
                raise ValueError(f"unrecognized challenge segment: {chunk!r}")
            out.append(Challenge(m2.group(1).lower(), None, None, None, {}, chunk))
            continue
        scheme = m.group(1).lower()
        # Params are `k="quoted"` or `k=bareword`. The regex yields (key, quoted,
        # bare); exactly one of quoted/bare is populated per match.
        params: dict[str, str] = {}
        for km, qv, bv in re.findall(r'([A-Za-z0-9_.-]+)=(?:"([^"]*)"|([^\s,]+))', m.group(2)):
            params[km.lower()] = qv if bv == "" else bv
        amount = None
        if "amount" in params:
            try:
                amount = float(params["amount"])
            except ValueError as exc:
                raise ValueError(f"non-numeric amount in challenge {chunk!r}: {exc}")
        out.append(Challenge(
            scheme=scheme,
            amount=amount,
            currency=(params.get("currency") or None),
            method=(params.get("method") or None),
            params=params,
            raw=chunk,
        ))
    if not out:
        raise ValueError(f"no challenges parsed from header value: {header_value!r}")
    return out


def choose_client(challenges: list[Challenge]) -> dict[str, Any]:
    """Pick the mpp-agent client per SKILL.md:30/36/112-113.

    If any challenge advertises `method="stripe"` -> Stripe Link (`link-cli`).
    Otherwise -> `mppx`, which pays the Tempo challenge.
    Returns {client, reason, target_challenge}.
    """
    stripe = next((c for c in challenges if (c.method or "").lower() == "stripe"), None)
    if stripe is not None:
        return {
            "client": "link-cli",
            "reason": 'challenge advertises method="stripe" (SKILL.md:30,112) -> Stripe Link',
            "target_challenge": stripe,
        }
    tempo = next((c for c in challenges if c.scheme == "tempo"), None)
    target = tempo if tempo is not None else challenges[0]
    return {
        "client": "mppx",
        "reason": "no stripe method; mppx pays the Tempo challenge (SKILL.md:36,113)",
        "target_challenge": target,
    }


def check_budget(amount: float | None, currency: str | None, spent_usd: float,
                 per_cap: float = PER_REQUEST_CAP_USD,
                 total_cap: float = TOTAL_CAP_USD) -> dict[str, Any]:
    """Decide whether a payment of `amount` may proceed.

    Zero-amount challenges are allowed (SKILL.md:114). Unknown currency FAILS
    LOUDLY — we do not invent an FX rate to force it under a USD cap.
    """
    if amount is None:
        raise ValueError("challenge has no amount; cannot budget-check (do not assume)")
    if amount == 0:
        return {"decision": "allow", "reason": "zero-amount proof credential (SKILL.md:114)",
                "amount_usd": 0.0}
    cur = (currency or "").lower()
    if cur not in _USD_EQUIVALENT:
        raise ValueError(
            f"currency {currency!r} is not a known USD-equivalent {sorted(_USD_EQUIVALENT)}; "
            f"refusing rather than guessing an FX rate"
        )
    amount_usd = float(amount)  # 1:1 stablecoin/USD
    if amount_usd > per_cap:
        return {"decision": "deny", "reason": f"per-request ${amount_usd:.4f} > cap ${per_cap:.2f}",
                "amount_usd": amount_usd}
    if spent_usd + amount_usd > total_cap:
        return {"decision": "deny",
                "reason": f"would exceed task total: spent ${spent_usd:.4f} + ${amount_usd:.4f} > cap ${total_cap:.2f}",
                "amount_usd": amount_usd}
    return {"decision": "allow", "reason": "within per-request and total caps", "amount_usd": amount_usd}


# ---------------------------------------------------------------------------
# Production wire format (Exa, 2026-08/10): `WWW-Authenticate: Payment ...`
#
#   Payment id="…", realm="api.exa.ai", method="tempo", intent="charge",
#           request="<base64url JSON>", description="…",
#           expires="2026-10-20T05:47:07.282Z", opaque="<base64url JSON>"
#
# The amount lives INSIDE `request` (base units, e.g. "7000") and `currency` is
# a token address, so the simplified `tempo amount=X` parser above cannot read
# it. The grammar below follows mppx 0.8.13 src/Challenge.ts
# (extractPaymentAuthParams / parseAuthParams / deserializeList): quoted
# strings may contain commas and `\"` escapes, one header may carry several
# `Payment` challenges, and several WWW-Authenticate headers may be present.
# ---------------------------------------------------------------------------

# (chainId, lowercase token address) -> token facts. Source: mppx 0.8.13
# src/tempo/internal/defaults.ts (tokens, chainId, decimals = 6 for TIP-20).
# Anything not listed is an UNKNOWN currency and is refused.
KNOWN_CURRENCIES: dict[tuple[int, str], dict[str, Any]] = {
    (4217, "0x20c000000000000000000000b9537d11c60e8b50"):
        {"symbol": "USDC", "decimals": 6, "network": "mainnet", "usd_per_unit": 1.0},
    (42431, "0x20c0000000000000000000000000000000000000"):
        {"symbol": "pathUSD", "decimals": 6, "network": "testnet", "usd_per_unit": 1.0},
}

PAYMENT_PARAMS = ("id", "realm", "method", "intent", "request", "expires",
                  "digest", "opaque", "description")


@dataclass
class PaymentChallenge:
    id: str | None
    realm: str | None
    method: str | None
    intent: str | None
    request: dict[str, Any]      # decoded base64url JSON
    expires: str | None
    digest: str | None
    opaque: Any                  # decoded base64url JSON when it decodes, else raw
    description: str | None
    params: dict[str, str]       # every auth-param as received
    raw: str


def b64url_json(value: str) -> Any:
    """Decode base64url (padding optional) JSON. Raises ValueError on failure."""
    try:
        pad = "=" * (-len(value) % 4)
        return json.loads(base64.urlsafe_b64decode(value + pad).decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - re-raised as ValueError
        raise ValueError(f"not base64url JSON: {value[:40]!r}…: {exc}") from exc


def _parse_auth_params(s: str, i: int) -> tuple[dict[str, str], int]:
    """Parse `k=v, k="v"` auth-params starting at s[i]. Stops at a token that is
    not followed by `=` (the next auth scheme). Returns (params, stop_index)."""
    out: dict[str, str] = {}
    n = len(s)
    while i < n:
        while i < n and s[i] in " \t,":
            i += 1
        if i >= n:
            break
        k0 = i
        while i < n and (s[i].isalnum() or s[i] in "_-."):
            i += 1
        key = s[k0:i]
        if not key:
            raise ValueError(f"malformed auth-param at {i}: {s[i:i+20]!r}")
        j = i
        while j < n and s[j] in " \t":
            j += 1
        if j >= n or s[j] != "=":
            return out, k0          # next scheme starts here
        i = j + 1
        while i < n and s[i] in " \t":
            i += 1
        if i < n and s[i] == '"':
            i += 1
            buf = []
            while i < n and s[i] != '"':
                if s[i] == "\\" and i + 1 < n:
                    i += 1
                buf.append(s[i])
                i += 1
            if i >= n:
                raise ValueError("unterminated quoted-string in auth-param")
            i += 1
            out[key.lower()] = "".join(buf)
        else:
            v0 = i
            while i < n and s[i] not in " \t,":
                i += 1
            out[key.lower()] = s[v0:i]
    return out, i


def parse_payment_header(value: str) -> list[PaymentChallenge]:
    """All `Payment` challenges in ONE WWW-Authenticate value (other schemes,
    e.g. `Bearer`, are skipped). Raises ValueError if none is present or a
    Payment challenge lacks a decodable `request`."""
    out: list[PaymentChallenge] = []
    i, n = 0, len(value)
    while i < n:
        while i < n and value[i] in " \t,":
            i += 1
        s0 = i
        while i < n and (value[i].isalnum() or value[i] in "_-"):
            i += 1
        scheme = value[s0:i]
        if not scheme:
            raise ValueError(f"malformed WWW-Authenticate at {i}: {value[i:i+20]!r}")
        params, end = _parse_auth_params(value, i)
        raw = value[s0:end].rstrip(" ,")
        i = end if end > i else n
        if scheme.lower() != "payment":
            continue
        if "request" not in params:
            raise ValueError(f"Payment challenge without request=: {raw[:80]!r}")
        if params.get("method") and not re.fullmatch(r"[a-z][a-z0-9:_-]*", params["method"]):
            raise ValueError(f"invalid method {params['method']!r} (must be lowercase)")
        opaque: Any = params.get("opaque")
        if opaque:
            try:
                opaque = b64url_json(opaque)
            except ValueError:
                pass                 # opaque is server-defined; keep raw
        out.append(PaymentChallenge(
            id=params.get("id"), realm=params.get("realm"),
            method=params.get("method"), intent=params.get("intent"),
            request=b64url_json(params["request"]),
            expires=params.get("expires"), digest=params.get("digest"),
            opaque=opaque, description=params.get("description"),
            params=params, raw=raw,
        ))
    if not out:
        raise ValueError(f"no Payment challenge in WWW-Authenticate: {value[:80]!r}")
    return out


def parse_payment_headers(values: list[str]) -> list[PaymentChallenge]:
    """Challenges across SEVERAL WWW-Authenticate headers. Headers that carry no
    Payment scheme are ignored; it is an error only if none carries one."""
    out: list[PaymentChallenge] = []
    for v in values:
        try:
            out.extend(parse_payment_header(v))
        except ValueError as exc:
            if not str(exc).startswith("no Payment challenge"):
                raise
    if not out:
        raise ValueError("no Payment challenge in any WWW-Authenticate header")
    return out


def parse_x402(payment_required: str | None, body: str | None) -> dict[str, Any] | None:
    """x402 offer from the same 402: the base64 `PAYMENT-REQUIRED` header
    (x402 v2) or, failing that, a JSON body with `accepts`. None if absent."""
    doc = None
    source = None
    if payment_required:
        doc, source = b64url_json(payment_required.strip()), "PAYMENT-REQUIRED header"
    elif body:
        try:
            cand = json.loads(body)
            if isinstance(cand, dict) and "accepts" in cand:
                doc, source = cand, "body"
        except ValueError:
            pass
    if doc is None:
        return None
    accepts = [{
        "scheme": a.get("scheme"),
        "network": a.get("network"),
        "amount": a.get("amount") or a.get("maxAmountRequired"),
        "asset": a.get("asset"),
        "payTo": a.get("payTo"),
        "name": (a.get("extra") or {}).get("name"),
        "totalUsd": (a.get("extra") or {}).get("totalUsd"),
    } for a in doc.get("accepts", [])]
    return {"source": source, "x402Version": doc.get("x402Version"), "accepts": accepts}


def read_http_file(path: str) -> dict[str, Any]:
    """Read a `curl -i`-style capture: status line, headers, blank line, body.
    Repeated headers are kept as lists (lower-cased names)."""
    with open(path, encoding="utf-8") as f:
        text = f.read().replace("\r\n", "\n")
    # Use the LAST status line: a proxy's "HTTP/1.1 200 Connection Established"
    # preamble (and any 100-continue) precede the real response.
    m = None
    for m in re.finditer(r"^HTTP/\S+ (\d{3})", text, re.M):
        if not text[m.end():].lstrip(" \t").startswith(("Connection Established", "Continue")):
            break
    if m:
        text = text[m.start():]
    head, _, body = text.partition("\n\n")
    lines = head.split("\n")
    headers: dict[str, list[str]] = {}
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers.setdefault(k.strip().lower(), []).append(v.strip())
    return {"status": int(m.group(1)) if m else None, "headers": headers, "body": body}


def mask(addr: str | None) -> str | None:
    """Mask an address to its last 4 characters (for publication)."""
    if not addr:
        return addr
    pre = "0x" if addr.startswith("0x") else ""
    return f"{pre}…{addr[-4:]}"


def describe(ch: PaymentChallenge, masked: bool = False) -> dict[str, Any]:
    """Flat, human-readable view of a challenge (C1)."""
    req = ch.request
    details = req.get("methodDetails") or {}
    chain = details.get("chainId")
    cur = req.get("currency")
    tok = KNOWN_CURRENCIES.get((chain, (cur or "").lower())) if isinstance(chain, int) else None
    amount_base = req.get("amount")
    human = None
    if tok and amount_base is not None:
        human = int(amount_base) / 10 ** tok["decimals"]
    return {
        "id": ch.id, "realm": ch.realm, "method": ch.method, "intent": ch.intent,
        "amount_base_units": amount_base,
        "amount": human, "symbol": tok["symbol"] if tok else None,
        "currency": cur, "chainId": chain,
        "network": tok["network"] if tok else None,
        "recipient": mask(req.get("recipient")) if masked else req.get("recipient"),
        "expires": ch.expires, "digest": ch.digest, "opaque": ch.opaque,
        "methodDetails": details,
    }


# --- ledger ---------------------------------------------------------------

def ledger_spent_usd(path: str, network: str = "mainnet") -> float:
    """Sum of `amount_usd` for `network` entries in an append-only JSONL ledger.
    A missing ledger is $0; a corrupt line FAILS LOUDLY."""
    if not os.path.exists(path):
        return 0.0
    total = 0.0
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError as exc:
                raise ValueError(f"{path}:{n}: corrupt ledger line: {exc}") from exc
            if row.get("network", "mainnet") == network and not row.get("dry_run"):
                total += float(row["amount_usd"])
    return total


def ledger_append(path: str, row: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def decide(ch: PaymentChallenge, spent_usd: float, now: datetime | None = None,
           per_cap: float = PER_REQUEST_CAP_USD,
           total_cap: float = TOTAL_CAP_USD, cap_testnet: bool = False) -> dict[str, Any]:
    """Budget decision for one production challenge. Order of checks:
    expired -> unknown currency -> (testnet: no cap) -> per-request -> total.
    Mainnet caps apply; testnet is uncapped by the work order."""
    now = now or datetime.now(timezone.utc)
    d = describe(ch)
    base = {"challenge_id": ch.id, "method": ch.method, "intent": ch.intent,
            "currency": d["currency"], "chainId": d["chainId"],
            "amount_base_units": d["amount_base_units"], "expires": ch.expires,
            "spent_before_usd": round(spent_usd, 6), "now": now.isoformat()}
    if ch.expires:
        try:
            if _parse_ts(ch.expires) <= now:
                return {**base, "decision": "deny", "reason": f"challenge expired at {ch.expires}",
                        "amount_usd": None, "network": d["network"]}
        except ValueError:
            return {**base, "decision": "deny", "reason": f"unparseable expires {ch.expires!r}",
                    "amount_usd": None, "network": d["network"]}
    if d["symbol"] is None:
        return {**base, "decision": "deny",
                "reason": f"unknown currency {d['currency']!r} on chainId {d['chainId']!r}",
                "amount_usd": None, "network": None}
    if d["amount_base_units"] is None:
        return {**base, "decision": "deny", "reason": "request has no amount",
                "amount_usd": None, "network": d["network"]}
    amount_usd = d["amount"] * KNOWN_CURRENCIES[(d["chainId"], d["currency"].lower())]["usd_per_unit"]
    if d["network"] == "testnet" and not cap_testnet:
        return {**base, "decision": "allow", "reason": "testnet: uncapped (pass --cap-testnet to apply caps)",
                "amount_usd": amount_usd, "network": "testnet"}
    if amount_usd > per_cap:
        return {**base, "decision": "deny",
                "reason": f"per-request ${amount_usd:.4f} > cap ${per_cap:.2f}",
                "amount_usd": amount_usd, "network": d["network"]}
    if spent_usd + amount_usd > total_cap + 1e-12:
        return {**base, "decision": "deny",
                "reason": f"total: spent ${spent_usd:.4f} + ${amount_usd:.4f} > cap ${total_cap:.2f}",
                "amount_usd": amount_usd, "network": d["network"]}
    return {**base, "decision": "allow", "reason": "within per-request and total caps",
            "amount_usd": amount_usd, "network": d["network"]}


def analyze(capture: dict[str, Any], ledger: str, now: datetime | None = None,
            masked: bool = False, per_cap: float = PER_REQUEST_CAP_USD,
            total_cap: float = TOTAL_CAP_USD, cap_testnet: bool = False) -> dict[str, Any]:
    """Side-by-side view of both rails in one 402 + a decision per MPP challenge.
    Picks the first `allow`ed tempo challenge as the one to pay (mppx pays tempo)."""
    hdr = capture["headers"]
    www = hdr.get("www-authenticate", [])
    challenges = parse_payment_headers(www) if www else []
    x402 = parse_x402((hdr.get("payment-required") or [None])[0], capture.get("body"))
    if masked and x402:
        for a in x402["accepts"]:
            a["payTo"] = mask(a["payTo"])
    spent = ledger_spent_usd(ledger)
    decisions = [decide(c, ledger_spent_usd(ledger, "testnet") if cap_testnet and describe(c)["network"] == "testnet"
                        else spent, now, per_cap, total_cap, cap_testnet) for c in challenges]
    pick = next((i for i, (c, dd) in enumerate(zip(challenges, decisions))
                 if c.method == "tempo" and dd["decision"] == "allow"), None)
    if pick is not None:
        verdict = {"decision": "allow", "reason": decisions[pick]["reason"],
                   "amount_usd": decisions[pick]["amount_usd"], "network": decisions[pick]["network"],
                   "challenge_id": challenges[pick].id}
    elif not challenges:
        verdict = {"decision": "deny", "reason": "no MPP `WWW-Authenticate: Payment` challenge "
                   "(x402-only or not a 402); mpp-agent pays MPP challenges"}
    elif not any(c.method == "tempo" for c in challenges):
        verdict = {"decision": "deny", "reason": "no tempo challenge; methods offered: "
                   + ", ".join(sorted({str(c.method) for c in challenges}))}
    else:
        verdict = {"decision": "deny", "reason": next(dd["reason"] for c, dd in zip(challenges, decisions)
                                                       if c.method == "tempo")}
    return {
        "verdict": verdict,
        "http_status": capture.get("status"),
        "mpp": [describe(c, masked) for c in challenges],
        "x402": x402,
        "decisions": decisions,
        "selected": pick,
        "spent_mainnet_usd": spent,
        "caps": {"per_request_usd": per_cap, "total_usd": total_cap},
    }


def _legacy_cli(argv: list[str]) -> None:
    """`challenge.py "<www-authenticate>" [spent]` — kept for mpp-agent-run's mpp-pay.sh. Accepts
    the simplified SKILL.md form AND the production `Payment …` form."""
    header = argv[1] if len(argv) > 1 else 'tempo amount=0.1 currency=USDC'
    spent = float(argv[2]) if len(argv) > 2 else 0.0
    if re.match(r"\s*Payment\s", header, re.I):
        chs = parse_payment_header(header)
        decs = [decide(c, spent) for c in chs]
        i = next((k for k, c in enumerate(chs) if c.method == "tempo"), 0)
        client = "link-cli" if chs[i].method == "stripe" else "mppx"
        print(json.dumps({
            "challenges": [describe(c) for c in chs], "client": client,
            "client_reason": f"Payment method={chs[i].method}",
            "target": chs[i].raw,
            "budget": {"decision": decs[i]["decision"], "reason": decs[i]["reason"],
                       "amount_usd": decs[i]["amount_usd"] or 0},
        }, ensure_ascii=False, indent=2))
        return
    chs = parse_challenge(header)
    choice = choose_client(chs)
    tc = choice["target_challenge"]
    budget = check_budget(tc.amount, tc.currency, spent)
    print(json.dumps({
        "challenges": [c.__dict__ for c in chs],
        "client": choice["client"],
        "client_reason": choice["reason"],
        "target": tc.raw,
        "budget": budget,
    }, ensure_ascii=False, indent=2))


def _state_dir() -> str:
    return os.path.expanduser(os.environ.get("MPP_BUDGET_GUARD_HOME", "~/.mpp-budget-guard"))


def main(argv: list[str]) -> int:
    import argparse

    if len(argv) > 1 and argv[1] not in ("analyze", "record", "-h", "--help"):
        _legacy_cli(argv)
        return 0
    ap = argparse.ArgumentParser(prog="challenge.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("analyze", help="decode both rails of a 402 capture and decide")
    a.add_argument("capture", help="curl -i style capture (scripts/probe.sh writes one)")
    a.add_argument("--ledger", default=os.path.join(_state_dir(), "spend-ledger.jsonl"))
    a.add_argument("--log", default=os.path.join(_state_dir(), "decision-log.jsonl"),
                   help="append the decision record here ('' to skip)")
    a.add_argument("--per-request-cap", type=float, default=PER_REQUEST_CAP_USD, help="USD, default 0.10")
    a.add_argument("--total-cap", type=float, default=TOTAL_CAP_USD, help="USD, default 0.50 (mainnet ledger)")
    a.add_argument("--cap-testnet", action="store_true",
                   help="apply the caps to testnet challenges too (default: testnet is uncapped)")
    a.add_argument("--now", help="ISO-8601 'now' (testing); default: current UTC")
    a.add_argument("--mask", action="store_true", help="mask recipients to last 4 chars")
    r = sub.add_parser("record", help="append a completed payment to the ledger")
    r.add_argument("--ledger", default=os.path.join(_state_dir(), "spend-ledger.jsonl"))
    r.add_argument("--amount-usd", type=float, required=True)
    r.add_argument("--network", choices=("mainnet", "testnet"), default="mainnet")
    r.add_argument("--tx", required=True, help="tx hash (mppx receipt `reference`)")
    r.add_argument("--challenge-id", default=None)
    r.add_argument("--url", default=None)
    args = ap.parse_args(argv[1:])

    if args.cmd == "record":
        row = {"ts": datetime.now(timezone.utc).isoformat(), "amount_usd": args.amount_usd,
               "network": args.network, "tx": args.tx, "challenge_id": args.challenge_id, "url": args.url}
        ledger_append(args.ledger, row)
        total = ledger_spent_usd(args.ledger, args.network)
        print(json.dumps({**row, "ledger": args.ledger, f"{args.network}_total_usd": round(total, 6)}))
        return 0

    try:
        res = analyze(read_http_file(args.capture), args.ledger, _parse_ts(args.now) if args.now else None,
                      args.mask, args.per_request_cap, args.total_cap, args.cap_testnet)
    except (OSError, ValueError) as exc:
        print(json.dumps({"verdict": {"decision": "error", "reason": str(exc)}}), file=sys.stderr)
        return 2
    res["capture"] = os.path.basename(args.capture)
    if args.log:
        ledger_append(args.log, {"ts": datetime.now(timezone.utc).isoformat(), **res})
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0 if res["verdict"]["decision"] == "allow" else 3


if __name__ == "__main__":
    sys.exit(main(sys.argv))
