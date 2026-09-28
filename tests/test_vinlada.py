import json
import unittest
from urllib.parse import parse_qs, urlsplit

from vinlada import taste
from vinlada.http import FetchError
from vinlada.models import Offer
from vinlada.parse import bottles_from_text, extract_links, parse_price, products_from_jsonld
from vinlada.report import best_quote
from vinlada.shipping import rule_for_postcode, shipping_cost
from vinlada.sources import fetch_html, fetch_shop, fetch_shopify, fetch_vivino, fetch_woocommerce, known_offers


class FakeFetcher:
    """Svarar med förinspelade sidor; allt annat ger FetchError (som en blockerad sajt)."""

    def __init__(self, pages):
        self.pages = pages
        self.requested = []

    def get(self, url, accept="", headers=None):
        self.requested.append(url)
        for key, body in self.pages.items():
            if url == key or (key.endswith("*") and url.startswith(key[:-1])):
                return body if isinstance(body, str) else json.dumps(body)
        raise FetchError(f"HTTP 404 för {url}")

    def get_json(self, url, headers=None):
        return json.loads(self.get(url))


class ParsePriceTest(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(parse_price("1 499,00 kr"), 1499.0)
        self.assertEqual(parse_price("1.499,95"), 1499.95)
        self.assertEqual(parse_price("699.00"), 699.0)
        self.assertEqual(parse_price("879,95"), 879.95)
        self.assertEqual(parse_price("1.499"), 1499.0)
        self.assertEqual(parse_price("1,299.50"), 1299.5)
        self.assertEqual(parse_price(699), 699.0)
        self.assertIsNone(parse_price("pris saknas"))


class BottlesTest(unittest.TestCase):
    def test_counts(self):
        self.assertEqual(bottles_from_text("Månadens Vinlåda – 6 flaskor")[0], 6)
        self.assertEqual(bottles_from_text("Trippel natur blandad vinlåda 3x2 flaskor")[0], 6)
        self.assertEqual(bottles_from_text("Få 18 fl. vin men betal för 12 fl.")[0], 18)
        self.assertEqual(bottles_from_text("Gourmet Selection i 6-pack")[0], 6)
        self.assertEqual(bottles_from_text("Tolv flaskor rött")[0], 12)
        self.assertEqual(bottles_from_text("Rødvinskasse 12 flasker")[0], 12)
        self.assertEqual(bottles_from_text("Ripasso 2021 75 cl"), (None, False))

    def test_bag_in_box_is_approximate(self):
        n, approx = bottles_from_text("Primitivo bag-in-box 3 liter")
        self.assertAlmostEqual(n, 4.0)
        self.assertTrue(approx)


class ShippingTest(unittest.TestCase):
    def test_free_from(self):
        rule = {"fee": 189, "free_from": 695}
        self.assertEqual(shipping_cost(rule, 600, 6)[0], 189)
        self.assertEqual(shipping_cost(rule, 700, 6)[0], 0)

    def test_tiers(self):
        rule = {"free_from": 1000, "tiers": [{"under": 600, "fee": 59}, {"under": 1000, "fee": 29}]}
        self.assertEqual(shipping_cost(rule, 500, 6)[0], 59)
        self.assertEqual(shipping_cost(rule, 880, 6)[0], 29)
        self.assertEqual(shipping_cost(rule, 1000, 6)[0], 0)

    def test_per_box_and_bottles(self):
        self.assertEqual(shipping_cost({"fee": 79, "per_box": True}, 1200, 12, boxes=2)[0], 158)
        self.assertEqual(shipping_cost({"fee": 149, "free_from_bottles": 12}, 900, 12)[0], 0)

    def test_unknown_fee(self):
        self.assertIsNone(shipping_cost({"fee": None, "free_from": 999}, 799, 6)[0])

    def test_zones(self):
        rule = {"fee": None, "zones": [{"postcode_prefixes": ["20", "21"], "fee": 49}]}
        self.assertEqual(rule_for_postcode(rule, "211 20")["fee"], 49)
        self.assertIsNone(rule_for_postcode(rule, "25221")["fee"])

    def test_two_boxes_can_win(self):
        offer = Offer("Nordiska Vin", "Toscana 6 flaskor", "u", 799, 6)
        quote = best_quote(offer, {"fee": 99, "free_from": 999}, "25221", max_boxes=2)
        self.assertEqual(quote.boxes, 2)
        self.assertAlmostEqual(quote.per_bottle, 1598 / 12)


PRODUCT_PAGE = """<html><head><title>Vinlåda Toscana</title>
<script type="application/ld+json">
{"@context": "https://schema.org", "@graph": [{"@type": "Product", "name": "Vinlåda Toscana – 6 flaskor",
 "description": "Sammetslena, fylliga röda viner med toner av vanilj och fatlagring.",
 "offers": {"@type": "Offer", "price": "799.00", "priceCurrency": "SEK",
 "availability": "https://schema.org/InStock"},}]}
</script></head><body></body></html>"""

LISTING_PAGE = """<html><body>
<a href="/vinlada-toscana">Toscana</a>
<a href="/varukorg">Varukorg</a>
<a href="https://annan-sajt.se/x">Extern</a>
<a href="/blog/nyhet">Blogg</a>
<a href="/vinlada-toscana#recensioner">Toscana igen</a>
</body></html>"""


class HtmlSourceTest(unittest.TestCase):
    def test_jsonld_product(self):
        products = products_from_jsonld(PRODUCT_PAGE, "https://x.se/p")
        self.assertEqual(len(products), 1)
        self.assertEqual(products[0]["price"], 799.0)
        self.assertTrue(products[0]["in_stock"])

    def test_links_are_deduplicated(self):
        links = extract_links(LISTING_PAGE, "https://butik.se/vinlador")
        self.assertEqual(links.count("https://butik.se/vinlada-toscana"), 1)

    def test_fetch_html(self):
        shop = {"name": "Butik", "url": "https://butik.se", "listing_urls": ["https://butik.se/vinlador"]}
        fetcher = FakeFetcher({
            "https://butik.se/vinlador": LISTING_PAGE,
            "https://butik.se/vinlada-toscana": PRODUCT_PAGE,
        })
        offers = fetch_html(shop, fetcher)
        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0].bottles, 6)
        self.assertEqual(offers[0].price, 799)
        self.assertNotIn("https://butik.se/varukorg", fetcher.requested)
        self.assertNotIn("https://annan-sajt.se/x", fetcher.requested)


