"""Kommandorad: python -m vinlada [--postnummer 25221] [--smak sammetslen] ..."""

from __future__ import annotations

import argparse
import copy
import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import taste
from .http import Fetcher
from .models import Offer
from .html_report import write_html
from .report import best_quote, print_markdown, print_table, rows, sort_key, write_csv, write_json
from .sources import fetch_shop, known_offers

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "butiker.json"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python -m vinlada",
        description="Jämför vinlådor inklusive frakt till ditt postnummer.",
    )
    p.add_argument("--postnummer", default="25221", help="leveranspostnummer (standard 25221)")
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="butikslista (butiker.json)")
    p.add_argument("--butik", action="append", default=[], metavar="NAMN",
                   help="bara dessa butiker (del av namnet räcker, kan upprepas)")
    p.add_argument("--smak", action="append", default=[], metavar="ORD",
                   help="smak som måste matcha, t.ex. sammetslen, fatlagrad, smörkola (kan upprepas)")
    p.add_argument("--farg", metavar="FÄRG", help="röd, vit, rosé eller mousserande")
    p.add_argument("--lista-smaker", action="store_true", help="visa kända smakprofiler och avsluta")
    p.add_argument("--max-pris", type=float, help="högsta totalpris inkl. frakt")
    p.add_argument("--max-per-flaska", type=float, help="högsta pris per flaska inkl. frakt")
    p.add_argument("--stad", default="Helsingborg", help="stad för Systembolagets lagerstatus")
    p.add_argument("--utan-systembolaget", action="store_true", help="hoppa över jämförelsen med Systembolaget")
    p.add_argument("--sb-max", type=int, default=1000, help="max antal viner att söka upp på Systembolaget")
    p.add_argument("--alkoholfritt", action="store_true", help="ta med alkoholfria lådor")
    p.add_argument("--min-flaskor", type=float, default=0, help="minsta antal flaskor per låda")
    p.add_argument("--antal-lador", type=int, default=2,
                   help="pröva att köpa upp till N lådor för att nå fri frakt (standard 2)")
    p.add_argument("--visa-okanda", action="store_true",
                   help="visa även lådor där flaskantal eller frakt är okänt")
    p.add_argument("--offline", action="store_true",
                   help="hämta inget, använd bara kända erbjudanden i butiker.json")
    p.add_argument("--utan-kanda", action="store_true", help="hoppa över kända erbjudanden")
    p.add_argument("--topp", type=int, default=30, help="antal rader att visa (0 = alla)")
    p.add_argument("--format", choices=["tabell", "markdown"], default="tabell")
    p.add_argument("--lankar", action="store_true", help="visa länk och kommentar under varje rad")
    p.add_argument("--csv", metavar="FIL", help="spara resultatet som CSV")
    p.add_argument("--json", metavar="FIL", help="spara resultatet som JSON")
    p.add_argument("--html", metavar="FIL", help="spara en klickbar HTML-sida med lådor och butiker")
    p.add_argument("--html-fragment", metavar="FIL", help="som --html men utan <html>/<head> (för inbäddning)")
    p.add_argument("--status", metavar="FIL", help="spara hämtstatus och fel per butik som JSON")
    p.add_argument("--ingen-cache", action="store_true", help="hämta allt på nytt")
    p.add_argument("--cache-timmar", type=float, default=6)
    p.add_argument("--ignorera-robots", action="store_true", help="strunta i robots.txt (eget ansvar)")
    p.add_argument("--dump-dir", metavar="MAPP", help=argparse.SUPPRESS)  # felsökning av hämtare
    p.add_argument("-v", "--verbose", action="count", default=0)
    return p.parse_args(argv)


def list_tastes() -> None:
    print("Smakprofiler (--smak NAMN). Okända ord söks som fritext i produkttexterna.\n")
    for name, profile in taste.PROFILES.items():
        print(f"  {name:<12} {profile['beskrivning']}")
        print(f"  {'':<12} söker bl.a.: {', '.join(profile['ord'][:10])} …")
    print("\nAlias:", ", ".join(f"{a}→{b}" for a, b in taste.ALIASES.items()))
    print("Färger (--farg):", ", ".join(taste.COLORS))


