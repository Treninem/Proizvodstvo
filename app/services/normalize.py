from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

CYR_LAT = str.maketrans({
    "a": "а", "e": "е", "o": "о", "p": "р", "c": "с", "x": "х", "y": "у",
    "A": "а", "E": "е", "O": "о", "P": "р", "C": "с", "X": "х", "Y": "у",
})


def normalize_text(text: str) -> str:
    text = text.replace("ё", "е").replace("Ё", "е")
    text = text.translate(CYR_LAT)
    text = text.lower()
    text = re.sub(r"[^0-9a-zа-я.,:\n\s-]", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_key(text: str) -> str:
    text = normalize_text(text)
    text = re.sub(r"[,.:;-]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def split_aliases(text: str) -> list[str]:
    text = text.replace(";", ",")
    parts: list[str] = []
    for chunk in text.split("\n"):
        for item in chunk.split(","):
            item = item.strip()
            if item:
                parts.append(item)
    seen: set[str] = set()
    result: list[str] = []
    for item in parts:
        key = normalize_key(item)
        if key and key not in seen:
            seen.add(key)
            result.append(item)
    return result


@dataclass(frozen=True)
class MatchResult:
    target_type: str
    target_id: int
    name: str
    score: float
    matched_text: str
    source: str


def similarity(a: str, b: str) -> float:
    a = normalize_key(a)
    b = normalize_key(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        shorter = min(len(a), len(b))
        longer = max(len(a), len(b))
        return max(0.82, shorter / max(longer, 1))
    return difflib.SequenceMatcher(None, a, b).ratio()
