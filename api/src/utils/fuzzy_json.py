"""
Generic fuzzy search over JSON arrays.

Given a list of dicts (from any API response), scores each item by fuzzy-matching
a query against ALL string values in the object (recursively), then returns the
top N items as valid JSON — ranked by descending match score.

Usage:
    results = fuzzy_filter(items, "emilio", top_n=5)
    # → list of (item, score) tuples, highest score first

Works with any shape: OpenPhone contacts, Google Drive files, ClickUp tasks, etc.
The caller just provides the array — no schema knowledge needed.
"""

from __future__ import annotations

import json

from rapidfuzz import fuzz


def _extract_strings(obj: object, out: list[str], depth: int = 0) -> None:
    """Recursively collect all non-trivial string values from a nested structure."""
    if depth > 10:
        return
    if isinstance(obj, str):
        if len(obj) > 2 and not obj.startswith(("http://", "https://")):
            out.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            _extract_strings(v, out, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            _extract_strings(v, out, depth + 1)


def _score_item(strings: list[str], query: str) -> int:
    """Best fuzzy score of `query` against any string in the item."""
    best = 0
    for s in strings:
        s_lower = s.lower()
        # partial_ratio handles substring-like matches ("anna" in "Anna Smith")
        score = max(
            fuzz.ratio(query, s_lower),
            fuzz.partial_ratio(query, s_lower),
        )
        if score > best:
            best = score
            if best == 100:
                return 100  # can't beat perfect
    return best


def fuzzy_filter(
    items: list[dict],
    query: str,
    *,
    top_n: int = 5,
    threshold: int = 55,
) -> list[tuple[dict, int]]:
    """Fuzzy-search a list of JSON objects, returning top N matches.

    Args:
        items: Array of dicts (any shape — contacts, files, tasks, etc.).
        query: Search string (name, phone, keyword, etc.).
        top_n: Max results to return.
        threshold: Minimum score (0–100) to include.

    Returns:
        List of (item, score) tuples, sorted by descending score.
    """
    q = query.lower().strip()
    if not q or not items:
        return []

    # Digit-only queries get special handling (phone number search)
    q_digits = "".join(c for c in q if c.isdigit())
    is_phone_query = len(q_digits) >= 4 and len(q_digits) == len(q.replace("-", "").replace(" ", "").replace("(", "").replace(")", "").replace("+", ""))

    scored: list[tuple[dict, int]] = []
    for item in items:
        strings: list[str] = []
        _extract_strings(item, strings)

        if is_phone_query:
            # For phone queries, do exact digit substring match
            for s in strings:
                s_digits = "".join(c for c in s if c.isdigit())
                if q_digits in s_digits:
                    scored.append((item, 100))
                    break
        else:
            score = _score_item(strings, q)
            if score >= threshold:
                scored.append((item, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_n]


def fuzzy_filter_json(
    items: list[dict],
    query: str,
    *,
    top_n: int = 5,
    threshold: int = 55,
) -> str:
    """Same as fuzzy_filter but returns a JSON string of just the matched items."""
    matches = fuzzy_filter(items, query, top_n=top_n, threshold=threshold)
    if not matches:
        return json.dumps({"results": [], "query": query, "total_matches": 0})
    return json.dumps(
        {
            "results": [item for item, _ in matches],
            "scores": [score for _, score in matches],
            "query": query,
            "total_matches": len(matches),
        },
        default=str,
    )
