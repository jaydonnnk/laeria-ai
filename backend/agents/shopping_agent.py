"""Shopping agent — the Discovery milestone, done by a model rather than a filter.

    instruction -> plan -> browser search -> pick -> proposal

The milestone reads: "an AI agent receives a purchase instruction, scans an
e-commerce site, and locates the item." Each clause is a step here, and each
was previously missing or done by something that was not an agent:

* RECEIVES AN INSTRUCTION. Free text in ("get me ski wax, under $30"). A model
  turns it into a search query and a budget. Previously a React component
  passed a bare query string and nothing interpreted it.
* SCANS THE SITE. A real browser opens the shop's own search page and reads
  the results that rendered — see StorefrontService.browser_search. Falls back
  to the JSON catalogue when the page cannot be read, and says which happened.
* LOCATES THE ITEM. A model picks from what was actually found, gives a reason,
  and says what it rejected. Previously `next(p for p in results if available)`
  — first in stock wins, no judgement anywhere.

The pick is a PROPOSAL, never a purchase. It returns a handle and variant, and
the caller feeds those to /actions/propose, where the mandate decides. Keeping
those separate is what stops a prompt from being able to spend money: the model
chooses WHAT, the mandate decides WHETHER.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.logging import get_logger

logger = get_logger(__name__)

# Enough of the catalogue for a judgement without burning the context window.
_MAX_CANDIDATES = 12

_PLAN_SYSTEM = """You turn a shopper's instruction into a storefront search.

Return JSON only:
{
  "query": "<2-4 words to type into the shop's search box>",
  "max_price": <number or null>,
  "notes": "<what matters to this shopper, one short phrase>"
}

The query is typed into a shop's search box, so it must be the words that name
the product — not a sentence, not a budget, not adjectives a merchant would
never put in a title. "get me some ski wax, nothing over $30" -> "ski wax".
max_price is the shopper's ceiling if they gave one, otherwise null."""

_PICK_SYSTEM = """You choose ONE product for a shopper from a shop's search results.

You may only choose from the numbered candidates given. Never invent a handle.

Return JSON only:
{
  "handle": "<handle of your choice, or empty string if nothing fits>",
  "reason": "<one sentence, why this one, in plain language>",
  "rejected": [{"handle": "<handle>", "why": "<short reason>"}]
}

Rules:
- Respect max_price when given. Over budget is not a candidate, however good.
- Unavailable items are not candidates.
- If nothing fits, return an empty handle and explain in reason. Choosing a bad
  product is worse than choosing none — the shopper is spending real money.
- Put every candidate you considered and did not pick in rejected, briefly."""


@dataclass
class ShoppingPick:
    """What the agent decided, and the evidence it decided from."""

    instruction: str
    query: str
    max_price: float | None
    handle: str
    title: str
    price: float
    variant_id: str
    url: str
    reason: str
    rejected: list[dict] = field(default_factory=list)
    candidates_seen: int = 0
    # "browser" when the shop's own search page was read, "catalogue" when that
    # failed and the JSON endpoint answered instead. Surfaced so a degraded run
    # is visible rather than silently equivalent.
    scanned_via: str = "browser"
    scan_note: str = ""
    search_url: str = ""
    screenshot_path: str = ""

    @property
    def found(self) -> bool:
        return bool(self.handle)


