"""Demo storefront routes — discovery over the Shopify dev store.

Search is HTTP-backed (fast, structured); verify drives a real browser to the
product page and returns a screenshot. Auth comes from the router-level
require_owner dependency in api/main.py, same as every feature router.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from services.storefront import StorefrontService

router = APIRouter(prefix="/store", tags=["store"])

_SCREENSHOTS_ROOT = Path(__file__).resolve().parent.parent.parent / "screenshots"


@router.get("/search")
def search(q: str = "", limit: int = 12) -> list[dict]:
    try:
        return StorefrontService().search_products(query=q, limit=min(limit, 50))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"store search failed: {exc}")


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