def load_shops(args: argparse.Namespace) -> list[dict]:
    config = json.loads(args.config.read_text(encoding="utf-8"))
    shops = [s for s in config["butiker"] if s.get("enabled", True)]
    if args.butik:
        wanted = [b.lower() for b in args.butik]
        shops = [s for s in shops if any(w in s["name"].lower() for w in wanted)]
    if args.farg:
        # Vivino kan filtrera på vintyp direkt i API:t.
        color_id = taste.COLORS[taste.normalize_color(args.farg)]["vivino"]
        shops = copy.deepcopy(shops)
        for shop in shops:
            if shop.get("platform") == "vivino":
                shop.setdefault("vivino", {})["wine_type_ids"] = [color_id]
    return shops


def make_fetcher(args: argparse.Namespace) -> Fetcher:
    return Fetcher(
        cache_dir=None if args.ingen_cache else Path(".cache"),
        ttl_hours=args.cache_timmar,
        respect_robots=not args.ignorera_robots,
    )


def collect(shops: list[dict], args: argparse.Namespace, fetcher: Fetcher
            ) -> tuple[list[tuple[Offer, dict]], list[str], dict[str, dict]]:
    offers: list[tuple[Offer, dict]] = []
    problems: list[str] = []
    statuses: dict[str, dict] = {}

    def run(shop: dict) -> tuple[dict, list[Offer], list[str]]:
        if args.offline or shop.get("platform") == "manual":
            return shop, [], []
        found, errors = fetch_shop(shop, fetcher)
        return shop, found, errors

    # En tråd per butik; Fetcher pausar mellan anrop till samma värd.
    with ThreadPoolExecutor(max_workers=6) as pool:
        for shop, found, errors in pool.map(run, shops):
            if shop.get("platform") == "manual":
                statuses[shop["name"]] = {"state": "bara länk", "count": 0}
            elif args.offline:
                statuses[shop["name"]] = {"state": "ej hämtad", "count": 0}
            else:
                status = f"{len(found)} lådor" if found else "inget hämtat"
                print(f"  {shop['name']:<18} {status}", file=sys.stderr)
                statuses[shop["name"]] = {"state": "live" if found else "kunde inte läsas",
                                          "count": len(found), "errors": errors}
                if not found:
                    problems.append(f"{shop['name']}: " + " | ".join(errors))
            offers += [(o, shop) for o in found]
            if not args.utan_kanda:
                live_urls = {o.url for o in found}
                offers += [(o, shop) for o in known_offers(shop) if o.url not in live_urls]
    return offers, problems, statuses


def compare_systembolaget(offers: list[Offer], args: argparse.Namespace, fetcher: Fetcher,
                          statuses: dict[str, dict], problems: list[str]) -> None:
    """Leta upp enskilda viner på Systembolaget och kolla lagret i butikerna i --stad."""
    from .http import FetchError
    from .systembolaget import Systembolaget

    singles = sum(1 for o in offers if o.kind == "flaska")
    if not singles:
        return
    print(f"Jämför {singles} viner med Systembolaget i {args.stad} …", file=sys.stderr)
    sb = Systembolaget(fetcher, city=args.stad)
    try:
        stats = sb.enrich(offers, limit=args.sb_max)
        from .systembolaget import store_name
        stores = [store_name(s) for s in sb.stores()]
    except FetchError as exc:
        problems.append(f"Systembolaget: {exc}")
        statuses["Systembolaget"] = {"state": "kunde inte läsas", "count": 0, "errors": [str(exc)]}
        return
    print(f"  Systembolaget      {stats['hittade']} av {stats['sokta']} hittade, "
          f"{stats['i_lager']} i lager i {args.stad}", file=sys.stderr)
    try:
        control = sb.control()
    except FetchError as exc:
        control = {"fel": str(exc)}
    print(f"  Systembolaget      kontroll: {control}", file=sys.stderr)
    statuses["Systembolaget"] = {"state": "live", "count": stats["hittade"], "stats": stats,
                                 "stores": stores, "city": args.stad, "kontroll": control,
                                 "lagersvar": sb.samples[:6]}


