"""Fuzzy search helper — vendored from api/src/utils/fuzzy_json.py.

Used by the Quo contact / ClickUp task search tools to rank items by best
fuzzy match across all string values, with phone-digit substring as a fast
path for numeric queries.
"""
from __future__ import annotations

import json

from rapidfuzz import fuzz


def _extract_strings(obj: object, out: list[str], depth: int = 0) -> None:
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


def _score_item(strings: list[str], query: str) -> float:
    best = 0.0
    for s in strings:
        s_lower = s.lower()
        score = max(fuzz.ratio(query, s_lower), fuzz.partial_ratio(query, s_lower))
        if score > best:
            best = score
            if best == 100:
                return 100.0
    return best


def fuzzy_filter(
    items: list[dict],
    query: str,
    *,
    top_n: int = 5,
    threshold: int = 55,
) -> list[tuple[dict, float]]:
    """Return up to ``top_n`` items scored above ``threshold``, highest first."""
    q = query.lower().strip()
    if not q or not items:
        return []

    q_digits = "".join(c for c in q if c.isdigit())
    is_phone_query = (
        len(q_digits) >= 4
        and len(q_digits)
        == len(q.replace("-", "").replace(" ", "").replace("(", "").replace(")", "").replace("+", ""))
    )

    scored: list[tuple[dict, float]] = []
    for item in items:
        strings: list[str] = []
        _extract_strings(item, strings)
        if is_phone_query:
            for s in strings:
                if q_digits in "".join(c for c in s if c.isdigit()):
                    scored.append((item, 100.0))
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
    """Same as ``fuzzy_filter`` but returns a JSON string of the matches."""
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
