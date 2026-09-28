"""Smaksökning: matcha smakord (med synonymer) mot produkttexter och Vivinos smakprofil."""

from __future__ import annotations

import html
import re
import unicodedata

from .models import Offer

# Varje profil har synonymer (svenska, danska, engelska – butikerna skriver på alla tre)
# och, för Vivino, önskade intervall på smakskalan 1–5.
PROFILES: dict[str, dict] = {
    "sammetslen": {
        "beskrivning": "mjuk, rund munkänsla med mjuka/mogna tanniner",
        "ord": [
            "sammetslen", "sammetsmjuk", "sammetsaktig", "sammet", "silkeslen", "silkig",
            "len", "mjuk", "mjuka tanniner", "mogna tanniner", "rund", "rundad", "smidig",
            "fyllig och mjuk", "krämig", "fløjlsblød", "fløjl", "blød", "rund og blød",
            "silkeblød", "velvety", "velvet", "silky", "smooth", "soft tannins", "supple",
            "plush", "round", "appassimento", "ripasso", "amarone", "primitivo", "merlot", "zinfandel",
        ],
        "struktur": {"tannin": (1.0, 3.3), "acidity": (1.0, 3.5), "intensity": (3.0, 5.0)},
    },
    "fyllig": {
        "beskrivning": "kraftig kropp",
        "ord": ["fyllig", "kraftig", "koncentrerad", "mäktig", "fyldig", "kraftfuld",
                "full-bodied", "full bodied", "rich", "bold", "amarone", "appassimento"],
        "struktur": {"intensity": (3.8, 5.0)},
    },
    "fruktig": {
        "beskrivning": "tydlig, mogen frukt",
        "ord": ["fruktig", "bärig", "mogen frukt", "frugtig", "fruity", "jammy", "juicy", "saftig"],
        "struktur": {},
    },
    "frisk": {
        "beskrivning": "hög syra, pigg",
        "ord": ["frisk", "friskt", "fräsch", "syrlig", "mineralisk", "citrus", "crisp", "fresh", "zesty"],
        "struktur": {"acidity": (3.5, 5.0)},
    },
    "torr": {
        "beskrivning": "låg sötma",
        "ord": ["torr", "torrt", "tør", "dry", "brut"],
        "struktur": {"sweetness": (1.0, 1.8)},
    },
    "söt": {
        "beskrivning": "märkbar sötma",
        "ord": ["söt", "sött", "halvtorr", "sød", "sweet", "dessertvin", "off-dry", "moscato", "portvin"],
        "struktur": {"sweetness": (2.5, 5.0)},
    },
    "kryddig": {
        "beskrivning": "peppar, kryddor",
        "ord": ["kryddig", "peppar", "kryddor", "krydret", "spicy", "pepper", "syrah", "shiraz", "grenache"],
        "struktur": {},
    },
    "fatlagrad": {
        "beskrivning": "tydlig fatkaraktär: ek, vanilj, rostade toner",
        "ord": [
            "fatlagrad", "fatlagring", "fatlagrat", "lagrad på fat", "lagrat på fat", "ekfat",
            "ekfatslagrad", "fatkaraktär", "fatton", "amerikansk ek", "fransk ek", "ny ek",
            "vanilj", "rostad", "rostade toner", "fadlagret", "egefad", "egetræ", "oak",
            "oaked", "oaky", "barrel", "barrique", "toasted", "crianza", "reserva",
            "gran reserva", "rioja", "ribera del duero",
        ],
        "struktur": {},
    },
    "smörkola": {
        "beskrivning": "kola, karamell, smörighet – ofta från amerikansk ek",
        "ord": [
            "smörkola", "kola", "gräddkola", "karamell", "knäck", "smörig", "smör", "vanilj",
            "mocka", "choklad", "flødekaramel", "karamel", "smørkaramel", "butterscotch",
            "toffee", "caramel", "buttery", "vanilla", "mocha",
        ],
        "struktur": {},
    },
    "sträv": {
        "beskrivning": "mycket tanniner",
        "ord": ["sträv", "strävhet", "tanninrik", "stram", "tannic", "grippy", "nebbiolo", "barolo"],
        "struktur": {"tannin": (3.8, 5.0)},
    },
}


