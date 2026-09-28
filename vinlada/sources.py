"""Hämtare per plattform: Shopify, WooCommerce, vanlig HTML (JSON-LD) och Vivino."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable
from urllib.parse import urlencode, urljoin, urlsplit

from .http import Fetcher, FetchError
from .models import Offer
from .parse import (clean_title, extract_links, guess_bottles, parse_price, product_from_html_price,
                    product_from_meta, products_from_jsonld)

log = logging.getLogger(__name__)

BOX_WORDS = re.compile(
    r"l[åa]da|l[åa]dan|lådor|\bbox|paket|kasse|pakke|smagekasse|provl[åa]d|provsmakningsl|"
    r"\bmix|blandl|sampler|\d+\s*-?\s*pac?k|kollektion|abonnemang|prenumeration|mixed case",
    re.I,
)

# Sådant som ser ut som lådor men inte är vin att dricka.
NOT_WINE = re.compile(
    r"trälåda|trälådor|tom\s+l[åa]da|ospecificerat|display\s*box|presentkort|gift\s*card|kartong|"
    r"emballage|presentförpackning|korkskruv|karaff|vinglas|vinkyl|dekanter|vinhylla",
    re.I,
)
ALCOHOL_FREE = re.compile(r"alkoholfri|alcohol[\s-]*free|\b0[,.]0\s*%|\bjuice\b|must\b|druvjuice", re.I)

DEFAULT_LINK_EXCLUDE = re.compile(
    r"/(cart|varukorg|kassa|checkout|login|logga-in|konto|account|my-account|sok|search|"
    r"blog|blogs|magasin|pages|sida|policies|kontakt|om-oss|villkor|faq|vanliga-fragor|"
    r"wishlist|onskelista|compare|nyhetsbrev|newsletter|presentkort)(/|$|\?)"
    r"|\.(jpe?g|png|gif|webp|svg|pdf|css|js)(\?|$)"
    r"|[?&](page|sort|order|filter|p|currencycode|activelanguageselection|loginaction|add-to-cart)=",
    re.I,
)


def looks_like_box(title: str, bottles: float | None) -> bool:
    return (bottles is not None and bottles >= 2) or bool(BOX_WORDS.search(title))


def _title_ok(shop: dict, title: str, *descriptions: str) -> bool:
    """Är produkten en vinlåda enligt butikens filter (eller standardheuristiken)?"""
    title_re = shop.get("title_regex")
    exclude_re = shop.get("exclude_regex")
    if NOT_WINE.search(title):
        return False
    if title_re and not re.search(title_re, title, re.I):
        return False
    if exclude_re and re.search(exclude_re, title, re.I):
        return False
    return bool(title_re) or looks_like_box(title, guess_bottles(title, *descriptions)[0])


def _make_offer(shop: dict, title: str, url: str, price: float, in_stock: bool | None,
                *descriptions: str, note: str = "") -> Offer | None:
    title = clean_title(title)
    if not _title_ok(shop, title, *descriptions):
        return _single_bottle_bundle(shop, title, url, price, in_stock, *descriptions)
    bottles, approx = guess_bottles(title, *descriptions)
    if bottles and price / bottles < MIN_PRICE_PER_BOTTLE:
        bottles, approx = None, False  # orimligt billigt – antalet är troligen feltolkat
    description = _plain(*descriptions)
    return Offer(shop=shop["name"], title=title.strip(), url=url, price=price,
                 bottles=bottles, bottles_approx=approx, in_stock=in_stock, note=note,
                 description=description,
                 alcohol_free=bool(ALCOHOL_FREE.search(title) or ALCOHOL_FREE.search(description[:400])))


# Svensk alkoholskatt + moms gör 75 cl vin under ca 55 kr orimligt (då är det små flaskor eller fel antal).
MIN_PRICE_PER_BOTTLE = 55


def _plain(*texts: str) -> str:
    text = " ".join(re.sub(r"<[^>]+>", " ", t or "") for t in texts)
    return re.sub(r"\s+", " ", text).strip()[:3000]


def _single_bottle_bundle(shop: dict, title: str, url: str, price: float, in_stock: bool | None,
                          *descriptions: str) -> Offer | None:
    """Butiker som bara säljer per flaska: räkna som N flaskor av samma vin (bundle_singles)."""
    n = shop.get("bundle_singles")
    if not n or NOT_WINE.search(title) or price < MIN_PRICE_PER_BOTTLE or price > 1500:
        return None
    if re.search(r"magnum|1[,.]5\s*l|150\s*cl|37[,.]5\s*cl|halvflaska", title, re.I):
        return None
    description = _plain(*descriptions)
    return Offer(shop=shop["name"], title=f"{title} ×{n}", url=url, price=price * n, bottles=float(n),
                 in_stock=in_stock, note=f"säljs per flaska; räknat som {n} st", kind="flaska",
                 description=description,
                 alcohol_free=bool(ALCOHOL_FREE.search(title)))


# ---------------------------------------------------------------------------
# Shopify
# ---------------------------------------------------------------------------


def _shopify_currency(shop: dict, fetcher: Fetcher, path: str) -> str | None:
    try:
        data = fetcher.get_json(shop["url"].rstrip("/") + path)
    except FetchError:
        return None
    if isinstance(data, dict):
        shop_data = data.get("shop") if isinstance(data.get("shop"), dict) else data
        if shop_data.get("currency"):
            return str(shop_data["currency"]).upper()
    return None


def fetch_shopify(shop: dict, fetcher: Fetcher) -> list[Offer]:
    """products.json ger priser i butikens grundvaluta, oberoende av besökarens land.

    Är grundvalutan inte SEK hämtas varje låda via product.js med ?currency=SEK,
    men bara om butiken bekräftar att den kan visa SEK.
    """
    base = shop["url"].rstrip("/")
    prefix = shop.get("path_prefix", "")
    market = shop.get("market_prefix", prefix)
    base_currency = _shopify_currency(shop, fetcher, "/meta.json")
    use_product_js = base_currency not in (None, "SEK")
    if use_product_js:
        presentment = _shopify_currency(shop, fetcher, f"{market}/cart.js?currency=SEK")
        if presentment != "SEK":
            raise FetchError(f"priser i {base_currency}, butiken visar inte SEK ({presentment})")
        log.info("%s: grundvaluta %s – hämtar SEK-priser per produkt", shop["name"], base_currency)
    offers: list[Offer] = []
    seen: set[str] = set()
    for handle in shop.get("collections") or [None]:
        path = f"/collections/{handle}/products.json" if handle else "/products.json"
        for page in range(1, shop.get("max_pages", 4) + 1):
            data = fetcher.get_json(f"{base}{prefix}{path}?limit=250&page={page}")
            products = data.get("products", []) if isinstance(data, dict) else []
            if not products:
                break
            for product in products:
                if product["handle"] in seen:
                    continue
                seen.add(product["handle"])
                if not shop.get("bundle_singles") and not _title_ok(
                        shop, product["title"], product.get("body_html") or ""):
                    continue
                offers.extend(_shopify_product_offers(shop, fetcher, product, use_product_js))
    return offers


def _shopify_product_offers(shop: dict, fetcher: Fetcher, product: dict,
                            use_product_js: bool) -> list[Offer]:
    base = shop["url"].rstrip("/")
    market = shop.get("market_prefix", shop.get("path_prefix", ""))
    url = f"{base}{market}/products/{product['handle']}"
    variants = product.get("variants", [])
    cents = False
    if use_product_js:
        try:
            data = fetcher.get_json(url + ".js?currency=SEK")
            variants = data.get("variants", []) if isinstance(data, dict) else []
            cents = True  # .js-endpointen anger priser i ören
        except FetchError as exc:
            log.info("%s: %s", shop["name"], exc)
            return []
    offers = []
    for variant in variants:
        try:
            price = float(variant["price"]) / (100 if cents else 1)
        except (KeyError, TypeError, ValueError):
            continue
        title = product["title"]
        vtitle = variant.get("title") or ""
        if len(variants) > 1 and vtitle.lower() not in ("default title", ""):
            title = f"{title} – {vtitle}"
        offer = _make_offer(shop, title, url + (f"?variant={variant['id']}" if len(variants) > 1 else ""),
                            price, variant.get("available"), product.get("body_html") or "")
        if offer:
            offers.append(offer)
    return offers


# ---------------------------------------------------------------------------
# WooCommerce (Store API)
# ---------------------------------------------------------------------------


def fetch_woocommerce(shop: dict, fetcher: Fetcher) -> list[Offer]:
    base = shop["url"].rstrip("/") + shop.get("path_prefix", "")
    api = f"{base}/wp-json/wc/store/v1"
    params: dict[str, Any] = {"per_page": 100}
    if shop.get("woo_lang"):
        params["lang"] = shop["woo_lang"]
    slugs = shop.get("woo_categories", [])
    if slugs:
        cats = fetcher.get_json(f"{api}/products/categories?per_page=100&{urlencode({k: v for k, v in params.items() if k == 'lang'})}")
        ids = [str(c["id"]) for c in cats if isinstance(c, dict) and c.get("slug") in slugs]
        if not ids:
            raise FetchError(f"hittade inga WooCommerce-kategorier {slugs}")
        params["category"] = ",".join(ids)
    offers: list[Offer] = []
    for page in range(1, shop.get("max_pages", 4) + 1):
        data = fetcher.get_json(f"{api}/products?{urlencode({**params, 'page': page})}")
        if not isinstance(data, list) or not data:
            break
        for product in data:
            prices = product.get("prices", {})
            if prices.get("currency_code", "SEK").upper() != "SEK":
                log.warning("%s: priser i %s, hoppar över", shop["name"], prices.get("currency_code"))
                return offers
            try:
                price = int(prices["price"]) / 10 ** int(prices.get("currency_minor_unit", 2))
            except (KeyError, TypeError, ValueError):
                continue
            offer = _make_offer(shop, product.get("name", ""), product.get("permalink", base), price,
                                product.get("is_in_stock"), product.get("short_description", ""),
                                product.get("description", ""))
            if offer:
                offers.append(offer)
    return offers


# ---------------------------------------------------------------------------
# Vanlig HTML med schema.org-data
# ---------------------------------------------------------------------------


def _candidate_links(shop: dict, page_html: str, page_url: str) -> list[str]:
    host = urlsplit(shop["url"]).netloc.removeprefix("www.")
    include = re.compile(shop["product_link_regex"], re.I) if shop.get("product_link_regex") else None
    skip = {u.rstrip("/") for u in shop.get("listing_urls", [])} | {shop["url"].rstrip("/")}
    links = []
    for link in extract_links(page_html, page_url):
        parts = urlsplit(link)
        if parts.scheme not in ("http", "https") or parts.netloc.removeprefix("www.") != host:
            continue
        if link.rstrip("/") in skip or parts.path in ("", "/"):
            continue
        if include is not None:
            if not include.search(link):
                continue
        elif DEFAULT_LINK_EXCLUDE.search(link):
            continue
        links.append(link)
    return links


def _offer_from_product(shop: dict, product: dict) -> Offer | None:
    currency = (product.get("currency") or "SEK").upper()
    if currency != "SEK":
        log.info("%s: %s har pris i %s, hoppar över", shop["name"], product["url"], currency)
        return None
    return _make_offer(shop, product["name"], product["url"], product["price"],
                       product.get("in_stock"), product.get("description", ""))


DUMP_DIR: str | None = None  # sätts av --dump-dir; sparar sidor utan hittat pris för felsökning
_dumped: dict[str, int] = {}


def _dump(shop: dict, url: str, page: str) -> None:
    if not DUMP_DIR or _dumped.get(shop["name"], 0) >= 3:
        return
    import os
    _dumped[shop["name"]] = _dumped.get(shop["name"], 0) + 1
    os.makedirs(DUMP_DIR, exist_ok=True)
    name = re.sub(r"[^a-z0-9]+", "-", shop["name"].lower())
    with open(os.path.join(DUMP_DIR, f"{name}-{_dumped[shop['name']]}.html"), "w", encoding="utf-8") as fh:
        fh.write(f"<!-- {url} -->\n" + page[:300_000])


def fetch_html(shop: dict, fetcher: Fetcher) -> list[Offer]:
    offers: dict[str, Offer] = {}
    candidates: list[str] = []
    listing_errors: list[FetchError] = []
    stats = {"listningar": 0, "länkar": 0, "sidor": 0, "med pris": 0, "fel valuta": 0}
    for listing in shop.get("listing_urls", []):
        try:
            page = fetcher.get(listing)
        except FetchError as exc:
            log.info("%s: %s", shop["name"], exc)
            listing_errors.append(exc)
            continue
        stats["listningar"] += 1
        _dump(shop, listing, page)
        # Vissa listningssidor bäddar in hela produktlistan som JSON-LD.
        for product in products_from_jsonld(page, listing):
            if product["url"].rstrip("/") != listing.rstrip("/"):
                offer = _offer_from_product(shop, product)
                if offer:
                    offers[offer.url] = offer
        candidates.extend(u for u in _candidate_links(shop, page, listing) if u not in candidates)

    if listing_errors and len(listing_errors) == len(shop.get("listing_urls", [])):
        raise listing_errors[0]

    # Länkar som ser ut som lådor först, så att menylänkar inte äter upp budgeten.
    candidates.sort(key=lambda u: 0 if BOX_WORDS.search(urlsplit(u).path) else 1)
    stats["länkar"] = len(candidates)
    log.info("%s: %d kandidatlänkar, t.ex. %s", shop["name"], len(candidates), candidates[:8])

    budget = shop.get("max_products", 40)
    for url in candidates:
        if url in offers:
            continue
        if budget <= 0:
            log.info("%s: nådde max_products, fler länkar hoppas över", shop["name"])
            break
        budget -= 1
        try:
            page = fetcher.get(url)
        except FetchError as exc:
            log.debug("%s: %s", shop["name"], exc)
            continue
        stats["sidor"] += 1
        products = products_from_jsonld(page, url)
        if not products:
            meta = product_from_meta(page, url)
            if meta is None and shop.get("html_price_fallback"):
                meta = product_from_html_price(page, url)
            products = [meta] if meta else []
        if not products:
            _dump(shop, url, page)
        for product in products[:1]:  # produktsidan beskriver en produkt
            product["url"] = url
            stats["med pris"] += 1
            if (product.get("currency") or "SEK").upper() != "SEK":
                stats["fel valuta"] += 1
            offer = _offer_from_product(shop, product)
            log.debug("%s: %s -> %s %s kr %s", shop["name"], url, product["name"][:60],
                      product["price"], "(låda)" if offer else "(filtrerad)")
            if offer:
                offers[offer.url] = offer
    if not offers:
        raise FetchError("inga vinlådor: " + ", ".join(f"{v} {k}" for k, v in stats.items()))
    return list(offers.values())


# ---------------------------------------------------------------------------
# JSON-flöden (t.ex. Dynamicweb/Rapido: listningssidan med ?feed=true)
# ---------------------------------------------------------------------------

_NAME_KEYS = ("name", "productName", "Name", "title", "Title", "ProductName")
_PRICE_KEYS = ("priceDouble", "PriceDouble", "priceWithVat", "PriceWithVat", "priceValue", "price", "Price")
_LINK_KEYS = ("link", "url", "Link", "Url", "productLink", "href")
_DESC_KEYS = ("shortDescription", "description", "ShortDescription", "Description", "teaser")


def _json_products(data: Any) -> list[dict]:
    """Alla objekt i ett godtyckligt JSON-svar som har både namn och pris."""
    found = []
    for node in _walk_json(data):
        name = next((node[k] for k in _NAME_KEYS if isinstance(node.get(k), str) and node[k].strip()), None)
        if not name:
            continue
        price = None
        for key in _PRICE_KEYS:
            value = node.get(key)
            if isinstance(value, dict):
                value = value.get("value") or value.get("price") or value.get("Price")
            price = parse_price(value)
            if price:
                break
        if not price:
            continue
        link = next((node[k] for k in _LINK_KEYS if isinstance(node.get(k), str)), "")
        desc = " ".join(str(node[k]) for k in _DESC_KEYS if isinstance(node.get(k), str))
        currency = next((str(node[k]) for k in ("currency", "Currency", "currencyCode", "CurrencyCode")
                         if isinstance(node.get(k), str)), None)
        found.append({"name": name, "price": price, "url": link, "description": desc, "currency": currency})
    return found


def _walk_json(node: Any):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk_json(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_json(item)


def fetch_jsonfeed(shop: dict, fetcher: Fetcher) -> list[Offer]:
    base = shop["url"].rstrip("/")
    offers: dict[str, Offer] = {}
    seen = 0
    for feed in shop.get("feed_urls", []):
        body = fetcher.get(feed, accept="application/json")
        try:
            data = json.loads(body)
        except ValueError:
            _dump(shop, feed, body)
            raise FetchError(f"flödet är inte JSON: {feed}")
        products = _json_products(data)
        seen += len(products)
        if not products:
            _dump(shop, feed, body)
        for product in products:
            currency = (product["currency"] or "SEK").upper()
            if currency not in ("SEK", "KR"):
                continue
            url = urljoin(base + "/", product["url"]) if product["url"] else feed
            offer = _make_offer(shop, product["name"], url, product["price"], None, product["description"])
            if offer:
                offers[offer.url + offer.title] = offer
    if not offers:
        raise FetchError(f"inga vinlådor i JSON-flödet ({seen} produkter med pris)")
    return list(offers.values())


# ---------------------------------------------------------------------------
# Vivino (säljer per flaska – räknas om till en "låda" om N flaskor)
# ---------------------------------------------------------------------------


def fetch_vivino(shop: dict, fetcher: Fetcher) -> list[Offer]:
    opts = shop.get("vivino", {})
    bundle = int(opts.get("bundle_size", 6))
    params: list[tuple[str, Any]] = [
        ("country_code", "SE"), ("currency_code", "SEK"), ("language", "sv"),
        ("min_rating", opts.get("min_rating", 1)),
        ("order_by", "price"), ("order", "asc"),
        ("price_range_min", opts.get("price_range_min", 0)),
        ("price_range_max", opts.get("price_range_max", 150)),
    ]
    params += [("wine_type_ids[]", t) for t in opts.get("wine_type_ids", [1, 2, 3, 4])]
    offers: list[Offer] = []
    for page in range(1, opts.get("pages", 2) + 1):
        url = "https://www.vivino.com/api/explore/explore?" + urlencode(params + [("page", page)])
        data = fetcher.get_json(url, headers=VIVINO_HEADERS)
        matches = (data or {}).get("explore_vintage", {}).get("matches", []) if isinstance(data, dict) else []
        if not matches:
            break
        external = 0
        for match in matches:
            offer = _vivino_offer(shop, match, bundle)
            if offer:
                offers.append(offer)
            else:
                external += 1
        log.info("%s: sida %d, %d träffar, %d från externa handlare bortfiltrerade",
                 shop["name"], page, len(matches), external)
    if not offers:
        raise FetchError("Vivino visade bara externa/utländska handlare (servern står utomlands)")
    return offers


# Be Vivino om svensk marknad även när anropet görs från en utländsk server.
VIVINO_HEADERS = {"Cookie": "country_code=SE; currency_code=SEK; language=sv; ship_to_country_code=SE",
                  "Accept-Language": "sv-SE,sv;q=0.9"}


def _vivino_offer(shop: dict, match: dict, bundle: int) -> Offer | None:
    price_info = match.get("price") or {}
    vintage = match.get("vintage") or {}
    wine = vintage.get("wine") or {}
    currency = ((price_info.get("currency") or {}).get("code") or "SEK").upper()
    amount = price_info.get("amount")
    if not amount or currency != "SEK":
        return None
    volume = (price_info.get("bottle_type") or {}).get("volume_ml") or 750
    if volume != 750:
        return None  # magnum/halvflaska – jämförs inte mot 75 cl
    winery = (wine.get("winery") or {}).get("name", "")
    name = vintage.get("name") or f"{winery} {wine.get('name', '')} {vintage.get('year', '')}"
    rating = (vintage.get("statistics") or {}).get("ratings_average")
    url = price_info.get("url") or ""
    if url.startswith("/"):
        url = "https://www.vivino.com" + url
    if not urlsplit(url).netloc.endswith("vivino.com"):
        return None  # extern handlare (ofta utländsk) som inte levererar till Sverige
    note = f"Vivino säljer per flaska; beräknat som {bundle} st"
    if rating:
        note += f", betyg {rating}"
    structure, description = _vivino_taste(vintage, wine)
    return Offer(shop=shop["name"], title=f"{name.strip()} ×{bundle}", url=url,
                 price=float(amount) * bundle, bottles=float(bundle), kind="flaska", in_stock=True, note=note,
                 description=description, structure=structure, wine_type=wine.get("type_id"))


def _vivino_taste(vintage: dict, wine: dict) -> tuple[dict[str, float] | None, str]:
    """Smakprofil (1–5) och beskrivande text ur Vivinos data, om de finns."""
    structure = dict(((wine.get("taste") or {}).get("structure") or {}))
    style = wine.get("style") or {}
    if not structure and style:
        structure = {"intensity": style.get("body"), "acidity": style.get("acidity")}
    structure = {k: float(v) for k, v in structure.items() if isinstance(v, (int, float))}
    texts = [style.get("name", ""), style.get("description", ""), style.get("blurb", "")]
    texts += [g.get("name", "") for g in style.get("grapes") or wine.get("grapes") or [] if isinstance(g, dict)]
    texts.append((wine.get("region") or {}).get("name", ""))
    return structure or None, " ".join(t for t in texts if t)


# ---------------------------------------------------------------------------

FETCHERS: dict[str, Callable[[dict, Fetcher], list[Offer]]] = {
    "shopify": fetch_shopify,
    "woocommerce": fetch_woocommerce,
    "html": fetch_html,
    "vivino": fetch_vivino,
    "jsonfeed": fetch_jsonfeed,
}


def fetch_shop(shop: dict, fetcher: Fetcher) -> tuple[list[Offer], list[str]]:
    """Hämta erbjudanden; faller tillbaka på HTML-läsning om plattforms-API:t inte svarar."""
    errors: list[str] = []
    order = [shop.get("platform", "html")]
    if order[0] != "html" and shop.get("listing_urls"):
        order.append("html")
    for platform in order:
        try:
            offers = FETCHERS[platform](shop, fetcher)
        except (FetchError, KeyError, TypeError, AttributeError, ValueError) as exc:
            errors.append(f"{platform}: {exc}")
            continue
        if offers:
            return offers, errors
        errors.append(f"{platform}: inga vinlådor hittades")
    return [], errors


def known_offers(shop: dict) -> list[Offer]:
    """Manuellt inlagda erbjudanden från butiker.json (används när sidan inte går att läsa)."""
    result = []
    for item in shop.get("known_offers", []):
        bottles = item.get("bottles")
        approx = False
        if bottles is None:
            bottles, approx = guess_bottles(item["title"])
        result.append(Offer(
            shop=shop["name"], title=item["title"], url=item.get("url", shop["url"]),
            price=float(item["price"]), bottles=bottles, bottles_approx=approx,
            source="känd", checked=item.get("checked"), note=item.get("note", ""),
        ))
    return result
