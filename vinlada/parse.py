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
    "en": 1, "ett": 1, "to": 2,
}

# Ord som betyder "flaskor vin" i lådbeskrivningar: "6 favoritviner", "6 festliga smakupplevelser".
_WINE_NOUN = (
    r"(?:[a-zåäöæøé]*viner|[a-zåäöæø]*vine\b|[a-zåäöæø]*vinsorter|smak(?:s)?upplevelser|"
    r"vinupplevelser|smagsoplevelser|wines\b|favoriter|årgångar(?:na)?|årgange)"
)
_NUM_WORD = r"\b(tre|fyra|fem|sex|sju|åtta|nio|tio|tolv|seks|otte)\b"
# Inga av de mellanliggande orden får vara "olika" (= antal sorter).
_NOT_KINDS = r"(?!(?:[a-zåäöæøé-]+\s+){0,3}?(?:olika|different|forskellige|skilda)\b)"
_NUM = r"(?<![\d.,])(\d{1,2})(?![\d.,]\d)"

_PATTERNS: list[tuple[re.Pattern[str], Any]] = [
    # "3x2 flaskor" -> 6
    (re.compile(_NUM + r"\s*[x×]\s*(\d{1,2})\s*" + _BOTTLE_WORD, re.I),
     lambda m: int(m.group(1)) * int(m.group(2))),
    # "6 x 75 cl"
    (re.compile(_NUM + r"\s*[x×]\s*75\s*cl", re.I), lambda m: int(m.group(1))),
    # "6 flaskor", "12 st. flaskor", "6 fl."
    (re.compile(_NUM + r"\s*(?:st\.?|stk\.?)?\s*" + _BOTTLE_WORD, re.I),
     lambda m: int(m.group(1))),
    # "6-pack", "12 pak", "[6-pac]"
    (re.compile(_NUM + r"[\s-]*(?:pack|pak|pac)\b", re.I), lambda m: int(m.group(1))),
    # "sexflaskorslåda", "tolv flaskor"
    (re.compile(r"\b(" + "|".join(_NUMBER_WORDS) + r")[\s-]*(?:flask|flaske|flasker)", re.I),
     lambda m: _NUMBER_WORDS[m.group(1).lower()]),
    # "1 Grande Reserva och 5 Beyra Reserva" -> 6
    (re.compile(_NUM + r"\s+[^\d,;]{2,40}?\s+(?:och|and|og|&|\+)\s+(\d{1,2})\s+[A-Za-zÅÄÖåäöÉé]", re.I),
     lambda m: int(m.group(1)) + int(m.group(2))),
    # "3 x topprankade Champagner" (antal först i titeln)
    (re.compile(r"^\s*(\d{1,2})\s*[x×]\s+[A-Za-zÅÄÖåäö]", re.I), lambda m: int(m.group(1))),
    # "mix x 9" i URL:er
    (re.compile(r"\bmix\s*[x×]\s*(\d{1,2})\b", re.I), lambda m: int(m.group(1))),
    # "6 favoritviner", "12 noggrant utvalda vita viner", "6 av de bästa årgångarna"
    # ("3 olika viner" anger antal sorter, inte flaskor, och räknas inte här.)
    (re.compile(_NUM + r"\s+" + _NOT_KINDS + r"(?:[a-zåäöæøé-]+\s+){0,3}" + _WINE_NOUN, re.I),
     lambda m: int(m.group(1))),
    # "sex röda favoriter", "tolv utvalda viner"
    (re.compile(_NUM_WORD + r"\s+" + _NOT_KINDS + r"(?:[a-zåäöæøé-]+\s+){0,3}" + _WINE_NOUN, re.I),
     lambda m: _NUMBER_WORDS[m.group(1).lower()]),
    # "(6st)", "låda (6 st)"
    (re.compile(r"\((\d{1,2})\s*st\.?\)", re.I), lambda m: int(m.group(1))),
    # "trepack", "sexpack"
    (re.compile(r"\b(tre|fyra|sex|tolv)[\s-]?pack", re.I), lambda m: _NUMBER_WORDS[m.group(1).lower()]),
]

# "2x Clos Malverne Brut, 2x Florence, 2x Aaldering" – delposter som summeras.
_ITEM = re.compile(r"(?<![\d.,])(\d{1,2})\s*[x×]\s+(?=[A-ZÅÄÖ])")

_EACH = re.compile(_NUM + r"\s*(?:st\.?\s*)?" + _BOTTLE_WORD +
                   r"\s+(?:av\s+varje|av\s+vardera|var\s+av|per\s+sort|of\s+each|af\s+hver)", re.I)
# Uppräkning efter "av varje;" – "Poggio Antico, La Gerla, Cerbaia, Cortonesi & San Filippo"
_LIST_AFTER = re.compile(r"[;:]\s*([^.«»\n]{3,300})")
_DISTINCT = re.compile(
    r"(?<![\d.,])(\d{1,2}|" + "|".join(_NUMBER_WORDS) + r")\s+(?:olika\s+|forskellige\s+|different\s+)?"
    r"(?:viner|vinsorter|sorter|wines|vine)\b", re.I)

_BIB = re.compile(r"bag[\s-]*in[\s-]*box|\bbib\b|\bbox\b|vinbox|\bboxvin", re.I)
_LITERS = re.compile(r"(?<![\d.,])(\d{1,2}(?:[.,]\d{1,2})?)\s*(?:l|liter|litre|ltr)\b", re.I)

MIN_BOTTLES, MAX_BOTTLES = 1, 48


