"""Tolkning av priser, flaskantal och produktdata ur HTML/JSON."""

from __future__ import annotations

import html
import json
import re
from html.parser import HTMLParser
from typing import Any, Iterator
from urllib.parse import urljoin, urldefrag

# ---------------------------------------------------------------------------
# Priser
# ---------------------------------------------------------------------------


def parse_price(value: Any) -> float | None:
    """Tolka ett pris som "1 499,00 kr", "1.499,95", "699.00" eller 699."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace("\xa0", " ").replace(" ", " ")
    text = re.sub(r"[^\d.,]", "", text)
    if not text or not re.search(r"\d", text):
        return None
    if "," in text and "." in text:
        decimal = "," if text.rfind(",") > text.rfind(".") else "."
        thousands = "." if decimal == "," else ","
        text = text.replace(thousands, "").replace(decimal, ".")
    elif "," in text or "." in text:
        sep = "," if "," in text else "."
        head, _, tail = text.rpartition(sep)
        if len(tail) == 3 and head:
            text = text.replace(sep, "")  # tusentalsavgränsare: "1.499"
        else:
            text = head.replace(sep, "") + "." + tail
    try:
        return float(text)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Antal flaskor
# ---------------------------------------------------------------------------

_BOTTLE_WORD = r"(?:flaskor|flaska|flasker|flaske|flaschen|bottles?|bouteilles|btl\.?|fl\.|fl\b)"
_NUMBER_WORDS = {
    "två": 2, "tre": 3, "fyra": 4, "fem": 5, "sex": 6, "sju": 7, "åtta": 8,
    "nio": 9, "tio": 10, "tolv": 12, "arton": 18, "seks": 6, "otte": 8, "ni": 9,
}

_PATTERNS: list[tuple[re.Pattern[str], Any]] = [
    # "3x2 flaskor" -> 6
    (re.compile(r"(?<!\d)(\d{1,2})\s*[x×]\s*(\d{1,2})\s*" + _BOTTLE_WORD, re.I),
     lambda m: int(m.group(1)) * int(m.group(2))),
    # "6 x 75 cl"
    (re.compile(r"(?<!\d)(\d{1,2})\s*[x×]\s*75\s*cl", re.I), lambda m: int(m.group(1))),
    # "6 flaskor", "12 st. flaskor", "6 fl."
    (re.compile(r"(?<!\d)(\d{1,2})\s*(?:st\.?|stk\.?)?\s*" + _BOTTLE_WORD, re.I),
     lambda m: int(m.group(1))),
    # "6-pack", "12 pak"
    (re.compile(r"(?<!\d)(\d{1,2})[\s-]*(?:pack|pak)\b", re.I), lambda m: int(m.group(1))),
    # "sexflaskorslåda", "tolv flaskor"
    (re.compile(r"\b(" + "|".join(_NUMBER_WORDS) + r")[\s-]*(?:flask|flaske|flasker)", re.I),
     lambda m: _NUMBER_WORDS[m.group(1).lower()]),
]

_BIB = re.compile(r"bag[\s-]*in[\s-]*box|\bbib\b|\bbox\b|vinbox|\bboxvin", re.I)
_LITERS = re.compile(r"(?<![\d.,])(\d{1,2}(?:[.,]\d{1,2})?)\s*(?:l|liter|litre|ltr)\b", re.I)

MIN_BOTTLES, MAX_BOTTLES = 1, 48


def bottles_from_text(text: str | None) -> tuple[float | None, bool]:
    """Returnera (antal flaskor, uppskattat?) ur en titel eller beskrivning.

    Bag-in-box räknas om till 75 cl-flaskor och markeras som uppskattat.
    """
    if not text:
        return None, False
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    for pattern, convert in _PATTERNS:
        for match in pattern.finditer(text):
            n = convert(match)
            if MIN_BOTTLES <= n <= MAX_BOTTLES:
                return float(n), False
    if _BIB.search(text):
        m = _LITERS.search(text)
        if m:
            liters = float(m.group(1).replace(",", "."))
            if 1 <= liters <= 20:
                return round(liters / 0.75, 2), True
    return None, False


def guess_bottles(title: str | None, *descriptions: str | None) -> tuple[float | None, bool]:
    """Titeln först, sedan beskrivningarna (där ensamma flaskor ignoreras)."""
    n, approx = bottles_from_text(title)
    if n is not None:
        return n, approx
    for desc in descriptions:
        n, approx = bottles_from_text(desc)
        if n is not None and n >= 2:
            return n, approx
    return None, False


# ---------------------------------------------------------------------------
# HTML: länkar, meta-taggar och JSON-LD
# ---------------------------------------------------------------------------


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.meta: dict[str, str] = {}
        self.itemprops: dict[str, str] = {}
        self.jsonld: list[str] = []
        self.title = ""
        self._in_jsonld = False
        self._in_title = False
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "a" and a.get("href"):
            self.links.append(a["href"])
        elif tag == "meta":
            key = (a.get("property") or a.get("name") or a.get("itemprop") or "").lower()
            if key and "content" in a:
                self.meta.setdefault(key, a["content"])
        elif tag == "script" and "ld+json" in a.get("type", "").lower():
            self._in_jsonld = True
            self._buf = []
        elif tag == "title":
            self._in_title = True
        if a.get("itemprop") in ("price", "name", "pricecurrency") and tag != "meta":
            value = a.get("content")
            if value:
                self.itemprops.setdefault(a["itemprop"].lower(), value)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._in_jsonld:
            self.jsonld.append("".join(self._buf))
            self._in_jsonld = False
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_jsonld:
            self._buf.append(data)
        elif self._in_title:
            self.title += data


def parse_page(page_html: str) -> _PageParser:
    parser = _PageParser()
    try:
        parser.feed(page_html)
        parser.close()
    except Exception:  # trasig HTML ska inte stoppa jämförelsen
        pass
    return parser


def extract_links(page_html: str, base_url: str) -> list[str]:
    """Alla unika absoluta länkar (utan #fragment) på sidan, i ordning."""
    seen: dict[str, None] = {}
    for href in parse_page(page_html).links:
        if href.startswith(("mailto:", "tel:", "javascript:")):
            continue
        url = urldefrag(urljoin(base_url, href.strip()))[0]
        seen.setdefault(url, None)
    return list(seen)


def _load_json(raw: str) -> Any:
    raw = raw.strip().rstrip(";")
    try:
        return json.loads(raw, strict=False)
    except ValueError:
        # Vanligt fel: avslutande kommatecken
        try:
            return json.loads(re.sub(r",\s*([}\]])", r"\1", raw), strict=False)
        except ValueError:
            return None


def _walk(node: Any) -> Iterator[dict]:
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _types(node: dict) -> set[str]:
    t = node.get("@type", [])
    return {str(x).split("/")[-1].lower() for x in (t if isinstance(t, list) else [t])}


def _offer_price(offers: Any) -> tuple[float | None, str | None, bool | None]:
    """Lägsta pris, valuta och lagerstatus ur schema.org-offers."""
    best: tuple[float | None, str | None, bool | None] = (None, None, None)
    for node in _walk(offers):
        types = _types(node)
        if node is not offers and not types & {"offer", "aggregateoffer"}:
            continue
        price = parse_price(node.get("lowPrice") if "aggregateoffer" in types else node.get("price"))
        if price is None and isinstance(node.get("priceSpecification"), dict):
            price = parse_price(node["priceSpecification"].get("price"))
        if price is None:
            price = parse_price(node.get("lowPrice"))
        if price is None or price <= 0:
            continue
        availability = str(node.get("availability", "")).lower()
        in_stock = None
        if availability:
            in_stock = "instock" in availability or "limitedavailability" in availability
        if best[0] is None or price < best[0]:
            best = (price, node.get("priceCurrency"), in_stock)
    return best


def products_from_jsonld(page_html: str, base_url: str = "") -> list[dict]:
    """Alla schema.org Product i sidans JSON-LD, som enkla dicts."""
    parser = parse_page(page_html)
    results: list[dict] = []
    for raw in parser.jsonld:
        data = _load_json(raw)
        if data is None:
            continue
        for node in _walk(data):
            if not _types(node) & {"product", "productgroup"}:
                continue
            offers = node.get("offers")
            if offers is None and node.get("hasVariant"):
                offers = [v.get("offers") for v in node["hasVariant"] if isinstance(v, dict)]
            price, currency, in_stock = _offer_price(offers)
            if price is None:
                continue
            offer_dict = node["offers"] if isinstance(node.get("offers"), dict) else {}
            url = node.get("url") or offer_dict.get("url")
            results.append({
                "name": html.unescape(str(node.get("name", "")).strip()),
                "description": html.unescape(str(node.get("description", ""))),
                "price": price,
                "currency": currency,
                "in_stock": in_stock,
                "url": urljoin(base_url, url) if url else base_url,
            })
    return results


def product_from_meta(page_html: str, base_url: str) -> dict | None:
    """Reserv när JSON-LD saknas: Open Graph-/produkt-meta eller microdata."""
    parser = parse_page(page_html)
    meta, props = parser.meta, parser.itemprops
    price = parse_price(
        meta.get("product:price:amount") or meta.get("og:price:amount")
        or meta.get("price") or props.get("price")
    )
    if price is None or price <= 0:
        return None
    name = meta.get("og:title") or props.get("name") or parser.title
    return {
        "name": html.unescape(name.strip()),
        "description": html.unescape(meta.get("og:description") or meta.get("description") or ""),
        "price": price,
        "currency": meta.get("product:price:currency") or meta.get("og:price:currency")
        or meta.get("pricecurrency") or props.get("pricecurrency"),
        "in_stock": None,
        "url": base_url,
    }
