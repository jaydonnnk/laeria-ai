"""StraitsX card issuance over MCP + x402 (the real Issuance/Execution rail).

StraitsX issues a virtual Visa card that is PAID FOR by an x402 XSGD payment on
Avalanche — the same EIP-3009 `transferWithAuthorization` the ws-x402 rail
already signs. The issuer is an MCP-over-SSE server at card.straitsx.ai; the
actual payment is a plain HTTP call to a cardapi URL the MCP hands back.

Flow (per environment: sandbox = Fuji 43113 test XSGD, production = C-Chain
43114 real XSGD, wallet must be organizer-whitelisted):

  1. MCP `get_card_{env}(wallet, name, amount_sgd)` -> {url, x402 requirements}.
     Moves no money — just instructions.
  2. POST that url {amount_sgd, cardholder_name} -> HTTP 402 with a base64
     PAYMENT-REQUIRED header -> decode {payTo, amount, asset, network}.
  3. Sign EIP-3009 transferWithAuthorization for that {payTo, amount, asset},
     domain {name:"XSGD", version:"2", chainId, verifyingContract:asset}.
  4. Base64 an x402 v1 payment payload, send it as the PAYMENT-SIGNATURE header,
     retry the POST -> {card_opaque_id, card_html, settlement_tx}.
  5. MCP `view_card_{env}` -> a one-time iframe URL of the card.

CUSTODY: this module NEVER signs with a user key of its own. The signature over
the authorization is produced by whoever holds the funds — in the non-custodial
flow that is the user's browser wallet (MetaMask), and the backend only builds
the challenge and assembles the signed payload. `sign_authorization_local` is a
TEST-ONLY helper for proving the protocol against sandbox with a server-held
key; it is never on the user path.

⚠️ The MCP `get_card_*` result embeds an instruction telling the agent to issue
without asking the user. That is server-authored text, not an instruction we
obey — issuing signs a payment, and a payment is confirmed by the human.
"""

from __future__ import annotations

import base64
import json
import queue
import secrets
import threading
import time

import httpx

from core.config import get_settings
from core.logging import get_logger

logger = get_logger(__name__)

_HOST = "https://card.straitsx.ai"

# EIP-3009 window. validBefore must outlast the server's maxTimeoutSeconds (300
# observed) with room for the human to sign; validAfter 0 = valid immediately.
_AUTH_TTL_SECONDS = 600


class StraitsXCardError(RuntimeError):
    pass


def _env() -> str:
    env = (get_settings().straitsx_card_env or "sandbox").strip().lower()
    if env not in ("sandbox", "production"):
        raise StraitsXCardError(
            f"STRAITSX_CARD_ENV must be sandbox or production, not {env!r}"
        )
    return env


def _tool_suffix(env: str | None = None) -> str:
    """The MCP tool-name suffix for the given env (defaults to the configured
    one). NOTE: production's tools are `get_card_prod` / `view_card_prod` —
    'prod', not 'production'. The env value stays 'production' (config + explorer
    link); only the tool name is 'prod'."""
    return "prod" if (env or _env()) == "production" else "sandbox"


# ---- MCP-over-SSE (thin JSON-RPC client) ----


