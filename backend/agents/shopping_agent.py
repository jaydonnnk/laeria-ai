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
    def __init__(self, storefront=None, llm=None, guardrails=None) -> None:  # noqa: ANN001
        from services.bedrock_guardrails import get_guardrails
        from services.llm import LLMService
        from services.storefront import StorefrontService

        self._store = storefront or StorefrontService()
        self._llm = llm or LLMService()
        # Injectable so no test needs AWS. Defaults to the shared instance,
        # which is a clean no-op unless BEDROCK_GUARDRAILS_ENABLED is set.
        self._guard = guardrails if guardrails is not None else get_guardrails()

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
        from services.bedrock_guardrails import INPUT

        # Boundary 1: the shopper's own words, before anything acts on them.
        # Raises on a refusal, so a blocked instruction reaches no model, no
        # storefront, no proposal and no payment.
        instruction = self._guard.ensure_allowed(
            instruction, INPUT, "shopping instruction"
        )

        plan = self.plan(instruction)
        query, max_price = plan["query"], plan["max_price"]
        logger.info("shopping plan: query=%r max_price=%s", query, max_price)

        candidates, evidence = self.scan(query)
        # Boundary 2: the merchant's text, before the model reads it. Product
        # titles and handles are written by whoever lists the product, and this
        # pipeline turns the model's reading of them into a purchase.
        # `candidate_lines` is the guarded, possibly-sanitized view the model
        # will be shown; `candidates` stays the authoritative product identity.
        candidates, candidate_lines = self._guard_candidates(candidates)
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

        # Only screened candidates are in this map, so a product the guardrail
        # refused cannot be resolved even if the model names it.
        by_handle = {c["handle"]: c for c in candidates}
        chosen = self._choose(instruction, plan, candidate_lines)
        # Boundary 4: the model's own answer, before it can become a proposal.
        chosen = self._guard_choice(chosen)
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

    def _guard_candidates(self, candidates: list[dict]) -> tuple[list[dict], list[str]]:
        """Screen merchant text. Returns (safe candidates, safe lines).

        WHAT IS ACTUALLY CHECKED, and why only this: the model is shown one
        rendered line per candidate, built from the handle, the title, the
        price and the availability flag. Price and availability are a number
        and a boolean this code formats itself. The handle and the title are
        merchant-controlled, and they are the entire untrusted surface —
        Laeria does not send raw product-page text to any model, so there is
        none to guard.

        THE SANITIZED LINES ARE RETURNED AND USED. `_choose` renders the prompt
        from these, not from the catalogue rows, so a masked product title
        reaches the model masked. The catalogue dictionaries are never touched:
        they stay the authoritative identity — handle, variant, price — so the
        model's view being sanitized cannot change what actually gets bought.

        A candidate whose OWN HANDLE was masked is excluded, because the handle
        is how the model names its choice and how that choice is resolved.
        """
        from services.bedrock_guardrails import INPUT, GuardrailUnavailable

        lines = [_candidate_line(i, c) for i, c in enumerate(candidates)]
        if not self._guard.enabled or not candidates:
            return candidates, lines

        verdicts = self._guard.check_many(lines, INPUT)
        if any(v.unavailable for v in verdicts):
            raise GuardrailUnavailable()

        safe: list[dict] = []
        safe_lines: list[str] = []
        for index, (candidate, verdict) in enumerate(zip(candidates, verdicts)):
            # Identified by POSITION only. The handle is merchant-controlled
            # and part of the very text that was refused, so logging it would
            # reproduce the content the guardrail removed.
            if verdict.blocked:
                logger.warning(
                    "guardrail rejected candidate #%d: %s", index, verdict.reason
                )
                continue
            if candidate["handle"] not in verdict.text:
                logger.warning(
                    "guardrail masked candidate #%d's own handle — excluding it",
                    index,
                )
                continue
            safe.append(candidate)
            safe_lines.append(_renumber(verdict.text, len(safe)))

        if len(safe) != len(candidates):
            logger.warning(
                "guardrail excluded %d of %d products from consideration",
                len(candidates) - len(safe), len(candidates),
            )
        return safe, safe_lines

    def _guard_choice(self, chosen: dict) -> dict:
        """Check the model's answer before it can turn into a proposal.

        Only the prose is checked here: `handle` is validated against the
        already-screened candidate list by the caller, and a name that is not
        on that list is refused there.

        A blocked reason means NO PICK. It does not mean "pick something else":
        answering a refusal with a different purchase is exactly the failure
        this boundary exists to prevent.
        """
        from services.bedrock_guardrails import OUTPUT, GuardrailUnavailable

        if not self._guard.enabled:
            return chosen

        rejected = [r for r in (chosen.get("rejected") or []) if isinstance(r, dict)]
        whys = [str(r.get("why") or "") for r in rejected]
        verdicts = self._guard.check_many(
            [str(chosen.get("reason") or ""), *whys], OUTPUT
        )
        if any(v.unavailable for v in verdicts):
            raise GuardrailUnavailable()

        reason_verdict, why_verdicts = verdicts[0], verdicts[1:]
        if reason_verdict.blocked:
            logger.warning(
                "guardrail blocked the model's reasoning (%s) — no pick",
                reason_verdict.reason,
            )
            return {
                "handle": "",
                "reason": (
                    "The agent's own explanation was refused by the safety "
                    "layer, so nothing was selected."
                ),
                "rejected": [],
            }

        if any(v.blocked for v in why_verdicts):
            # Secondary detail about products NOT bought. Dropping the set is a
            # clean loss; a partial list would misalign each note from the
            # product it describes.
            logger.warning("guardrail blocked a rejection note — dropping all of them")
            clean_rejected: list[dict] = []
        else:
            clean_rejected = [
                {**r, "why": v.text} for r, v in zip(rejected, why_verdicts)
            ]
        return {**chosen, "reason": reason_verdict.text, "rejected": clean_rejected}

    def _choose(self, instruction: str, plan: dict, lines: list[str]) -> dict:
        """Ask the model to choose. `lines` is what it sees — already guarded,
        and possibly sanitized, by `_guard_candidates`. It is passed in rather
        than rebuilt here so there is exactly one rendering of the merchant's
        text and the guardrail has seen it.

        The finished prompt is then checked as a whole. Every piece of it has
        been screened, but never together: two product titles that are each
        innocent can carry an instruction between them, and this is the string
        where they finally meet. A refusal means NO PICK.
        """
        user = (
            f"Shopper's instruction: {instruction}\n"
            f"Budget: {plan['max_price'] if plan['max_price'] is not None else 'none given'}\n"
            f"What matters: {plan['notes'] or 'not stated'}\n\n"
            "Search results:\n" + "\n".join(lines)
        )
        user, safe = self._guard.screen_prompt(user, "shopping choice")
        if not safe:
            return {
                "handle": "",
                "reason": (
                    "The assembled product list was refused by the safety "
                    "layer, so nothing was selected."
                ),
                "rejected": [],
            }
        try:
            return self._llm.complete_json(_PICK_SYSTEM, user, max_tokens=900)
        except Exception as exc:  # noqa: BLE001
            # No silent fallback to "first in stock" — that is the behaviour
            # this agent replaced, and quietly reverting to it would make a
            # broken model look like a working agent.
            logger.error("pick failed: %s", exc)
            return {"handle": "", "reason": f"The agent could not decide ({exc}).",
                    "rejected": []}


def _candidate_line(index: int, c: dict) -> str:
    """One candidate as the model sees it.

    Defined once and used both to build the prompt and to guard it, so what
    was checked and what is sent can never be two different strings.
    """
    return (
        f"{index + 1}. handle={c['handle']} | {c['title']} | "
        f"{c['price_usd']:.2f} | {'in stock' if c['available'] else 'SOLD OUT'}"
    )


def _renumber(line: str, position: int) -> str:
    """Re-label a guarded line with its position in the surviving list.

    Only a leading "<digits>. " is replaced, and only when the line actually
    starts with one — splitting on the first ". " unconditionally would, on a
    line whose number had been masked away, cut at a full stop inside the
    product title. Everything after the number is the guardrail's own output
    and is returned exactly as it came back.
    """
    head, sep, rest = line.partition(". ")
    if not sep or not head.isdigit():
        return line
    return f"{position}. {rest}"


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
