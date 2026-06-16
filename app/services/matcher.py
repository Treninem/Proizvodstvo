from __future__ import annotations

from .normalize import MatchResult, normalize_key, similarity
from .repository import list_alias_candidates


def best_matches(chat_id: int, text: str, allowed_types: set[str] | None = None, limit: int = 5) -> list[MatchResult]:
    text_key = normalize_key(text)
    if not text_key:
        return []
    results: list[MatchResult] = []
    for c in list_alias_candidates(chat_id):
        if allowed_types and c["target_type"] not in allowed_types:
            continue
        key = c["key"]
        score = similarity(text_key, key)
        if key and (key in text_key or text_key in key):
            score = max(score, 0.9)
        if score >= 0.58:
            results.append(MatchResult(c["target_type"], int(c["target_id"]), c["name"], score, text, c["source"]))
    results.sort(key=lambda m: m.score, reverse=True)
    unique: list[MatchResult] = []
    seen: set[tuple[str, int]] = set()
    for r in results:
        key = (r.target_type, r.target_id)
        if key in seen:
            continue
        seen.add(key)
        unique.append(r)
        if len(unique) >= limit:
            break
    return unique


def confident_match(chat_id: int, text: str, allowed_types: set[str] | None = None) -> tuple[MatchResult | None, list[MatchResult]]:
    matches = best_matches(chat_id, text, allowed_types=allowed_types, limit=5)
    if not matches:
        return None, []
    top = matches[0]
    second = matches[1] if len(matches) > 1 else None
    if top.score >= 0.86 and (second is None or top.score - second.score >= 0.08):
        return top, matches
    return None, matches
