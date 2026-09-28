"""Jämför enskilda viner mot Systembolaget: pris och lagerstatus i butikerna i en viss stad.

Använder samma API som systembolaget.se. Nyckeln läses från miljövariabeln
SYSTEMBOLAGET_API_KEY (egen nyckel från api-portal.systembolaget.se) eller, om den
saknas, från webbplatsens skript på samma sätt som webbläsaren får den.
"""

from __future__ import annotations

import logging
import os
import re
import unicodedata
from urllib.parse import quote, urlencode

from .http import Fetcher, FetchError
from .models import Offer

log = logging.getLogger(__name__)

API = "https://api-extern.systembolaget.se/sb-api-ecommerce/v1"
SITE = "https://www.systembolaget.se"
_CHUNK = re.compile(r'src="(/_next/static/[^"]+\.js)"')
_KEY = re.compile(r'NEXT_PUBLIC_API_KEY_APIM:"([^"]+)"')

# Ord som inte hjälper till att känna igen ett vin.
_STOP = {
    "vin", "wine", "rött", "rod", "rodvin", "vitt", "red", "white", "rose", "rosé", "the", "de", "del",
    "di", "la", "le", "les", "el", "and", "och", "et", "x6", "75", "cl", "doc", "docg", "aoc", "igt",
    "chateau", "château", "domaine", "bodega", "bodegas", "cantina", "tenuta", "weingut",
}


# Druvor, områden och stilord: räcker inte ensamma för att säga att det är samma vin.
GENERIC = {
    "nebbiolo", "langhe", "barbera", "piemonte", "alba", "asti", "barolo", "barbaresco", "dolcetto",
    "pinot", "grigio", "gris", "noir", "blanc", "bianco", "rosso", "tinto", "blanco", "chardonnay",
    "sauvignon", "cabernet", "merlot", "syrah", "shiraz", "grenache", "garnacha", "tempranillo",
    "riesling", "malbec", "zinfandel", "primitivo", "sangiovese", "montepulciano", "chianti",
    "classico", "rioja", "crianza", "reserva", "gran", "ribera", "duero", "toscana", "sicilia",
    "puglia", "veneto", "valpolicella", "ripasso", "amarone", "appassimento", "prosecco", "cava",
    "champagne", "brut", "cremant", "bordeaux", "bourgogne", "cotes", "rhone", "provence", "douro",
    "vinho", "verde", "mosel", "pfalz", "rheingau", "napa", "valley", "superiore", "riserva",
    "negroamaro", "nero", "avola", "vermentino", "gruner", "veltliner", "chenin", "viognier",
    "moscato", "muscat", "rose", "rosato", "cuvee", "selection", "reserve", "old", "vines", "vieilles",
    "vignes", "vino", "cru", "grand", "premier", "estate", "family", "doc", "igp", "sur", "lie",
}


def _tokens(text: str) -> set[str]:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    words = re.findall(r"[a-z0-9]+", text)
    return {w for w in words if w not in _STOP and not re.fullmatch(r"(19|20)\d\d", w) and len(w) > 1}


def _vintage(text: str) -> str | None:
    m = re.search(r"\b(19[89]\d|20[0-4]\d)\b", text)
    return m.group(1) if m else None


def base_name(title: str) -> str:
    """"2021 Barolo Serralunga ×6" -> "Barolo Serralunga" (för sökning)."""
    title = re.sub(r"\s*×\s*\d+\s*$", "", title)
    title = re.sub(r"\b(19|20)\d\d\b", " ", title)
    title = re.sub(r"\b\d+[,.]?\d*\s*(cl|ml|l)\b", " ", title, flags=re.I)
    return re.sub(r"\s+", " ", title).strip(" -–,")


def product_name(p: dict) -> str:
    return " ".join(x for x in (p.get("productNameBold"), p.get("productNameThin")) if x)


def product_url(p: dict) -> str:
    slug = unicodedata.normalize("NFKD", product_name(p).lower())
    slug = "".join(c for c in slug if not unicodedata.combining(c))
    slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-")
    return f"{SITE}/produkt/vin/{slug}-{p.get('productNumber', '')}/"


def match_score(offer_title: str, p: dict) -> float:
    """Andel av vinets namnord som återfinns hos Systembolagets produkt (0–1)."""
    wanted = _tokens(base_name(offer_title))
    have = _tokens(f"{p.get('producerName', '')} {product_name(p)}")
    if not wanted or not have:
        return 0.0
    overlap = len(wanted & have)
    specific = wanted - GENERIC
    if not specific or not (specific & have):
        return 0.0  # bara druva/område gemensamt – troligen ett annat vin
    score = overlap / len(wanted)
    # Straffa när Systembolagets namn har mycket som vi inte sökte på (annat vin från samma producent).
    score *= min(1.0, (overlap + 1) / len(have))
    vintage = _vintage(offer_title)
    if vintage and p.get("vintage") and str(p["vintage"]) != vintage:
        score *= 0.9  # annan årgång: samma vin, men flagga det
    return round(score, 3)


