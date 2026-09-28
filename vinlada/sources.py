"""Hämtare per plattform: Shopify, WooCommerce, vanlig HTML (JSON-LD) och Vivino."""

from __future__ import annotations

import logging
import re
from typing import Any, Callable
from urllib.parse import urlencode, urlsplit

from .http import Fetcher, FetchError
from .models import Offer
from .parse import extract_links, guess_bottles, product_from_meta, products_from_jsonld

log = logging.getLogger(__name__)

BOX_WORDS = re.compile(
    r"l[åa]da|l[åa]dan|lådor|box|paket|kasse|pakke|smag|prov|mix|blandl|sampler|"
    r"\d+\s*-?\s*pack|kollektion|selection|abonnemang|prenumeration",
    re.I,
)

DEFAULT_LINK_EXCLUDE = re.compile(
    r"/(cart|varukorg|kassa|checkout|login|logga-in|konto|account|my-account|sok|search|"
    r"blog|blogs|magasin|pages|sida|policies|kontakt|om-oss|villkor|faq|vanliga-fragor|"
    r"wishlist|onskelista|compare|nyhetsbrev|newsletter|presentkort)(/|$|\?)"
    r"|\.(jpe?g|png|gif|webp|svg|pdf|css|js)(\?|$)"
    r"|[?&](page|sort|order|filter|p)=",
    re.I,
)


def looks_like_box(title: str, bottles: float | None) -> bool:
    return (bottles is not None and bottles >= 2) or bool(BOX_WORDS.search(title))


def _make_offer(shop: dict, title: str, url: str, price: float, in_stock: bool | None,
                *descriptions: str, note: str = "") -> Offer | None:
    bottles, approx = guess_bottles(title, *descriptions)
    title_re = shop.get("title_regex")
    exclude_re = shop.get("exclude_regex")
    if title_re and not re.search(title_re, title, re.I):
        return None
    if exclude_re and re.search(exclude_re, title, re.I):
        return None
    if not title_re and not looks_like_box(title, bottles):
        return None
    description = " ".join(re.sub(r"<[^>]+>", " ", d or "") for d in descriptions)
    return Offer(shop=shop["name"], title=title.strip(), url=url, price=price,
                 bottles=bottles, bottles_approx=approx, in_stock=in_stock, note=note,
                 description=re.sub(r"\s+", " ", description).strip()[:3000])


# ---------------------------------------------------------------------------
# Shopify
# ---------------------------------------------------------------------------


def _shopify_currency(shop: dict, fetcher: Fetcher, prefix: str) -> str | None:
    base = shop["url"].rstrip("/")
    for path in (f"{prefix}/cart.js", "/meta.json"):
        try:
            data = fetcher.get_json(base + path)
        except FetchError:
            continue
        if isinstance(data, dict) and data.get("currency"):
            return str(data["currency"]).upper()
    return None


def fetch_shopify(shop: dict, fetcher: Fetcher) -> list[Offer]:
    base = shop["url"].rstrip("/")
    prefix = shop.get("path_prefix", "")
    market = shop.get("market_prefix", prefix)
    currency = _shopify_currency(shop, fetcher, market)
    use_product_js = bool(currency and currency != "SEK")
    if use_product_js:
        log.info("%s: grundvaluta %s – hämtar SEK-priser per produkt via %s",
                 shop["name"], currency, market or "/")
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
            data = fetcher.get_json(url + ".js")
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


def fetch_html(shop: dict, fetcher: Fetcher) -> list[Offer]:
    offers: dict[str, Offer] = {}
    candidates: list[str] = []
    listing_errors: list[FetchError] = []
    for listing in shop.get("listing_urls", []):
        try:
            page = fetcher.get(listing)
        except FetchError as exc:
            log.info("%s: %s", shop["name"], exc)
            listing_errors.append(exc)
            continue
        # Vissa listningssidor bäddar in hela produktlistan som JSON-LD.
        for product in products_from_jsonld(page, listing):
            if product["url"].rstrip("/") != listing.rstrip("/"):
                offer = _offer_from_product(shop, product)
                if offer:
                    offers[offer.url] = offer
        candidates.extend(u for u in _candidate_links(shop, page, listing) if u not in candidates)

    if listing_errors and len(listing_errors) == len(shop.get("listing_urls", [])):
        raise listing_errors[0]

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
        products = products_from_jsonld(page, url)
        if not products:
            meta = product_from_meta(page, url)
            products = [meta] if meta else []
        for product in products[:1]:  # produktsidan beskriver en produkt
            product["url"] = url
            offer = _offer_from_product(shop, product)
            if offer:
                offers[offer.url] = offer
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
        data = fetcher.get_json(url)
        matches = (data or {}).get("explore_vintage", {}).get("matches", []) if isinstance(data, dict) else []
        if not matches:
            break
        for match in matches:
            offer = _vivino_offer(shop, match, bundle)
            if offer:
                offers.append(offer)
    return offers


def _vivino_offer(shop: dict, match: dict, bundle: int) -> Offer | None:
    price_info = match.get("price") or {}
    vintage = match.get("vintage") or {}
    wine = vintage.get("wine") or {}
    currency = ((price_info.get("currency") or {}).get("code") or "SEK").upper()
    amount = price_info.get("amount")
    if not amount or currency != "SEK":
        return None
    volume = (price_info.get("bottle_type") or {}).get("volume_ml") or 750
    winery = (wine.get("winery") or {}).get("name", "")
    name = vintage.get("name") or f"{winery} {wine.get('name', '')} {vintage.get('year', '')}"
    rating = (vintage.get("statistics") or {}).get("ratings_average")
    url = price_info.get("url") or f"https://www.vivino.com/sv/w/{wine.get('id', '')}"
    if url.startswith("/"):
        url = "https://www.vivino.com" + url
    note = f"Vivino säljer per flaska; beräknat som {bundle} st"
    if rating:
        note += f", betyg {rating}"
    structure, description = _vivino_taste(vintage, wine)
    return Offer(shop=shop["name"], title=f"{name.strip()} ×{bundle}", url=url,
                 price=float(amount) * bundle, bottles=round(bundle * volume / 750, 2),
                 bottles_approx=volume != 750, in_stock=True, note=note,
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
