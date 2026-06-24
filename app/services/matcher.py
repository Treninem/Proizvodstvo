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
    text_key = normalize_key(text)
    if text_key:
        exact_matches: list[MatchResult] = []
        seen_exact: set[tuple[str, int]] = set()
        for c in list_alias_candidates(chat_id):
            if allowed_types and c["target_type"] not in allowed_types:
                continue
            if c["key"] != text_key:
                continue
            exact_key = (str(c["target_type"]), int(c["target_id"]))
            if exact_key in seen_exact:
                continue
            seen_exact.add(exact_key)
            exact_matches.append(MatchResult(c["target_type"], int(c["target_id"]), c["name"], 1.0, text, c["source"]))
        if len(exact_matches) == 1:
            return exact_matches[0], exact_matches
        if len(exact_matches) > 1:
            return None, exact_matches

    matches = best_matches(chat_id, text, allowed_types=allowed_types, limit=5)
    if not matches:
        return None, []
    top = matches[0]
    second = matches[1] if len(matches) > 1 else None
    if top.score >= 0.86 and (second is None or top.score - second.score >= 0.08):
        return top, matches
    return None, matches