def _clean(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", text))


def _bag_in_box(text: str) -> tuple[float | None, bool]:
    if _BIB.search(text):
        m = _LITERS.search(text)
        if m:
            liters = float(m.group(1).replace(",", "."))
            if 1 <= liters <= 20:
                return round(liters / 0.75, 2), True
    return None, False


def bottles_from_text(text: str | None) -> tuple[float | None, bool]:
    """Returnera (antal flaskor, uppskattat?) ur en titel.

    Bag-in-box räknas om till 75 cl-flaskor och markeras som uppskattat.
    """
    if not text:
        return None, False
    text = _clean(text)
    for pattern, convert in _PATTERNS:
        for match in pattern.finditer(text):
            n = convert(match)
            if MIN_BOTTLES <= n <= MAX_BOTTLES:
                return float(n), False
    return _bag_in_box(text)


def _number(token: str) -> int:
    return int(token) if token.isdigit() else _NUMBER_WORDS[token.lower()]


def bottles_from_description(text: str | None) -> tuple[float | None, bool]:
    """Flaskantal ur en längre produkttext, där delposter ofta räknas upp.

    * "2 flaskor av varje" + "3 olika viner" -> 6
    * "2 flaskor Barolo, 2 flaskor Barbaresco, 2 flaskor Langhe" -> 6
    * "Lådan innehåller 6 flaskor: 3 röda och 3 vita" -> 6
    * bara "2 flaskor av varje" utan antal viner -> okänt
    """
    if not text:
        return None, False
    text = _clean(text)
    items = [int(m.group(1)) for m in _ITEM.finditer(text)]
    if len(items) >= 2 and 2 <= sum(items) <= MAX_BOTTLES:
        return float(sum(items)), False
    each = _EACH.search(text)
    if each:
        per = int(each.group(1))
        distinct = _DISTINCT.search(text)
        if distinct:
            n = per * _number(distinct.group(1))
            if 2 <= n <= MAX_BOTTLES:
                return float(n), False
        listed = _LIST_AFTER.search(text, each.end())
        if listed:
            parts = [p for p in re.split(r",|&|\boch\b|\band\b|\s(?=(?:19|20)\d\d\b)", listed.group(1)) if p.strip()]
            if 2 <= len(parts) <= 24:
                return float(per * len(parts)), False
        text = _EACH.sub(" ", text)  # "N av varje" är inte lådans totala antal
    found: dict[int, int] = {}  # position i texten -> antal (första mönstret vinner)
    for pattern, convert in _PATTERNS:
        for match in pattern.finditer(text):
            n = convert(match)
            if MIN_BOTTLES <= n <= MAX_BOTTLES:
                found.setdefault(match.start(), n)
    counts = [found[pos] for pos in sorted(found)]
    if counts:
        first = counts[0]
        if first >= 3:
            return float(first), False
        if len(counts) >= 3 and max(counts) <= 3:
            return float(sum(counts)), False
        return None, False
    return _bag_in_box(text)


def url_words(url: str) -> str:
    """"…/chardonnay-vita-favoriter-8-flaskor" -> "chardonnay vita favoriter 8 flaskor"."""
    path = re.sub(r"^https?://[^/]+", "", url or "").split("?")[0]
    last = [p for p in path.split("/") if p][-1:] or [""]
    return re.sub(r"[-_]+", " ", last[0])


def guess_bottles(title: str | None, *descriptions: str | None, url: str = "") -> tuple[float | None, bool]:
    """Titeln först, sedan URL:en, sedan beskrivningarna."""
    n, approx = bottles_from_text(title)
    if n is not None:
        return n, approx
    if url:
        n, approx = bottles_from_text(url_words(url))
        if n is not None and n >= 2:
            return n, approx
    for desc in descriptions:
        n, approx = bottles_from_description(desc)
        if n is not None and n >= 2:
            return n, approx
    return None, False


_TITLE_SUFFIX = re.compile(r"\s*(?:\||:\s*köp\b|–\s*köp\b).*$", re.I)


def clean_title(title: str) -> str:
    """Ta bort butiksnamn och säljfraser: "Köp X online | Butik" -> "X"."""
    title = html.unescape(title).strip()
    short = _TITLE_SUFFIX.sub("", title).strip()
    short = re.sub(r"^köp\s+", "", short, flags=re.I)
    short = re.sub(r"\s+online$", "", short, flags=re.I)
    return (short[:1].upper() + short[1:]) if short else title


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


_HTML_PRICE = re.compile(
    r'class="[^"]*\bprice\b[^"]*"[^>]*>\s*(?:<[^>]+>\s*)*([\d\s\xa0.,]+?)\s*(?:kr|SEK|:-)', re.I)
_H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.I | re.S)


def product_from_html_price(page_html: str, base_url: str) -> dict | None:
    """Sista utväg för sidor utan strukturerad data: första elementet med klassen "price".

    Används bara för butiker med html_price_fallback i butiker.json.
    """
    m = _HTML_PRICE.search(page_html)
    if not m:
        return None
    price = parse_price(m.group(1))
    if price is None or price <= 0:
        return None
    parser = parse_page(page_html)
    h1 = _H1.search(page_html)
    name = parser.meta.get("og:title") or (re.sub(r"<[^>]+>", " ", h1.group(1)) if h1 else parser.title)
    return {
        "name": html.unescape(re.sub(r"\s+", " ", name).strip()),
        "description": html.unescape(parser.meta.get("og:description") or parser.meta.get("description") or ""),
        "price": price,
        "currency": "SEK",
        "in_stock": None,
        "url": base_url,
    }
