"""Demo storefront routes — discovery over the Shopify dev store.

Search is HTTP-backed (fast, structured); verify drives a real browser to the
product page and returns a screenshot. Auth comes from the router-level
require_owner dependency in api/main.py, same as every feature router.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from services.storefront import StorefrontService

router = APIRouter(prefix="/store", tags=["store"])


class ShopRequest(BaseModel):
    instruction: str = Field(min_length=3, max_length=400)

_SCREENSHOTS_ROOT = Path(__file__).resolve().parent.parent.parent / "screenshots"


@router.get("/search")
def search(q: str = "", limit: int = 12) -> list[dict]:
    try:
        return StorefrontService().search_products(query=q, limit=min(limit, 50))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"store search failed: {exc}")


@router.post("/shop")
def shop(req: ShopRequest) -> dict:
    """Discovery: a free-text purchase instruction in, one located product out.

    Returns a PROPOSAL — handle, variant, price, and the agent's reasoning.
    Nothing is bought here. The caller feeds the handle and variant to
    /actions/propose, where the mandate decides whether it may execute. That
    split is deliberate: the model chooses what, the mandate decides whether,
    so no instruction can talk its way into a purchase.
    """
    from agents.shopping_agent import ShoppingAgent
    from services.bedrock_guardrails import GuardrailBlocked, GuardrailUnavailable

    try:
        pick = ShoppingAgent().shop(req.instruction)
    except GuardrailBlocked as exc:
        # The caller's own request was refused. 400, not 502: nothing failed
        # here, and dressing a refusal up as a server error would tell someone
        # to retry a request that will always be refused.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except GuardrailUnavailable as exc:
        # The safety check could not run, so the agent stopped rather than
        # shopping unverified. 503 says "try again", which is the truth.
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"shopping agent failed: {exc}")

    return {
        "found": pick.found,
        "instruction": pick.instruction,
        "query": pick.query,
        "max_price": pick.max_price,
        "handle": pick.handle,
        "title": pick.title,
        "price_usd": pick.price,
        "variant_id": pick.variant_id,
        "url": pick.url,
        "reason": pick.reason,
        "rejected": pick.rejected,
        "candidates_seen": pick.candidates_seen,
        "scanned_via": pick.scanned_via,
        "scan_note": pick.scan_note,
        "search_url": pick.search_url,
        "screenshot_path": pick.screenshot_path,
        "unsafe_candidates_excluded": pick.unsafe_candidates_excluded,
    }


@router.get("/screenshot/{category}/{filename}")
def screenshot(category: str, filename: str) -> FileResponse:
    """Serve an audit-trail screenshot (store verification or checkout stage).
    Filename-only lookup inside the fixed screenshots root — no traversal."""
    if category not in ("store", "checkout") or Path(filename).name != filename:
        raise HTTPException(status_code=404, detail="not found")
    path = _SCREENSHOTS_ROOT / category / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(path, media_type="image/png")


@router.post("/product/{handle}/verify")
def verify(handle: str) -> dict:
    """Browser-verify a product: live DOM price + availability + screenshot.
    POST because it does real work (launches a browser, writes a screenshot)."""
    try:
        return StorefrontService().verify_product(handle)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"product verify failed: {exc}")
