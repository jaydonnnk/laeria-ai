"""StraitsX card MCP: the two load-bearing, network-free pieces.

The x402 dance and on-chain settlement are exercised by hand on sandbox (see the
card MCP notes). These lock the parts a refactor could silently break: the
payment payload's `accepted` field (whose absence is the cohort-wide
`invalid atomic amount` bug) and the card_html credential parser.
"""
from __future__ import annotations

import base64
import json

import pytest

from services import straitsx_card as sx


# ---- payment payload shape ----

def test_payment_header_carries_the_accepted_requirement():
    """The `accepted` field is what the stateless cardapi reads the amount from.
    Without it the server can't parse an amount and 402s with
    'invalid atomic amount'. This asserts it's present with the amount inside."""
    accepted = {
        "scheme": "exact", "network": "eip155:43113", "amount": "5000000",
        "asset": "0x" + "ab" * 20, "payTo": "0x" + "cd" * 20,
        "extra": {"name": "XSGD", "version": "2"},
    }
    auth = {"from": "0x" + "11" * 20, "to": accepted["payTo"], "value": "5000000",
            "validAfter": "0", "validBefore": "999", "nonce": "0x" + "22" * 32}

    header = sx._payment_header(accepted, auth, "abc123")  # bare sig, no 0x
    payload = json.loads(base64.b64decode(header))

    assert payload["x402Version"] == 1
    assert payload["scheme"] == "exact"
    assert payload["accepted"]["amount"] == "5000000"  # the load-bearing field
    assert payload["payload"]["signature"].startswith("0x")  # normalised
    assert payload["payload"]["authorization"]["value"] == "5000000"


# ---- card_html credential parser ----

_CARD_HTML = """
<div class="card">
  <div class="card-body">
    <div class="card-number">4111 1111 1111 2362</div>
    <div class="card-trailing">
      <div class="card-expiry"><div class="exp">EXP</div><div class="exp_val">08/29</div></div>
      <div class="card-cvv"><div class="cvv">CVV</div><div class="cvv_val">123</div></div>
    </div>
    <div class="card-holder-name">Laeria Agent</div>
  </div>
</div>
"""


def test_parse_card_html_extracts_all_fields():
    d = sx.parse_card_html(_CARD_HTML)
    assert d["number"] == "4111111111112362"  # spaces stripped
    assert d["cvc"] == "123"
    assert d["exp_month"] == 8
    assert d["exp_year"] == 2029  # 2-digit year expanded
    assert d["name"] == "Laeria Agent"


def test_parse_card_html_refuses_when_no_pan():
    """A checkout with no card number is a failure, not a blank submission."""
    with pytest.raises(sx.StraitsXCardError):
        sx.parse_card_html("<div class='card'><div class='cvv_val'>123</div></div>")


def test_view_card_routes_tool_and_sse_to_card_env(monkeypatch):
    seen = {}

    def fake_mcp_call(tool, arguments, *, timeout=30.0, env=None):
        seen.update(tool=tool, arguments=arguments, timeout=timeout, env=env)
        return {"card_html": _CARD_HTML}

    monkeypatch.setattr(sx, "_mcp_call", fake_mcp_call)

    out = sx.mcp_view_card("opaque", "0xsettlement", "0x" + "11" * 20,
                           env="production")

    assert out["card_html"] == _CARD_HTML
    assert seen["tool"] == "view_card_prod"
    assert seen["env"] == "production"
    assert seen["arguments"]["card_opaque_id"] == "opaque"