# Vardagliga varianter som pekar på en profil.
ALIASES = {
    "ek": "fatlagrad", "ekig": "fatlagrad", "fat": "fatlagrad", "fatlagring": "fatlagrad",
    "fatkaraktär": "fatlagrad", "oak": "fatlagrad",
    "kola": "smörkola", "karamell": "smörkola", "toffee": "smörkola", "butterscotch": "smörkola",
    "mjuk": "sammetslen", "len": "sammetslen", "silkeslen": "sammetslen", "velvety": "sammetslen",
}

# Färg/typ: ord i titel eller beskrivning, samt Vivinos wine type id.
COLORS: dict[str, dict] = {
    "röd": {"ord": ["rött", "röda", "rödvin", "rödviner", "röd ", "rødvin", "røde", "rød ",
                    "red wine", "red ", "rosso", "tinto", "rouge"], "vivino": 1},
    "vit": {"ord": ["vitt", "vita", "vitvin", "vitviner", "hvidvin", "hvide", "white",
                    "bianco", "blanco", "blanc"], "vivino": 2},
    "mousserande": {"ord": ["mousserande", "bubbel", "champagne", "cava", "prosecco", "crémant",
                            "sparkling", "mousserende"], "vivino": 3},
    "rosé": {"ord": ["rosé", "rose ", "rosévin", "rosado", "rosato"], "vivino": 4},
}
COLOR_ALIASES = {"rod": "röd", "rött": "röd", "röda": "röd", "red": "röd", "vitt": "vit",
                 "vita": "vit", "white": "vit", "rose": "rosé", "bubbel": "mousserande"}


def normalize_color(color: str) -> str:
    key = color.strip().lower()
    key = COLOR_ALIASES.get(key, key)
    if key not in COLORS:
        raise ValueError(f"okänd färg {color!r}, välj bland {', '.join(COLORS)}")
    return key


def matches_color(offer: Offer, color: str) -> bool:
    """Sant om erbjudandet (troligen) bara innehåller viner av färgen.

    Blandlådor räknas bara om titeln tydligt anger färgen.
    """
    color = normalize_color(color)
    if offer.wine_type is not None:
        return offer.wine_type == COLORS[color]["vivino"]
    title = _normalize(offer.title) + " "
    if any(w in title for w in COLORS[color]["ord"]):
        return True
    other = [w for c, v in COLORS.items() if c != color for w in v["ord"]]
    if any(w in title for w in other):
        return False
    text = _normalize(offer.description) + " "
    return any(w in text for w in COLORS[color]["ord"]) and not any(w in text for w in other)


def _normalize(text: str) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", text or "")).lower()
    return unicodedata.normalize("NFC", text)


def resolve(terms: list[str]) -> list[tuple[str, list[str], dict]]:
    """Gör om användarens smakord till (namn, synonymer, struktur).

    Kända profiler ger synonymlistan; okända ord används som de är.
    """
    resolved = []
    for term in terms:
        key = _normalize(term).strip()
        key = ALIASES.get(key, key)
        profile = PROFILES.get(key)
        if profile is None:
            # "sammetslena" / "sammetslent" -> sammetslen
            profile = next((p for name, p in PROFILES.items() if key.startswith(name)), None)
        if profile:
            resolved.append((key, profile["ord"], profile["struktur"]))
        else:
            resolved.append((key, [key], {}))
    return resolved


def _word_hit(text: str, word: str) -> bool:
    # Ordet får stå i början av ett ord ("mjuk" matchar "mjuka", inte "ljummjuk").
    return re.search(r"(?<![a-zåäöæø])" + re.escape(word), text) is not None


def score(offer: Offer, terms: list[str]) -> tuple[float, list[str]]:
    """Poäng > 0 om erbjudandet matchar alla angivna smaker."""
    text = _normalize(f"{offer.title} {offer.description}")
    total = 0.0
    matches: list[str] = []
    for name, words, structure in resolve(terms):
        hits = [w for w in words if _word_hit(text, w)]
        term_score = min(len(hits), 3)
        if offer.structure and structure:
            fits = [lo <= offer.structure[k] <= hi
                    for k, (lo, hi) in structure.items() if offer.structure.get(k) is not None]
            if fits and all(fits):
                term_score += 2
                hits.append("smakprofil")
        if term_score == 0:
            return 0.0, []
        total += term_score
        matches.extend(hits[:3])
    return total, matches


def apply(offers: list[Offer], terms: list[str]) -> list[Offer]:
    """Sätt smakpoäng och behåll bara erbjudanden som matchar."""
    kept = []
    for offer in offers:
        offer.taste_score, offer.taste_matches = score(offer, terms)
        if offer.taste_score > 0:
            kept.append(offer)
    return kept