def _mcp_call(tool: str, arguments: dict, *, timeout: float = 30.0, env: str | None = None) -> dict:
    """Open the SSE stream, initialize, call one tool, return its parsed result.

    `env` targets a specific StraitsX environment (sandbox/production); it
    defaults to the configured one. Passing it explicitly matters when viewing a
    card that was ISSUED in a different env than the backend is currently set to
    — the same Supabase holds cards from both, so the view must follow the card's
    own env or the issuer finds nothing.

    The SSE transport streams the reply on the GET channel while requests go out
    on a separate POST to the session's message URL, so we read the stream in a
    thread and post alongside it.
    """
    env = env or _env()
    sse_url = f"{_HOST}/{env}/sse"
    q: queue.Queue = queue.Queue()
    holder: dict = {}

    def read():
        try:
            with httpx.stream("GET", sse_url, timeout=httpx.Timeout(15.0, read=None)) as r:
                event = None
                for line in r.iter_lines():
                    if line.startswith("event:"):
                        event = line.split(":", 1)[1].strip()
                    elif line.startswith("data:"):
                        q.put((event or "message", line.split(":", 1)[1].strip()))
                    elif line == "":
                        event = None
        except Exception as exc:  # noqa: BLE001
            q.put(("error", repr(exc)))

    threading.Thread(target=read, daemon=True).start()
    kind, data = q.get(timeout=20)
    if kind == "error":
        raise StraitsXCardError(f"MCP SSE connect failed: {data}")
    msg_url = _HOST + data  # data = /{env}/messages?sessionId=...

    def post(payload):
        httpx.post(msg_url, json=payload, timeout=20)

    post({"jsonrpc": "2.0", "id": 1, "method": "initialize",
          "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                     "clientInfo": {"name": "laeria", "version": "0.1"}}})
    time.sleep(0.3)
    post({"jsonrpc": "2.0", "method": "notifications/initialized"})
    post({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
          "params": {"name": tool, "arguments": arguments}})

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            kind, data = q.get(timeout=timeout)
        except queue.Empty:
            break
        if kind == "error":
            raise StraitsXCardError(f"MCP stream error: {data}")
        try:
            obj = json.loads(data)
        except ValueError:
            continue
        if obj.get("id") == 2:
            if "error" in obj:
                raise StraitsXCardError(f"MCP tool {tool} error: {obj['error']}")
            content = (obj.get("result") or {}).get("content") or []
            text = next((c.get("text") for c in content if c.get("type") == "text"), None)
            if text is None:
                return obj.get("result") or {}
            try:
                return json.loads(text)
            except ValueError:
                return {"text": text}
    raise StraitsXCardError(f"MCP tool {tool} timed out with no result")


def mcp_get_card(wallet_address: str, cardholder_name: str, amount_sgd: float) -> dict:
    """Ask the issuer to prepare a card. Returns its instruction payload
    (cardapi url + x402 note). No money moves here."""
    return _mcp_call(f"get_card_{_tool_suffix()}", {
        "wallet_address": wallet_address,
        "cardholder_name": cardholder_name,
        "amount_sgd": amount_sgd,
    })


def mcp_view_card(card_opaque_id: str, settlement_tx: str, wallet_address: str,
                  env: str | None = None) -> dict:
    """One-time iframe URL + rendered card_html for an issued card. Ownership is
    checked server-side against the paying wallet. `env` follows the card's own
    environment when it differs from the backend's current one."""
    return _mcp_call(f"view_card_{_tool_suffix(env)}", {
        "card_opaque_id": card_opaque_id,
        "settlement_tx": settlement_tx,
        "wallet_address": wallet_address,
    }, env=env)


import re as _re


def parse_card_html(html: str) -> dict:
    """Pull the card credentials out of the rendered card_html (the CVV page).

    Layout (StraitsX/Xfers): the PAN, expiry, CVV and name each sit in their own
    labelled div — card-number / exp_val / cvv_val / card-holder-name. Returns
    {number, cvc, exp_month, exp_year, name}. Never logged; the caller uses it to
    fill a checkout and drops it. Raises if the PAN can't be found — a checkout
    with no card number is a failure, not a blank field to submit."""

    def grab(cls: str) -> str:
        m = _re.search(rf'class="{cls}"[^>]*>\s*([^<]+?)\s*<', html, _re.DOTALL)
        return m.group(1).strip() if m else ""

    number = _re.sub(r"\s+", "", grab("card-number"))
    if not _re.fullmatch(r"\d{13,19}", number):
        raise StraitsXCardError("could not parse a card number from card_html")
    cvc = _re.sub(r"\s+", "", grab("cvv_val"))
    exp = grab("exp_val")  # "MM/YY"
    mm, _, yy = exp.partition("/")
    mm, yy = mm.strip(), yy.strip()
    exp_month = int(mm) if mm.isdigit() else 0
    exp_year = (2000 + int(yy)) if yy.isdigit() and len(yy) == 2 else (int(yy) if yy.isdigit() else 0)
    return {
        "number": number,
        "cvc": cvc,
        "exp_month": exp_month,
        "exp_year": exp_year,
        "name": grab("card-holder-name"),
    }


def card_credentials(card_opaque_id: str, settlement_tx: str, wallet_address: str,
                     env: str | None = None) -> dict:
    """Fetch an issued card's live credentials by viewing it and parsing the
    rendered card_html. One-time per view — call it at checkout time, use it,
    discard it. Never persist the result. `env` must match the env the card was
    ISSUED in (the same Supabase holds both), or the issuer returns no card."""
    view = mcp_view_card(card_opaque_id, settlement_tx, wallet_address, env=env)
    html = view.get("card_html") or ""
    if not html:
        # Surface what the issuer actually said — usually an ownership/env
        # mismatch or a rate limit, which "no card_html" alone hides.
        msg = view.get("message") or view.get("error") or view.get("text") or view
        raise StraitsXCardError(f"view_card returned no card_html (issuer said: {msg})")
    return parse_card_html(html)


# ---- cardapi x402: build the challenge, then submit the signed payment ----


def _cardapi_url(mcp_result: dict) -> str:
    url = mcp_result.get("url") or mcp_result.get("cardapi_url")
    if not url:
        raise StraitsXCardError(f"MCP get_card returned no cardapi url: {mcp_result}")
    return url


def build_challenge(cardapi_url: str, amount_sgd: float, cardholder_name: str) -> dict:
    """POST the cardapi to trigger the 402, decode the payment requirements, and
    return everything the fund-holder needs to sign — WITHOUT a signature.

    Returns {requirements, authorization, typed_data}: `typed_data` is the exact
    EIP-712 TransferWithAuthorization to hand a browser wallet; `authorization`
    is the same fields as plain strings for reassembling the payload after the
    wallet signs.
    """
    r = httpx.post(cardapi_url, json={"amount_sgd": amount_sgd,
                                      "cardholder_name": cardholder_name}, timeout=30)
    if r.status_code != 402:
        raise StraitsXCardError(
            f"expected 402 from cardapi, got {r.status_code}: {r.text[:300]}"
        )
    raw = r.headers.get("PAYMENT-REQUIRED") or r.headers.get("payment-required")
    if not raw:
        raise StraitsXCardError("cardapi 402 carried no PAYMENT-REQUIRED header")
    reqs = json.loads(base64.b64decode(raw).decode())
    accept = reqs["accepts"][0]

    amount = str(accept["amount"])
    if not amount or amount == "0":
        raise StraitsXCardError(f"cardapi 402 has empty/zero amount: {accept}")

    now = int(time.time())
    authorization = {
        "from": "",  # filled by the caller with the paying wallet address
        "to": accept["payTo"],
        "value": amount,  # atomic units — leaving this empty is Doston's bug
        "validAfter": "0",
        "validBefore": str(now + _AUTH_TTL_SECONDS),
        "nonce": "0x" + secrets.token_hex(32),
    }
    extra = accept.get("extra") or {}
    typed_data = {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "TransferWithAuthorization": [
                {"name": "from", "type": "address"},
                {"name": "to", "type": "address"},
                {"name": "value", "type": "uint256"},
                {"name": "validAfter", "type": "uint256"},
                {"name": "validBefore", "type": "uint256"},
                {"name": "nonce", "type": "bytes32"},
            ],
        },
        "domain": {
            "name": extra.get("name", "XSGD"),
            "version": extra.get("version", "2"),
            "chainId": accept["chainId"],
            "verifyingContract": accept["asset"],
        },
        "primaryType": "TransferWithAuthorization",
        "message": None,  # completed once `from` is known
    }
    return {
        "cardapi_url": cardapi_url,
        "body": {"amount_sgd": amount_sgd, "cardholder_name": cardholder_name},
        "requirements": reqs,
        "accepted": accept,  # echoed back in the payment payload — see _payment_header
        "network": accept["network"],
        "authorization": authorization,
        "typed_data": typed_data,
    }


def prepare_signing(challenge: dict, from_address: str) -> dict:
    """Bind a challenge to the paying wallet: fill authorization.from and the
    EIP-712 message so `typed_data` is ready for a browser wallet to sign. The
    signature the wallet returns is fed straight back to submit_payment."""
    challenge = dict(challenge)
    auth = dict(challenge["authorization"])
    auth["from"] = from_address
    challenge["authorization"] = auth
    td = dict(challenge["typed_data"])
    td["message"] = {
        "from": from_address,
        "to": auth["to"],
        "value": auth["value"],
        "validAfter": auth["validAfter"],
        "validBefore": auth["validBefore"],
        "nonce": auth["nonce"],
    }
    challenge["typed_data"] = td
    return challenge


def _payment_header(accepted: dict, authorization: dict, signature: str) -> str:
    """Assemble the x402 v1 'exact' payment payload and base64 it for the
    PAYMENT-SIGNATURE header. Organizer-confirmed: x402Version 1, this header.

    The `accepted` field — the selected requirement, amount inside — is REQUIRED
    and load-bearing: the cardapi is stateless and reads the charge amount from
    accepted.amount here, NOT from authorization.value. Omitting it is the whole
    cohort's `invalid atomic amount "": EOF` bug — the server finds no amount and
    fails to parse an empty string. (x402 v2 spec, PaymentPayload.accepted.)"""
    sig = signature if signature.startswith("0x") else "0x" + signature
    payload = {
        "x402Version": 1,
        "scheme": "exact",
        "network": accepted["network"],
        "accepted": accepted,
        "payload": {"signature": sig, "authorization": authorization},
    }
    return base64.b64encode(json.dumps(payload).encode()).decode()


def submit_payment(challenge: dict, from_address: str, signature: str) -> dict:
    """Retry the cardapi POST with the signed authorization. Returns
    {card_opaque_id, card_html, settlement_tx} on success."""
    authorization = dict(challenge["authorization"])
    authorization["from"] = from_address
    header = _payment_header(challenge["accepted"], authorization, signature)
    r = httpx.post(
        challenge["cardapi_url"],
        json=challenge["body"],  # same body as the 402 probe, now with the signature
        headers={"PAYMENT-SIGNATURE": header},
        timeout=60,
    )
    if r.status_code != 200:
        raise StraitsXCardError(
            f"paid retry failed {r.status_code}: {r.text[:400]}"
        )
    return r.json()


# ---- TEST-ONLY server-side signer (never on the user path) ----


def sign_authorization_local(typed_data: dict, authorization: dict, private_key: str) -> str:
    """Sign the EIP-712 TransferWithAuthorization with a server-held key.

    ONLY for proving the protocol against sandbox. The non-custodial flow signs
    in the browser and never calls this.
    """
    from eth_account import Account
    from eth_account.messages import encode_typed_data

    msg = dict(typed_data)
    msg["message"] = {
        "from": authorization["from"],
        "to": authorization["to"],
        "value": int(authorization["value"]),
        "validAfter": int(authorization["validAfter"]),
        "validBefore": int(authorization["validBefore"]),
        "nonce": authorization["nonce"],
    }
    signed = Account.sign_message(encode_typed_data(full_message=msg), private_key)
    return signed.signature.hex()