class ShopifyTest(unittest.TestCase):
    PRODUCTS = {"products": [
        {"title": "XL Intro vinlåda", "handle": "xl-intro", "body_html": "<p>12 flaskor rött</p>",
         "variants": [{"id": 1, "title": "Default Title", "price": "1299.00", "available": True}]},
        {"title": "Korkskruv", "handle": "korkskruv", "body_html": "",
         "variants": [{"id": 2, "title": "Default Title", "price": "99.00", "available": True}]},
    ]}

    def test_sek_shop(self):
        shop = {"name": "Dittvin", "url": "https://dittvin.se", "collections": ["vinlador"]}
        fetcher = FakeFetcher({
            "https://dittvin.se/meta.json": {"currency": "SEK"},
            "https://dittvin.se/collections/vinlador/products.json?limit=250&page=1": self.PRODUCTS,
            "https://dittvin.se/collections/vinlador/products.json?limit=250&page=2": {"products": []},
        })
        offers = fetch_shopify(shop, fetcher)
        self.assertEqual([o.title for o in offers], ["XL Intro vinlåda"])  # korkskruven filtreras bort
        self.assertEqual(offers[0].bottles, 12)
        self.assertEqual(offers[0].url, "https://dittvin.se/products/xl-intro")

    def test_foreign_currency_uses_product_js(self):
        shop = {"name": "Vinibutik", "url": "https://vinibutik.dk", "market_prefix": "/sv"}
        fetcher = FakeFetcher({
            "https://vinibutik.dk/meta.json": {"currency": "DKK"},
            "https://vinibutik.dk/sv/cart.js?currency=SEK": {"currency": "SEK"},
            "https://vinibutik.dk/products.json?limit=250&page=1": self.PRODUCTS,
            "https://vinibutik.dk/products.json?limit=250&page=2": {"products": []},
            "https://vinibutik.dk/sv/products/xl-intro.js?currency=SEK": {"variants": [{"id": 1, "price": 149900, "available": True}]},
        })
        offers = fetch_shopify(shop, fetcher)
        self.assertEqual(offers[0].price, 1499.0)
        self.assertNotIn("https://vinibutik.dk/sv/products/korkskruv.js?currency=SEK", fetcher.requested)

    def test_foreign_currency_without_sek_is_rejected(self):
        shop = {"name": "Vinibutik", "url": "https://vinibutik.dk"}
        fetcher = FakeFetcher({
            "https://vinibutik.dk/meta.json": {"currency": "DKK"},
            "https://vinibutik.dk/cart.js?currency=SEK": {"currency": "DKK"},
        })
        with self.assertRaises(FetchError):
            fetch_shopify(shop, fetcher)


