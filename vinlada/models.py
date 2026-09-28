from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Offer:
    """En vinlåda (eller ett antal flaskor som säljs tillsammans) hos en butik."""

    shop: str
    title: str
    url: str
    price: float  # SEK för en låda, inkl. moms och alkoholskatt
    bottles: float | None = None  # antal 75 cl-flaskor (eller flaskekvivalenter)
    bottles_approx: bool = False  # True om antalet är uppskattat (t.ex. bag-in-box)
    in_stock: bool | None = None
    source: str = "live"  # "live" = hämtad nu, "känd" = manuellt inlagd från butikens sida
    checked: str | None = None  # datum för manuellt inlagda erbjudanden
    note: str = ""
    description: str = ""  # produkttext, används för smaksökning
    structure: dict[str, float] | None = None  # Vivinos smakprofil 1–5 (tannin, acidity, ...)
    alcohol_free: bool = False
    kind: str = "låda"  # "låda" eller "flaska" (enskilt vin räknat som N st)
    systembolaget: dict | None = None  # matchande vara på Systembolaget, se systembolaget.py
    wine_type: int | None = None  # Vivinos typ: 1 röd, 2 vit, 3 mousserande, 4 rosé
    taste_score: float = 0.0
    taste_matches: list[str] = field(default_factory=list)


@dataclass
class Quote:
    """Totalkostnad för ett visst antal lådor av ett erbjudande."""

    offer: Offer
    boxes: int
    subtotal: float
    shipping: float | None  # None = okänd fraktkostnad
    shipping_note: str = ""
    extras: list[str] = field(default_factory=list)

    @property
    def total(self) -> float | None:
        if self.shipping is None:
            return None
        return self.subtotal + self.shipping

    @property
    def total_bottles(self) -> float | None:
        if self.offer.bottles is None:
            return None
        return self.offer.bottles * self.boxes

    @property
    def per_bottle(self) -> float | None:
        if self.total is None or not self.total_bottles:
            return None
        return self.total / self.total_bottles
