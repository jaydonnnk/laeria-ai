"""Demo storefront routes — discovery over the Shopify dev store.

Search is HTTP-backed (fast, structured); verify drives a real browser to the
product page and returns a screenshot. Auth comes from the router-level
require_owner dependency in api/main.py, same as every feature router.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from services.storefront import StorefrontService

router = APIRouter(prefix="/store", tags=["store"])


@router.get("/search")
def search(q: str = "", limit: int = 12) -> list[dict]:
    try:
        return StorefrontService().search_products(query=q, limit=min(limit, 50))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"store search failed: {exc}")


@router.post("/product/{handle}/verify")
def verify(handle: str) -> dict:
    """Browser-verify a product: live DOM price + availability + screenshot.
    POST because it does real work (launches a browser, writes a screenshot)."""
    try:
        return StorefrontService().verify_product(handle)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"product verify failed: {exc}")