class WooCommerceTest(unittest.TestCase):
    def test_store_api(self):
        shop = {"name": "Vinfolket", "url": "https://vinfolket.se"}
        fetcher = FakeFetcher({
            "https://vinfolket.se/wp-json/wc/store/v1/products?per_page=100&page=1": [
                {"name": "Månadens Vinlåda – 6 flaskor", "permalink": "https://vinfolket.se/p/1",
                 "is_in_stock": True, "short_description": "",
                 "prices": {"price": "79900", "currency_code": "SEK", "currency_minor_unit": 2}},
            ],
            "https://vinfolket.se/wp-json/wc/store/v1/products?per_page=100&page=2": [],
        })
        offers = fetch_woocommerce(shop, fetcher)
        self.assertEqual(offers[0].price, 799.0)
        self.assertEqual(offers[0].bottles, 6)


class VivinoTest(unittest.TestCase):
    def test_bundle_of_six(self):
        match = {"vintage": {"name": "Masi Campofiorin 2021", "year": 2021,
                             "statistics": {"ratings_average": 3.9},
                             "wine": {"id": 7, "type_id": 1, "winery": {"name": "Masi"},
                                      "style": {"name": "Italian Ripasso", "body": 4, "acidity": 3,
                                                "description": "Mjuka, runda viner med toner av körsbär och vanilj."}}},
                 "price": {"amount": 139.0, "currency": {"code": "SEK"}, "bottle_type": {"volume_ml": 750},
                           "url": "https://www.vivino.com/sv/masi-campofiorin/w/7"}}
        shop = {"name": "Vivino", "url": "https://www.vivino.com/sv/", "vivino": {"pages": 1}}

        class VivinoFetcher(FakeFetcher):
            def get_json(self, url, headers=None):
                q = parse_qs(urlsplit(url).query)
                assert q["country_code"] == ["SE"] and q["currency_code"] == ["SEK"]
                return {"explore_vintage": {"matches": [match]}}

        offers = fetch_vivino(shop, VivinoFetcher({}))
        self.assertEqual(offers[0].price, 139.0 * 6)
        self.assertEqual(offers[0].bottles, 6)
        self.assertEqual(offers[0].wine_type, 1)
        self.assertEqual(offers[0].structure, {"intensity": 4.0, "acidity": 3.0})


class TasteTest(unittest.TestCase):
    def offer(self, title, description="", **kw):
        return Offer("Butik", title, "u", 800, 6, description=description, **kw)

    def test_synonyms(self):
        o = self.offer("Italiensk vinlåda", "Mjuka tanniner och en rund, len avslutning.")
        s, hits = taste.score(o, ["sammetslena"])
        self.assertGreater(s, 0)
        self.assertIn("mjuka tanniner", hits)

    def test_all_terms_must_match(self):
        o = self.offer("Rioja Reserva 6 flaskor", "Fatlagrad på amerikansk ek med toner av vanilj och kola.")
        self.assertGreater(taste.score(o, ["fatlagring", "smörkola"])[0], 0)
        self.assertEqual(taste.score(o, ["fatlagrad", "söt"])[0], 0)

    def test_no_false_hit_on_ekologisk(self):
        o = self.offer("Ekologisk vinlåda", "Ekologiska viner från små odlare.")
        self.assertEqual(taste.score(o, ["fatlagrad"])[0], 0)

    def test_vivino_structure(self):
        o = self.offer("Vin ×6", "", structure={"tannin": 2.5, "acidity": 3.0, "intensity": 4.2})
        s, hits = taste.score(o, ["sammetslen"])
        self.assertGreater(s, 0)
        self.assertIn("smakprofil", hits)

    def test_color(self):
        self.assertTrue(taste.matches_color(self.offer("Rödvinslåda 6 flaskor"), "röd"))
        self.assertFalse(taste.matches_color(self.offer("Vita viner 6 flaskor"), "röd"))
        self.assertFalse(taste.matches_color(self.offer("Blandlåda"), "röd"))
        self.assertTrue(taste.matches_color(self.offer("X", wine_type=1), "rod"))


