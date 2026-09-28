"""Beräkna totalpris per erbjudande och skriv ut tabell, CSV, JSON eller Markdown."""

from __future__ import annotations

import csv
import json
import shutil
from typing import Iterable, TextIO

from .models import Offer, Quote
from .shipping import rule_for_postcode, shipping_cost


def best_quote(offer: Offer, rule: dict, postcode: str, max_boxes: int = 1) -> Quote:
    """Billigaste antal lådor (1..max_boxes) räknat i kr per flaska inkl. frakt.

    Två lådor kan bli billigare per flaska om de når gränsen för fri frakt.
    """
    rule = rule_for_postcode(rule, postcode)
    best: Quote | None = None
    for boxes in range(1, max_boxes + 1):
        subtotal = offer.price * boxes
        bottles = offer.bottles * boxes if offer.bottles else None
        cost, note = shipping_cost(rule, subtotal, bottles, boxes)
        quote = Quote(offer, boxes, subtotal, cost, note)
        if best is None:
            best = quote
        elif quote.per_bottle is not None and (
            best.per_bottle is None or quote.per_bottle < best.per_bottle - 0.5
        ):
            best = quote
    assert best is not None
    return best


def sort_key(q: Quote, by_taste: bool = False) -> tuple:
    # Kända priser först, sedan okända; vid smaksökning vinner bäst match inom samma prisnivå.
    unknown = q.per_bottle is None
    main = q.per_bottle if q.per_bottle is not None else (q.total if q.total is not None else q.subtotal)
    taste = -q.offer.taste_score if by_taste else 0
    return (unknown, q.total is None, round(main or 0), taste)


def _fmt_money(value: float | None) -> str:
    return "?" if value is None else f"{value:,.0f}".replace(",", " ")


def _fmt_bottles(q: Quote) -> str:
    if q.total_bottles is None:
        return "?"
    n = q.total_bottles
    text = f"{n:g}" if n == int(n) else f"{n:.1f}"
    return ("≈" if q.offer.bottles_approx else "") + text


def _sb_text(q: Quote) -> str:
    sb = q.offer.systembolaget
    if not sb:
        return ""
    stock = sum(sb["stock"].values())
    where = f"{stock} st i lager" if stock else "beställs till butik"
    cheaper = q.per_bottle is not None and sb["price"] < q.per_bottle
    return f"{sb['price']:.0f} kr ({where}){' – billigare!' if cheaper else ''}"


def rows(quotes: Iterable[Quote]) -> list[dict]:
    result = []
    for q in quotes:
        o = q.offer
        result.append({
            "butik": o.shop,
            "lada": (f"{q.boxes}× " if q.boxes > 1 else "") + o.title,
            "flaskor": _fmt_bottles(q),
            "pris": _fmt_money(q.subtotal),
            "frakt": _fmt_money(q.shipping),
            "totalt": _fmt_money(q.total),
            "kr_per_flaska": _fmt_money(q.per_bottle),
            "smak": ", ".join(o.taste_matches),
            "kalla": o.source + (f" {o.checked}" if o.checked else ""),
            "systembolaget": _sb_text(q),
            "kommentar": "; ".join(x for x in (q.shipping_note, o.note) if x),
            "url": o.url,
        })
    return result


COLUMNS = [
    ("butik", "Butik", 14), ("lada", "Låda", 44), ("flaskor", "Fl", 4), ("pris", "Pris", 6),
    ("frakt", "Frakt", 5), ("totalt", "Totalt", 6), ("kr_per_flaska", "kr/fl", 5),
    ("smak", "Smakträff", 22), ("systembolaget", "Systembolaget", 34), ("kalla", "Källa", 15),
]
RIGHT = {"flaskor", "pris", "frakt", "totalt", "kr_per_flaska"}


def _cut(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


def _visible_columns(data: list[dict], show_taste: bool) -> list[tuple[str, str, int]]:
    show_sb = any(row["systembolaget"] for row in data)
    return [c for c in COLUMNS
            if (show_taste or c[0] != "smak") and (show_sb or c[0] != "systembolaget")]


def print_table(data: list[dict], out: TextIO, show_taste: bool, show_urls: bool) -> None:
    cols = _visible_columns(data, show_taste)
    term = shutil.get_terminal_size((160, 20)).columns
    fixed = sum(w for k, _, w in cols if k != "lada") + 3 * len(cols) + 4
    lada_width = max(24, min(60, term - fixed))
    widths = {k: (lada_width if k == "lada" else w) for k, _, w in cols}
    header = " # | " + " | ".join(h.ljust(widths[k]) for k, h, _ in cols)
    out.write(header + "\n" + "-" * len(header) + "\n")
    for i, row in enumerate(data, 1):
        cells = []
        for key, _, _ in cols:
            text = _cut(str(row[key]), widths[key])
            cells.append(text.rjust(widths[key]) if key in RIGHT else text.ljust(widths[key]))
        out.write(f"{i:>2} | " + " | ".join(cells) + "\n")
        if show_urls:
            out.write(f"     {row['url']}\n")
            if row["kommentar"]:
                out.write(f"     {row['kommentar']}\n")


def print_markdown(data: list[dict], out: TextIO, show_taste: bool) -> None:
    cols = _visible_columns(data, show_taste) + [("kommentar", "Kommentar", 0)]
    out.write("| # | " + " | ".join(h for _, h, _ in cols) + " |\n")
    out.write("|---|" + "|".join("---:" if k in RIGHT else "---" for k, _, _ in cols) + "|\n")
    for i, row in enumerate(data, 1):
        cells = []
        for key, _, _ in cols:
            text = str(row[key]).replace("|", "\\|")
            if key == "lada":
                text = f"[{text}]({row['url']})"
            cells.append(text)
        out.write(f"| {i} | " + " | ".join(cells) + " |\n")


def write_csv(data: list[dict], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(data[0]) if data else ["butik"])
        writer.writeheader()
        writer.writerows(data)


def write_json(quotes: list[Quote], path: str) -> None:
    payload = []
    for q in quotes:
        o = q.offer
        payload.append({
            "butik": o.shop, "lada": o.title, "url": o.url, "antal_lador": q.boxes,
            "flaskor": q.total_bottles, "flaskor_uppskattat": o.bottles_approx,
            "pris": q.subtotal, "frakt": q.shipping, "totalt": q.total,
            "kr_per_flaska": q.per_bottle, "i_lager": o.in_stock, "kalla": o.source,
            "kontrollerad": o.checked, "smakpoang": o.taste_score, "smaktraffar": o.taste_matches,
            "kommentar": "; ".join(x for x in (q.shipping_note, o.note) if x),
            "systembolaget": o.systembolaget,
        })
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
