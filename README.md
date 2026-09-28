# vinlada – jämför vinlådor inklusive frakt

Ett litet Python-skript som hämtar vinlådor från svenska och EU-baserade vinbutiker, lägger på frakten till ditt
postnummer (standard **252 21 Helsingborg**) och sorterar på **kronor per flaska inklusive frakt**. Du kan filtrera
på färg och smak, till exempel sammetslena röda viner med smörkola och tydlig fatlagring.

Skriptet kräver bara Python 3.10 eller senare och inga extra paket.

```bash
python -m vinlada                                       # alla butiker, tabell i terminalen
python -m vinlada --html rapport.html                   # klickbar sida med lådor och butiker
python -m vinlada --farg röd --smak sammetslen          # bara sammetslena röda
python -m vinlada --farg röd --smak fatlagrad --smak smörkola --max-per-flaska 160
python -m vinlada --offline                             # bara manuellt inlagda priser, inget hämtas
python -m vinlada --lista-smaker                        # visa smakprofilerna
```

## Butiker

Butikerna står i [`butiker.json`](butiker.json). Samtliga säljer till svenska privatpersoner med svensk alkoholskatt
och moms inräknad, antingen via distanshandel eller privatimport.

| Butik | Plattform | Fri frakt | Frakt annars |
|---|---|---|---|
| [Dittvin](https://dittvin.se/collections/vinlador) | Shopify | från 695 kr | 189 kr |
| [Vinoteket](https://vinoteket.se/attribut/vinlador) | HTML | från 799 kr | ca 99 kr |
| [Winefinder](https://www.winefinder.se/vinlador) | HTML | från 1 200 kr | 99 kr |
| [Supervin](https://www.supervin.se/vin/vinlador/) | HTML | från 1 000 kr | 59 kr under 600 kr, 29 kr under 1 000 kr |
| [Philipson Wine](https://philipsonwine.se/vinlaador/vinlaador-med-6-flaskor) | HTML | från 1 295 kr | ca 99 kr |
| [Nordiska Vin](https://nordiskavin.se/store/collections/blandlador) | Shopify/HTML | från 999 kr i tätort | okänd |
| [Vinfolket](https://vinfolket.se/vinpaket/) | WooCommerce/HTML | från 800 kr | okänd |
| [Tidblom Wines](https://tidblomwines.com/) | WooCommerce/HTML | – | 79 kr per låda |
| [IVINIO](https://www.ivinio.com/sv/categorie-produit/blandade-provsmakningslador/) | WooCommerce/HTML | – | okänd |
| [Wine Trade](https://winetrade.se/en/collections/vinlador) | Shopify | från 12 flaskor | 149 kr |
| [Vinibutik](https://vinibutik.dk/) | Shopify | – | 49 kr till Sthlm/Gbg/Malmö, annars okänd |
| [Gassås Wine](https://gassaswine.se/kategori/blandlador) | HTML | – | okänd |
| [Fine Wine Service](https://finewineservice.se/) | Shopify/HTML | från 2 000 kr | okänd |
| [Pompette](https://www.pompette.se/) | WooCommerce/HTML | – | okänd |
| [Vivino](https://www.vivino.com/sv/) | Vivinos API | från 1 000 kr (äldre uppgift) | ca 59 kr |

Fraktvillkoren kontrollerades 2026-09-28. "Okänd" betyder att avgiften under gränsen för fri frakt inte gick att
hitta. Sådana lådor visas bara med `--visa-okanda`. Fyll gärna i `fee` när du ser beloppet i kassan.

Vivino säljer per flaska. Skriptet räknar därför om Vivinos priser till en "låda" om 6 flaskor av samma vin
(`bundle_size` i `butiker.json`).

Systembolaget finns inte med, eftersom de saknar öppet API. Deras hemleverans kostar 120 kr för det första paketet
(högst 12 flaskor) och 80 kr per paket därefter. Att hämta i butik är gratis.

## Hur det fungerar

* **Shopify-butiker** läses via `/collections/<namn>/products.json`. Om butikens grundvaluta inte är SEK hämtas
  SEK-priset per produkt via `/products/<namn>.js`.
* **WooCommerce-butiker** läses via Store API (`/wp-json/wc/store/v1/products`).
* **Övriga butiker** läses genom att skriptet öppnar listningssidan, följer produktlänkarna och läser
  schema.org-data (JSON-LD) eller produkt-metataggar.
* Går plattforms-API:t inte att nå faller skriptet tillbaka på vanlig HTML-läsning.
* **Flaskantal** tolkas ur titel och beskrivning, till exempel "6 flaskor", "3x2 flaskor", "12-pack" eller
  "tolv flaskor". Bag-in-box räknas om till 75 cl-flaskor och markeras med ≈.
* **Frakt** räknas enligt butikens regler: fri frakt över ett belopp eller från ett antal flaskor, avgift per låda,
  prisnivåer och postnummerzoner. Skriptet provar även att köpa **två lådor**, eftersom det ofta ger fri frakt och
  lägre pris per flaska (styrs med `--antal-lador`).
* **Kända erbjudanden** (`known_offers`) är priser som lagts in för hand. De visas med källan "känd" och används när
  butikens sida inte går att läsa.
* Skriptet är artigt mot butikerna. Det följer robots.txt, pausar en sekund mellan anrop till samma butik och
  cachar svar i sex timmar i `.cache/`.

## Smaksökning

`--smak` kan upprepas, och en låda måste matcha alla angivna smaker. Varje smakprofil söker efter synonymer på
svenska, danska och engelska i produktens titel och beskrivning:

| Profil | Söker bland annat efter |
|---|---|
| `sammetslen` | sammetslen, silkeslen, mjuka/mogna tanniner, rund, velvety, smooth, appassimento, ripasso, merlot |
| `fatlagrad` | fatlagrad, ekfat, amerikansk/fransk ek, vanilj, rostade toner, barrique, oak, crianza, reserva, rioja |
| `smörkola` | smörkola, kola, karamell, smörig, vanilj, mocka, butterscotch, toffee, caramel |
| `fyllig`, `fruktig`, `frisk`, `torr`, `söt`, `kryddig`, `sträv` | se `python -m vinlada --lista-smaker` |

Vanliga böjningar och alias fungerar också: `sammetslena`, `fatlagring`, `ek`, `kola`, `toffee`. Okända ord söks
som fritext. För Vivino jämförs även vinets smakprofil (tanniner, syra, kropp på skalan 1–5). "Sammetslen" betyder
där låga tanniner och fyllig kropp.

`--farg` (röd, vit, rosé, mousserande) filtrerar på vinets färg. Blandlådor räknas bara om titeln tydligt anger
färgen.

HTML-sidan (`--html`) har samma filter som klickbara knappar. Varje låda och varje butik länkar direkt till butiken.

## Lägga till en butik

Lägg till ett objekt i `butiker.json`:

```json
{
  "name": "Min butik",
  "url": "https://minbutik.se",
  "platform": "html",
  "listing_urls": ["https://minbutik.se/vinlador"],
  "shipping": {"fee": 99, "free_from": 999}
}
```

Valfria fält:

* `collections` för Shopify
* `woo_categories` för WooCommerce
* `product_link_regex`
* `title_regex` och `exclude_regex`
* `max_products`
* `known_offers`
* `shipping.tiers`
* `shipping.per_box`
* `shipping.free_from_bottles`
* `shipping.zones`

## Tester

```bash
python -m unittest discover -s tests
```

Testerna använder inspelade exempelsvar för varje plattform och kräver ingen nätverksåtkomst.