def write_status(path: str, statuses: dict[str, dict], pairs: list[tuple[Offer, dict]]) -> None:
    """Diagnos per butik: hur många lådor som hittades och vilka fel som uppstod."""
    report = {}
    for name, status in statuses.items():
        if name == "Systembolaget":
            found = [o for o, _ in pairs if o.systembolaget]
            report[name] = {**status, "exempel": [
                {"vin": o.title, "butik": o.shop, "sb": o.systembolaget} for o in found[:15]]}
            continue
        found = [o for o, _ in pairs if o.shop == name and o.source == "live"]
        report[name] = {
            **status,
            "utan_flaskantal": sum(1 for o in found if o.bottles is None),
            "slut_i_lager": sum(1 for o in found if o.in_stock is False),
            "fa_flaskor_exempel": [{"titel": o.title, "pris": o.price, "flaskor": o.bottles,
                                    "text": o.description[:500]}
                                   for o in found if o.bottles and o.bottles <= 3 and o.kind == "låda"][:8],
            "alkoholfria": sum(1 for o in found if o.alcohol_free),
            "utan_flaskantal_exempel": [{"titel": o.title, "url": o.url, "text": o.description[:400]}
                                        for o in found if o.bottles is None][:8],
            "exempel": [{"titel": o.title, "pris": o.price, "flaskor": o.bottles, "url": o.url,
                         "text": o.description[:250]} for o in found[:8]],
        }
    Path(path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=[logging.WARNING, logging.INFO, logging.DEBUG][min(args.verbose, 2)],
                        format="%(levelname)s %(name)s: %(message)s")
    if args.lista_smaker:
        list_tastes()
        return 0
    if args.farg:
        try:
            taste.normalize_color(args.farg)
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return 2

    if args.dump_dir:
        from . import sources
        sources.DUMP_DIR = args.dump_dir
    shops = load_shops(args)
    if not shops:
        print("Inga butiker matchar --butik.", file=sys.stderr)
        return 2
    if not args.offline:
        print(f"Hämtar från {len(shops)} butiker …", file=sys.stderr)
    fetcher = make_fetcher(args)
    pairs, problems, statuses = collect(shops, args, fetcher)
    if not args.offline and not args.utan_systembolaget:
        compare_systembolaget([o for o, _ in pairs], args, fetcher, statuses, problems)

    if not args.alkoholfritt:
        pairs = [(o, s) for o, s in pairs if not o.alcohol_free]
    if args.farg:
        pairs = [(o, s) for o, s in pairs if taste.matches_color(o, args.farg)]
    if args.smak:
        kept = {id(o) for o in taste.apply([o for o, _ in pairs], args.smak)}
        pairs = [(o, s) for o, s in pairs if id(o) in kept]

    quotes = [best_quote(o, s.get("shipping", {}), args.postnummer, args.antal_lador) for o, s in pairs]
    quotes = [q for q in quotes if q.offer.in_stock is not False]
    with_unknown = sorted(quotes, key=sort_key)  # HTML-sidan har egen växel för okända
    if not args.visa_okanda:
        quotes = [q for q in quotes if q.per_bottle is not None]
    if args.min_flaskor:
        quotes = [q for q in quotes if (q.offer.bottles or 0) >= args.min_flaskor]
    if args.max_pris is not None:
        quotes = [q for q in quotes if q.total is not None and q.total <= args.max_pris]
    if args.max_per_flaska is not None:
        quotes = [q for q in quotes if q.per_bottle is not None and q.per_bottle <= args.max_per_flaska]
    quotes.sort(key=lambda q: sort_key(q, by_taste=bool(args.smak)))

    shown = quotes[: args.topp] if args.topp else quotes
    data = rows(shown)
    print()
    title = f"Vinlådor till {args.postnummer}, sorterat på kr/flaska inkl. frakt"
    if args.smak or args.farg:
        title += " – " + ", ".join(filter(None, [args.farg, *args.smak]))
    print(title + "\n")
    if not data:
        print("Inga träffar. Prova --visa-okanda, färre filter eller --offline för kända erbjudanden.")
    elif args.format == "markdown":
        print_markdown(data, sys.stdout, bool(args.smak))
    else:
        print_table(data, sys.stdout, bool(args.smak), args.lankar)

    if len(shown) < len(quotes):
        print(f"\n… och {len(quotes) - len(shown)} till (använd --topp 0 för alla).")
    if any(q.offer.source == "känd" for q in shown):
        print("\nKälla 'känd' = manuellt inlagt pris från butikens sida; kontrollera att det gäller.")
    if problems:
        print("\nKunde inte läsa:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
    if args.csv:
        write_csv(rows(quotes), args.csv)
    if args.json:
        write_json(quotes, args.json)
    html_quotes = [q for q in with_unknown if q in quotes or q.per_bottle is None]
    if args.html:
        write_html(args.html, html_quotes, shops, statuses, args.postnummer, standalone=True)
        print(f"\nHTML-sida sparad: {args.html}", file=sys.stderr)
    if args.html_fragment:
        write_html(args.html_fragment, html_quotes, shops, statuses, args.postnummer, standalone=False)
    if args.status:
        write_status(args.status, statuses, pairs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