class FallbackTest(unittest.TestCase):
    def test_falls_back_to_html(self):
        shop = {"name": "Butik", "url": "https://butik.se", "platform": "shopify",
                "listing_urls": ["https://butik.se/vinlador"]}
        fetcher = FakeFetcher({
            "https://butik.se/vinlador": LISTING_PAGE,
            "https://butik.se/vinlada-toscana": PRODUCT_PAGE,
        })
        offers, errors = fetch_shop(shop, fetcher)
        self.assertEqual(len(offers), 1)
        self.assertTrue(errors[0].startswith("shopify"))

    def test_known_offers(self):
        shop = {"name": "N", "url": "https://n.se", "known_offers": [{"title": "Låda 9 flaskor", "price": 1149}]}
        offer = known_offers(shop)[0]
        self.assertEqual(offer.bottles, 9)
        self.assertEqual(offer.source, "känd")


class ConfigTest(unittest.TestCase):
    def test_config_is_valid(self):
        from pathlib import Path
        from vinlada.sources import FETCHERS
        config = json.loads((Path(__file__).parent.parent / "butiker.json").read_text(encoding="utf-8"))
        names = set()
        for shop in config["butiker"]:
            self.assertIn(shop.get("platform", "html"), {*FETCHERS, "manual"})
            self.assertNotIn(shop["name"], names)
            names.add(shop["name"])
            self.assertIn("shipping", shop)


if __name__ == "__main__":
    unittest.main()


class SystembolagetTest(unittest.TestCase):
    PRODUCTS = {"products": [
        {"productId": "1001", "productNumber": "7401", "productNameBold": "Langhe Nebbiolo",
         "productNameThin": "", "producerName": "Burzi", "price": 179.0, "volume": 750, "vintage": "2023",
         "assortmentText": "Beställningssortiment"},
        {"productId": "1002", "productNumber": "2400", "productNameBold": "Barbaresco",
         "productNameThin": "Produttori", "producerName": "Produttori del Barbaresco", "price": 399.0,
         "volume": 750, "vintage": "2020"},
    ]}

    def fetcher(self):
        from urllib.parse import quote
        return FakeFetcher({
            "https://api-extern.systembolaget.se/sb-api-ecommerce/v1/productsearch/search?*": self.PRODUCTS,
            "https://api-extern.systembolaget.se/sb-api-ecommerce/v1/sitesearch/site?*": {"siteSearchResults": [
                {"siteId": "1234", "alias": "Väla", "city": "HELSINGBORG", "isAgent": False},
                {"siteId": "9999", "alias": "Ombud", "city": "HELSINGBORG", "isAgent": True},
                {"siteId": "0102", "alias": "Fältöversten", "city": "STOCKHOLM", "isAgent": False},
            ]},
            "https://api-extern.systembolaget.se/sb-api-ecommerce/v1/stockbalance/store/1234/1001/": {"stock": 7},
        })

    def test_match_and_stock(self):
        from vinlada.systembolaget import Systembolaget
        sb = Systembolaget(self.fetcher(), city="Helsingborg", api_key="nyckel")
        offer = Offer("Fine Wine Service", "2024 Burzi Langhe Nebbiolo ×6", "u", 249 * 6, 6, kind="flaska")
        stats = sb.enrich([offer])
        self.assertEqual(stats["hittade"], 1)
        self.assertEqual(offer.systembolaget["price"], 179.0)
        self.assertEqual(offer.systembolaget["stock"], {"Väla": 7})
        self.assertTrue(offer.systembolaget["other_vintage"])
        self.assertIn("/produkt/vin/langhe-nebbiolo-7401/", offer.systembolaget["url"])

    def test_no_match_for_other_wine(self):
        from vinlada.systembolaget import Systembolaget
        sb = Systembolaget(self.fetcher(), api_key="nyckel")
        offer = Offer("X", "Bindella Fossolupaio Rosso di Montepulciano ×6", "u", 189 * 6, 6, kind="flaska")
        sb.enrich([offer])
        self.assertIsNone(offer.systembolaget)

    def test_boxes_are_not_searched(self):
        from vinlada.systembolaget import Systembolaget
        fetcher = self.fetcher()
        Systembolaget(fetcher, api_key="nyckel").enrich([Offer("X", "Toscana-låda 6 flaskor", "u", 799, 6)])
        self.assertEqual(fetcher.requested, [])

    def test_api_key_from_site_scripts(self):
        from vinlada.systembolaget import Systembolaget
        fetcher = FakeFetcher({
            "https://www.systembolaget.se/": '<script src="/_next/static/chunks/abc.js" defer=""></script>',
            "https://www.systembolaget.se/_next/static/chunks/abc.js": 'x={NEXT_PUBLIC_API_KEY_APIM:"hemlig"}',
        })
        self.assertEqual(Systembolaget(fetcher).api_key(), "hemlig")