class ShoppingAgent:
    def __init__(self, storefront=None, llm=None) -> None:  # noqa: ANN001
        from services.llm import LLMService
        from services.storefront import StorefrontService

        self._store = storefront or StorefrontService()
        self._llm = llm or LLMService()

    # ---- step 1: understand the instruction ----

    def plan(self, instruction: str) -> dict:
        """Instruction -> {query, max_price, notes}. Degrades to using the
        instruction verbatim as the query, because a shop search with clumsy
        words still beats refusing to look."""
        try:
            raw = self._llm.complete_json(_PLAN_SYSTEM, instruction, max_tokens=400)
        except Exception as exc:  # noqa: BLE001
            logger.warning("plan failed (%s); searching the raw instruction", exc)
            return {"query": instruction.strip()[:60], "max_price": None, "notes": ""}

        query = str(raw.get("query") or "").strip() or instruction.strip()[:60]
        max_price = raw.get("max_price")
        try:
            max_price = float(max_price) if max_price is not None else None
        except (TypeError, ValueError):
            max_price = None
        return {"query": query, "max_price": max_price, "notes": str(raw.get("notes") or "")}

    # ---- step 2: scan the shop ----

    def scan(self, query: str) -> tuple[list[dict], dict]:
        """Search the shop in a browser; fall back to the JSON catalogue.

        Returns (candidates, evidence). Evidence records which path answered,
        so a run that degraded is never presented as one that scanned.
        """
        evidence = {"scanned_via": "browser", "note": "", "url": "", "screenshot_path": ""}
        try:
            found = self._store.browser_search(query, limit=_MAX_CANDIDATES)
            evidence["url"] = found["url"]
            evidence["screenshot_path"] = found["screenshot_path"]
            products = []
            for handle in found["handles"]:
                try:
                    product = self._store.get_product(handle)
                except Exception as exc:  # noqa: BLE001
                    logger.info("could not resolve %s: %s", handle, exc)
                    continue
                if product:
                    products.append(product)
            if products:
                return products, evidence
            # The page rendered but held no products — a real "no results",
            # not a failure. Say so rather than quietly re-searching the API
            # and presenting whatever it returns as what the shop showed.
            evidence["note"] = "the shop's search page returned no products"
            return [], evidence
        except Exception as exc:  # noqa: BLE001
            logger.warning("browser search failed (%s); using the catalogue", exc)
            evidence["scanned_via"] = "catalogue"
            evidence["note"] = f"browser search unavailable ({exc})"

        return self._store.search_products(query=query, limit=_MAX_CANDIDATES), evidence

    # ---- step 3: choose ----

    def shop(self, instruction: str) -> ShoppingPick:
        plan = self.plan(instruction)
        query, max_price = plan["query"], plan["max_price"]
        logger.info("shopping plan: query=%r max_price=%s", query, max_price)

        candidates, evidence = self.scan(query)
        pick = ShoppingPick(
            instruction=instruction,
            query=query,
            max_price=max_price,
            handle="", title="", price=0.0, variant_id="", url="",
            reason="",
            candidates_seen=len(candidates),
            scanned_via=str(evidence["scanned_via"]),
            scan_note=str(evidence["note"]),
            search_url=str(evidence["url"]),
            screenshot_path=str(evidence["screenshot_path"]),
        )
        if not candidates:
            pick.reason = (
                f"Searched the shop for {query!r} and found nothing to consider."
                + (f" ({evidence['note']})" if evidence["note"] else "")
            )
            return pick

        by_handle = {c["handle"]: c for c in candidates}
        chosen = self._choose(instruction, plan, candidates)
        handle = str(chosen.get("handle") or "")
        product = by_handle.get(handle)

        if handle and product is None:
            # The model named something that was not on the page. Refuse rather
            # than fuzzy-matching it onto a real product: a purchase built on a
            # hallucinated handle is exactly the failure this pipeline must not
            # have.
            logger.warning("model picked unknown handle %r; treating as no pick", handle)
            pick.reason = (
                "The agent named a product that was not in the search results, "
                "so nothing was selected."
            )
            pick.rejected = _clean_rejected(chosen.get("rejected"), by_handle)
            return pick

        pick.rejected = _clean_rejected(chosen.get("rejected"), by_handle)
        if product is None:
            pick.reason = str(chosen.get("reason") or "Nothing in the results fitted.")
            return pick

        # Last guard, in code rather than in the prompt: a model that ignores
        # the budget must not be able to propose an over-budget purchase. The
        # mandate would catch it downstream, but a refusal that names the
        # budget is a better answer than one that names a spending cap.
        if max_price is not None and float(product["price_usd"]) > max_price + 1e-9:
            pick.reason = (
                f"The best match, {product['title']}, is "
                f"{product['price_usd']:.2f} — over the {max_price:.2f} limit."
            )
            pick.rejected = [
                {"handle": product["handle"], "why": "over budget"},
                *pick.rejected,
            ]
            return pick

        pick.handle = product["handle"]
        pick.title = product["title"]
        pick.price = float(product["price_usd"])
        pick.variant_id = product["variant_id"]
        pick.url = product["url"]
        pick.reason = str(chosen.get("reason") or "Best fit among the search results.")
        return pick

    def _choose(self, instruction: str, plan: dict, candidates: list[dict]) -> dict:
        lines = [
            f"{i + 1}. handle={c['handle']} | {c['title']} | "
            f"{c['price_usd']:.2f} | {'in stock' if c['available'] else 'SOLD OUT'}"
            for i, c in enumerate(candidates)
        ]
        user = (
            f"Shopper's instruction: {instruction}\n"
            f"Budget: {plan['max_price'] if plan['max_price'] is not None else 'none given'}\n"
            f"What matters: {plan['notes'] or 'not stated'}\n\n"
            "Search results:\n" + "\n".join(lines)
        )
        try:
            return self._llm.complete_json(_PICK_SYSTEM, user, max_tokens=900)
        except Exception as exc:  # noqa: BLE001
            # No silent fallback to "first in stock" — that is the behaviour
            # this agent replaced, and quietly reverting to it would make a
            # broken model look like a working agent.
            logger.error("pick failed: %s", exc)
            return {"handle": "", "reason": f"The agent could not decide ({exc}).",
                    "rejected": []}


def _clean_rejected(raw, by_handle: dict) -> list[dict]:  # noqa: ANN001
    """Keep only rejections naming a product that was actually on the page."""
    out: list[dict] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        handle = str(item.get("handle") or "")
        product = by_handle.get(handle)
        if product is None:
            continue
        out.append(
            {
                "handle": handle,
                "title": product["title"],
                "price_usd": product["price_usd"],
                "why": str(item.get("why") or "")[:160],
            }
        )
    return out
