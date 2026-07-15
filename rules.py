"""
Bodovacie pravidlá. Toto je jediný súbor, ktorý budeš reálne ladiť.

Sú to heuristiky, nie overený model. Sú tu preto, aby si ich prepísal
podľa vlastného úsudku — nie preto, aby si im veril.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Rule:
    id: str
    label: str
    why: str
    points: int
    test: Callable[[dict], bool]


RULES: list[Rule] = [
    Rule(
        id="buy",
        label="Nákup na otvorenom trhu",
        why="Insider predáva z desiatich dôvodov. Kupuje z jedného.",
        points=3,
        test=lambda s: s.get("action") == "buy",
    ),
    Rule(
        id="cluster3",
        label="Klaster: 3+ insiderov",
        why="Viacero nezávislých ľudí naraz je silnejšie než jeden.",
        points=2,
        test=lambda s: s.get("clusterCount", 1) >= 3,
    ),
    Rule(
        id="cluster2",
        label="Dvojica insiderov",
        why="Slabšia verzia klastra.",
        points=1,
        test=lambda s: s.get("clusterCount", 1) == 2,
    ),
    Rule(
        id="fresh",
        label="Nahlásené do 2 dní",
        why="Čím starší filing, tým viac to už trh započítal.",
        points=1,
        test=lambda s: isinstance(s.get("lagDays"), int) and s["lagDays"] <= 2,
    ),
    Rule(
        id="stale",
        label="Nahlásené po 30+ dňoch",
        why="Informácia je prakticky historická.",
        points=-1,
        test=lambda s: isinstance(s.get("lagDays"), int) and s["lagDays"] > 30,
    ),
    Rule(
        id="seniority",
        label="CEO / CFO / predseda",
        why="Vidia viac než radový člen dozornej rady.",
        points=1,
        test=lambda s: bool(re.search(r"\b(CEO|CFO|Chief|Chair|President)\b", s.get("role") or "", re.I)),
    ),
    Rule(
        id="size",
        label="Objem nad 1 mil. USD",
        why="Hrubá skratka za 'je to preňho veľa'. Nepozná jeho majetok.",
        points=1,
        test=lambda s: (s.get("amountUsd") or 0) >= 1_000_000,
    ),
]


def score_signal(s: dict) -> tuple[int, list[dict]]:
    hits = []
    for r in RULES:
        try:
            if r.test(s):
                hits.append({"id": r.id, "label": r.label, "points": r.points})
        except Exception:
            continue
    return sum(h["points"] for h in hits), hits
