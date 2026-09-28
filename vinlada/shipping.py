"""Fraktberäkning utifrån butikens regler i butiker.json.

En regel kan innehålla:

    fee               fast fraktavgift under gränsen för fri frakt (null = okänd)
    free_from         fri frakt när varuvärdet är minst detta belopp
    free_from_bottles fri frakt från detta antal flaskor
    per_box           avgiften tas ut per låda i stället för per order
    tiers             [{"under": 600, "fee": 59}, ...] – avgift efter varuvärde
    included          priset inkluderar redan frakt
    approx            avgiften är osäker/ungefärlig
    zones             [{"postcode_prefixes": ["1", "41"], ...}] – regler som
                      ersätter grundregeln för vissa postnummer
    note              fri text som visas i rapporten
"""

from __future__ import annotations

from typing import Any


def rule_for_postcode(rule: dict[str, Any], postcode: str) -> dict[str, Any]:
    """Slå ihop grundregeln med den första zon vars postnummerprefix matchar."""
    postcode = postcode.replace(" ", "")
    merged = {k: v for k, v in rule.items() if k != "zones"}
    for zone in rule.get("zones", []):
        if any(postcode.startswith(p) for p in zone.get("postcode_prefixes", [])):
            merged.update({k: v for k, v in zone.items() if k != "postcode_prefixes"})
            break
    return merged


def shipping_cost(
    rule: dict[str, Any], subtotal: float, bottles: float | None, boxes: int = 1
) -> tuple[float | None, str]:
    """Returnera (fraktkostnad, kommentar). Kostnaden är None om den är okänd."""
    note = rule.get("note", "")

    def with_note(text: str) -> str:
        return f"{text}; {note}" if note and text else (text or note)

    if rule.get("included"):
        return 0.0, with_note("frakt ingår i priset")
    free_bottles = rule.get("free_from_bottles")
    if free_bottles is not None and bottles is not None and bottles >= free_bottles:
        return 0.0, with_note(f"fri frakt från {free_bottles} flaskor")
    free_from = rule.get("free_from")
    if free_from is not None and subtotal >= free_from:
        return 0.0, with_note(f"fri frakt från {free_from:g} kr")

    fee = rule.get("fee")
    for tier in rule.get("tiers", []):
        if subtotal < tier["under"]:
            fee = tier["fee"]
            break
    if fee is None:
        return None, with_note("fraktavgift okänd – kontrollera i kassan")
    if rule.get("per_box"):
        fee = fee * boxes
    text = "ca " if rule.get("approx") else ""
    return float(fee), with_note(f"{text}{fee:g} kr frakt")