class JsonFeedTest(unittest.TestCase):
    def test_rapido_like_feed(self):
        from vinlada.sources import fetch_jsonfeed
        feed = {"ProductsContainer": [{"Product": [
            {"id": "1", "name": "Smagekasse Italien – 6 flaskor", "link": "/smagekasse-italien",
             "priceDouble": 1295.0, "currency": "SEK"},
            {"id": "2", "name": "Vinglas 6 st", "link": "/glas", "priceDouble": 199.0, "currency": "SEK"},
        ]}]}
        shop = {"name": "Philipson Wine", "url": "https://philipsonwine.se",
                "feed_urls": ["https://philipsonwine.se/vinlaador?feed=true"]}
        offers = fetch_jsonfeed(shop, FakeFetcher({"https://philipsonwine.se/vinlaador?feed=true": feed}))
        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0].url, "https://philipsonwine.se/smagekasse-italien")
        self.assertEqual(offers[0].bottles, 6)


class BundleSinglesTest(unittest.TestCase):
    def test_shopify_singles_counted_as_six(self):
        shop = {"name": "Vinibutik", "url": "https://vinibutik.dk", "bundle_singles": 6,
                "title_regex": "l[åa]da|kasse"}
        fetcher = FakeFetcher({
            "https://vinibutik.dk/meta.json": {"currency": "SEK"},
            "https://vinibutik.dk/products.json?limit=250&page=1": {"products": [
                {"title": "Yllera Crianza 2020", "handle": "yllera", "body_html": "",
                 "variants": [{"id": 1, "title": "Default Title", "price": "129.00", "available": True}]},
                {"title": "Yllera Magnum 1,5 l", "handle": "magnum", "body_html": "",
                 "variants": [{"id": 2, "title": "Default Title", "price": "299.00", "available": True}]},
            ]},
            "https://vinibutik.dk/products.json?limit=250&page=2": {"products": []},
        })
        offers = fetch_shopify(shop, fetcher)
        self.assertEqual([o.title for o in offers], ["Yllera Crianza 2020 ×6"])
        self.assertEqual((offers[0].price, offers[0].bottles, offers[0].kind), (774.0, 6.0, "flaska"))


class PricePerBottleTest(unittest.TestCase):
    def test_winefinder_box_price_is_per_bottle(self):
        from vinlada.sources import _make_offer
        shop = {"name": "Winefinder", "url": "https://www.winefinder.se", "price_is_per_bottle": True}
        offer = _make_offer(shop, "Goa Buteljer från Göteborg! #6 – vinlåda", "u", 269.0, True,
                            "Lådan innehåller 6 flaskor.")
        self.assertEqual((offer.price, offer.bottles), (1614.0, 6.0))


class GenericNameTest(unittest.TestCase):
    def test_grape_and_region_alone_do_not_match(self):
        from vinlada.systembolaget import match_score
        p = {"producerName": "Sturm", "productNameBold": "Sturm", "productNameThin": "Pinot Grigio"}
        self.assertEqual(match_score("Pinot Grigio 2022 ×6", p), 0.0)
        self.assertGreater(match_score("Sturm Pinot Grigio 2022 ×6", p), 0.6)