class Systembolaget:
    def __init__(self, fetcher: Fetcher, city: str = "Helsingborg", api_key: str | None = None) -> None:
        self.fetcher = fetcher
        self.city = city
        self._key = api_key or os.environ.get("SYSTEMBOLAGET_API_KEY")
        self._stores: list[dict] | None = None
        self.samples: list[dict] = []  # några råa lagersvar, för felsökning

    # -- åtkomst --------------------------------------------------------------

    def api_key(self) -> str:
        if self._key:
            return self._key
        page = self.fetcher.get(SITE + "/")
        for path in _CHUNK.findall(page):
            try:
                script = self.fetcher.get(SITE + path, accept="*/*")
            except FetchError:
                continue
            m = _KEY.search(script)
            if m:
                self._key = m.group(1)
                return self._key
        raise FetchError("hittade ingen API-nyckel på systembolaget.se (sätt SYSTEMBOLAGET_API_KEY)")

    def _get(self, path: str, params: dict | None = None) -> object:
        url = f"{API}{path}" + (f"?{urlencode(params)}" if params else "")
        return self.fetcher.get_json(url, headers={
            "Ocp-Apim-Subscription-Key": self.api_key(),
            "Origin": SITE,
            "Referer": SITE + "/",
        })

    # -- butiker och sökning ----------------------------------------------------

    def stores(self) -> list[dict]:
        if self._stores is None:
            data = self._get("/sitesearch/site", {"q": self.city, "includePredictions": "true"})
            sites = data.get("siteSearchResults", data) if isinstance(data, dict) else data
            if isinstance(sites, dict):
                sites = sites.get("results") or sites.get("sites") or []
            self._stores = [
                s for s in (sites or [])
                if isinstance(s, dict) and str(s.get("city", "")).lower() == self.city.lower()
                and not s.get("isAgent")
            ]
            log.info("Systembolaget: %d butiker i %s: %s", len(self._stores), self.city,
                     [s.get("alias") or s.get("displayName") for s in self._stores])
        return self._stores

    def search(self, text: str, size: int = 10) -> list[dict]:
        data = self._get("/productsearch/search", {
            "textQuery": text, "size": size, "page": 1, "categoryLevel1": "Vin",
        })
        products = data.get("products", []) if isinstance(data, dict) else []
        return [p for p in products if isinstance(p, dict)]

    def stock(self, store_id: str, product_id: str) -> int | None:
        try:
            data = self._get(f"/stockbalance/store/{quote(store_id)}/{quote(product_id)}/")
        except FetchError as exc:
            self.samples.append({"butik": store_id, "vara": product_id, "fel": str(exc)})
            return None
        if len(self.samples) < 6:
            self.samples.append({"butik": store_id, "vara": product_id, "svar": data})
        if isinstance(data, dict) and data.get("stock") is not None:
            return int(data["stock"])
        return None

    # -- jämförelse -------------------------------------------------------------

    def find(self, offer: Offer, min_score: float = 0.6) -> dict | None:
        query = base_name(offer.title)
        if len(_tokens(query)) < 2:
            return None  # för kort namn för att matcha säkert
        best, best_score = None, 0.0
        for p in self.search(query):
            if p.get("volume") not in (750, 750.0) or p.get("isDiscontinued"):
                continue
            score = match_score(offer.title, p)
            if score > best_score:
                best, best_score = p, score
        if best is None or best_score < min_score:
            return None
        stock = {}
        for store in self.stores():
            n = self.stock(str(store.get("siteId")), str(best.get("productId")))
            if n is not None:
                stock[store.get("alias") or store.get("displayName") or store.get("siteId")] = n
        vintage = _vintage(offer.title)
        return {
            "name": f"{best.get('producerName', '')} – {product_name(best)}".strip(" –"),
            "number": best.get("productNumber"),
            "price": float(best["price"]),
            "vintage": best.get("vintage"),
            "other_vintage": bool(vintage and best.get("vintage") and str(best["vintage"]) != vintage),
            "assortment": best.get("assortmentText"),
            "url": product_url(best),
            "stock": stock,
            "in_stock_city": any(n > 0 for n in stock.values()),
            "score": best_score,
        }

    def enrich(self, offers: list[Offer], limit: int = 400) -> dict:
        """Sätt offer.systembolaget för enskilda viner. Returnerar statistik."""
        stats = {"sokta": 0, "hittade": 0, "i_lager": 0, "fel": 0}
        singles = [o for o in offers if o.kind == "flaska"][:limit]
        for offer in singles:
            stats["sokta"] += 1
            try:
                match = self.find(offer)
            except FetchError as exc:
                stats["fel"] += 1
                log.info("Systembolaget: %s", exc)
                if stats["fel"] >= 5 and stats["hittade"] == 0:
                    raise  # API:t svarar inte alls – ingen idé att fortsätta
                continue
            if match:
                offer.systembolaget = match
                stats["hittade"] += 1
                stats["i_lager"] += match["in_stock_city"]
                log.debug("Systembolaget: %s -> %s %s kr (%.2f)", offer.title, match["name"],
                          match["price"], match["score"])
        return stats
