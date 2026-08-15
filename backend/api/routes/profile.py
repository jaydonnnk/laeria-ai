"""Profile routes — the details the agent needs to act on a user's behalf.

Today that is the shipping address the card rail ships physical goods to
(services/checkout.py execute_checkout). It is stored per-user in
profiles.shipping; the env SHIPPING_* profile is the fallback for an account
that has not filled one in yet.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/profile", tags=["profile"])


class ShippingUpdate(BaseModel):
    # All optional at the type level — a half-filled profile is allowed to save
    # (the checkout falls back to the env address until it is complete). The
    # `complete` flag in the response tells the UI whether the agent can ship.
    name: str = Field(default="", max_length=120)
    email: str = Field(default="", max_length=200)
    address1: str = Field(default="", max_length=200)
    city: str = Field(default="", max_length=120)
    postal_code: str = Field(default="", max_length=32)
    country_code: str = Field(default="", max_length=2)  # ISO-3166 alpha-2, e.g. "SG"
    zone_code: str = Field(default="", max_length=8)  # state/province where a country needs one


@router.get("")
def get_profile() -> dict:
    from db import repositories as repo
    from services.checkout import shipping_is_complete

    try:
        shipping = repo.get_shipping()
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"shipping": shipping or {}, "complete": shipping_is_complete(shipping)}


@router.put("")
def put_profile(req: ShippingUpdate) -> dict:
    from db import repositories as repo
    from services.checkout import shipping_is_complete

    data = req.model_dump()
    # Shopify wants the country/zone as upper-case codes; normalise here so the
    # user can type "sg" and the checkout still matches the option.
    data["country_code"] = data["country_code"].strip().upper()
    data["zone_code"] = data["zone_code"].strip().upper()
    saved = repo.set_shipping(data)
    return {"shipping": saved, "complete": shipping_is_complete(saved)}
