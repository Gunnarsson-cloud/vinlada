"""HTML-rapport: klickbar tabell över lådorna och en katalog över alla butiker."""

from __future__ import annotations

import html
import json
from datetime import date

from . import taste
from .models import Quote
from .shipping import rule_for_postcode


def _shipping_summary(rule: dict) -> tuple[str, str]:
    """(fri frakt-gräns, avgift) som korta texter."""
    if rule.get("included"):
        return "ingår", "0 kr"
    parts = []
    if rule.get("free_from") is not None:
        parts.append(f"från {rule['free_from']:,.0f} kr".replace(",", " "))
    if rule.get("free_from_bottles") is not None:
        parts.append(f"från {rule['free_from_bottles']} flaskor")
    free = " / ".join(parts) or "nej"
    if rule.get("tiers"):
        fee = ", ".join(f"{t['fee']} kr under {t['under']}" for t in rule["tiers"])
    elif rule.get("fee") is None:
        fee = "okänd"
    else:
        fee = ("ca " if rule.get("approx") else "") + f"{rule['fee']:g} kr" + (" per låda" if rule.get("per_box") else "")
    return free, fee


def _offer_tags(quote: Quote) -> tuple[list[str], str | None]:
    tags = [name for name in taste.PROFILES if taste.score(quote.offer, [name])[0] > 0]
    color = next((c for c in taste.COLORS if taste.matches_color(quote.offer, c)), None)
    return tags, color


def build_data(quotes: list[Quote], shops: list[dict], statuses: dict[str, dict], postcode: str) -> dict:
    offers = []
    for q in quotes:
        o = q.offer
        tags, color = _offer_tags(q)
        offers.append({
            "shop": o.shop, "title": o.title, "url": o.url, "boxes": q.boxes,
            "bottles": q.total_bottles, "approx": o.bottles_approx,
            "price": round(q.subtotal, 2), "shipping": q.shipping, "total": q.total,
            "perBottle": round(q.per_bottle, 1) if q.per_bottle is not None else None,
            "source": o.source, "checked": o.checked, "tags": tags, "color": color,
            "note": "; ".join(x for x in (q.shipping_note, o.note) if x),
            "kind": o.kind, "sb": o.systembolaget,
        })
    shop_rows = []
    for shop in shops:
        rule = rule_for_postcode(shop.get("shipping", {}), postcode)
        free, fee = _shipping_summary(rule)
        status = statuses.get(shop["name"], {})
        shop_rows.append({
            "name": shop["name"], "url": shop["url"], "free": free, "fee": fee,
            "note": rule.get("note", ""), "state": status.get("state", "ej hämtad"),
            "count": status.get("count", 0), "known": len(shop.get("known_offers", [])),
            "platform": shop.get("platform", "html"),
        })
    sb_status = statuses.get("Systembolaget", {})
    return {
        "postcode": postcode, "generated": date.today().isoformat(), "offers": offers, "shops": shop_rows,
        "sbStores": sb_status.get("stores", []), "sbState": sb_status.get("state"),
        "sbCity": sb_status.get("city", "Helsingborg"),
        "profiles": {k: v["beskrivning"] for k, v in taste.PROFILES.items()},
        "colors": list(taste.COLORS),
    }


def render(data: dict, standalone: bool = True) -> str:
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    body = TEMPLATE.replace("__DATA__", payload).replace("__POSTCODE__", html.escape(data["postcode"]))
    if not standalone:
        return body
    return ('<!doctype html>\n<html lang="sv">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
            '</head>\n<body>\n' + body + '\n</body>\n</html>\n')


def write_html(path: str, quotes: list[Quote], shops: list[dict], statuses: dict[str, dict],
               postcode: str, standalone: bool = True) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(render(build_data(quotes, shops, statuses, postcode), standalone))


TEMPLATE = r"""<title>Vinlådor till __POSTCODE__</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&family=Young+Serif&display=swap">
<style>
:root {
  --bg: #f4f3f6; --surface: #ffffff; --surface-2: #ecebf0; --ink: #221b26; --muted: #675e6d;
  --line: #dcd8e1; --accent: #7a1f3d; --accent-soft: #f3e4ea; --good: #2d6a4c; --good-soft: #e1efe7;
  --warn: #8a5a10; --warn-soft: #f6ecd9; --focus: #b0305a; --sb: #1f5f8b; --sb-soft: #e2edf5;
  --serif: "Young Serif", Georgia, "Times New Roman", serif;
  --sans: "IBM Plex Sans", system-ui, -apple-system, "Segoe UI", sans-serif;
  --mono: "IBM Plex Mono", ui-monospace, "SF Mono", Menlo, monospace;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --bg: #17131a; --surface: #211b25; --surface-2: #2a2330; --ink: #efe9f2; --muted: #a99fae;
    --line: #3a3141; --accent: #e57b9d; --accent-soft: #3a1f2a; --good: #74c79d; --good-soft: #1d3228;
    --warn: #e2ad5b; --warn-soft: #3a2c16; --focus: #f09ab6; --sb: #7fb8e0; --sb-soft: #1b2c3a;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --bg: #17131a; --surface: #211b25; --surface-2: #2a2330; --ink: #efe9f2; --muted: #a99fae;
  --line: #3a3141; --accent: #e57b9d; --accent-soft: #3a1f2a; --good: #74c79d; --good-soft: #1d3228;
  --warn: #e2ad5b; --warn-soft: #3a2c16; --focus: #f09ab6; --sb: #7fb8e0; --sb-soft: #1b2c3a;
}
* { box-sizing: border-box; }
body { background: var(--bg); color: var(--ink); font: 15px/1.5 var(--sans); margin: 0; }
.wrap { max-width: 1180px; margin: 0 auto; padding-inline: 20px; padding-block: 32px 56px; display: grid; grid-template-columns: minmax(0, 1fr); gap: 28px; }
a { color: var(--accent); text-underline-offset: 3px; }
a:focus-visible, button:focus-visible, input:focus-visible, select:focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; }
header { display: grid; gap: 8px; }
.eyebrow { font: 500 12px/1 var(--mono); letter-spacing: .08em; text-transform: uppercase; color: var(--muted); }
h1 { font: 400 clamp(28px, 4.2vw, 42px)/1.1 var(--serif); margin: 0; text-wrap: balance; }
h2 { font: 400 24px/1.2 var(--serif); margin: 0; text-wrap: balance; }
.lede { color: var(--muted); max-width: 68ch; margin: 0; }
.best { display: flex; flex-wrap: wrap; gap: 10px 24px; align-items: baseline; background: var(--surface); border: 1px solid var(--line); border-radius: 10px; padding: 16px 20px; }
.best .num { font: 500 30px/1 var(--mono); color: var(--accent); font-variant-numeric: tabular-nums; }
.best .what { display: grid; gap: 2px; min-width: 0; }
.best .what a { font-weight: 600; overflow-wrap: anywhere; }
.best .what span { color: var(--muted); font-size: 13px; }
.filters { display: flex; flex-wrap: wrap; gap: 12px 20px; align-items: end; }
.field { display: grid; gap: 6px; }
.field > span, .field legend { font: 500 11px/1 var(--mono); letter-spacing: .08em; text-transform: uppercase; color: var(--muted); padding: 0; }
fieldset.field { border: 0; margin: 0; padding: 0; min-width: 0; }
select, input[type="number"], input[type="search"] { font: inherit; color: var(--ink); background: var(--surface); border: 1px solid var(--line); border-radius: 6px; padding: 7px 10px; min-height: 36px; }
input[type="number"] { width: 110px; }
input[type="search"] { width: min(240px, 100%); }
.chips { display: flex; flex-wrap: wrap; gap: 6px; }
.chip { font: 500 13px/1 var(--sans); color: var(--ink); background: var(--surface); border: 1px solid var(--line); border-radius: 999px; padding: 8px 12px; cursor: pointer; }
.chip[aria-pressed="true"] { background: var(--accent); border-color: var(--accent); color: var(--bg); }
.toggle { display: flex; gap: 8px; align-items: center; min-height: 36px; color: var(--muted); font-size: 14px; }
.table-scroll { overflow-x: auto; background: var(--surface); border: 1px solid var(--line); border-radius: 10px; }
table { border-collapse: collapse; width: 100%; min-width: 760px; }
th, td { text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--line); vertical-align: top; }
tr:last-child td { border-bottom: 0; }
th { font: 500 11px/1.2 var(--mono); letter-spacing: .08em; text-transform: uppercase; color: var(--muted); background: var(--surface-2); position: sticky; top: 0; }
td.n, th.n { text-align: right; font-family: var(--mono); font-variant-numeric: tabular-nums; white-space: nowrap; }
td.per { font-weight: 500; color: var(--accent); }
.title a { font-weight: 600; }
.sub { display: block; color: var(--muted); font-size: 12.5px; margin-top: 2px; }
.tag { display: inline-block; font: 500 11px/1 var(--mono); padding: 4px 6px; border-radius: 4px; background: var(--surface-2); color: var(--muted); margin: 4px 4px 0 0; }
.tag.hit { background: var(--accent-soft); color: var(--accent); }
.pill { display: inline-block; font: 500 11px/1 var(--mono); padding: 4px 7px; border-radius: 999px; white-space: nowrap; }
.pill.free { background: var(--good-soft); color: var(--good); }
.pill.unknown { background: var(--warn-soft); color: var(--warn); }
.pill.known { background: var(--surface-2); color: var(--muted); }
.pill.live { background: var(--good-soft); color: var(--good); }
.empty { padding: 24px; color: var(--muted); }
.sb { display: flex; flex-wrap: wrap; gap: 4px 8px; align-items: baseline; margin-top: 6px; font-size: 13px; color: var(--muted); }
.sb .sb-label { font: 500 11px/1 var(--mono); letter-spacing: .06em; text-transform: uppercase; color: var(--sb); }
.pill.cheaper { background: var(--sb-soft); color: var(--sb); }
.sb-note { background: var(--surface); border: 1px solid var(--line); border-radius: 10px; padding: 14px 18px; display: grid; gap: 6px; max-width: 80ch; }
.sb-note h3 { font: 500 13px/1.2 var(--mono); letter-spacing: .06em; text-transform: uppercase; color: var(--sb); margin: 0; }
.sb-note p { margin: 0; color: var(--muted); }
.shops { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 12px; }
.shop { background: var(--surface); border: 1px solid var(--line); border-radius: 10px; padding: 14px 16px; display: grid; gap: 8px; align-content: start; }
.shop-head { display: flex; justify-content: space-between; gap: 8px; align-items: baseline; }
.shop-head a { font-weight: 600; font-size: 16px; }
.shop dl { display: grid; grid-template-columns: auto 1fr; gap: 2px 12px; margin: 0; font-size: 13.5px; }
.shop dt { color: var(--muted); }
.shop dd { margin: 0; font-family: var(--mono); font-size: 13px; }
.shop .note { color: var(--muted); font-size: 13px; margin: 0; }
footer { color: var(--muted); font-size: 13px; max-width: 80ch; display: grid; gap: 6px; }
footer p { margin: 0; }
code { font-family: var(--mono); font-size: .92em; background: var(--surface-2); padding: 1px 5px; border-radius: 4px; }
@media (max-width: 560px) { .wrap { padding-inline: 16px; } .best .num { font-size: 26px; } }
</style>

<div class="wrap">
  <header>
    <div class="eyebrow" id="meta"></div>
    <h1>Vinlådor till __POSTCODE__</h1>
    <p class="lede">Alla priser inklusive svensk alkoholskatt och moms. Listan är sorterad på kronor per flaska med frakt inräknad. När två lådor ger fri frakt och blir billigare per flaska visas priset för två lådor.</p>
  </header>

  <section class="best" id="best" aria-live="polite"></section>

  <section class="filters" aria-label="Filter">
    <fieldset class="field"><legend>Typ</legend><div class="chips" id="kinds"></div></fieldset>
    <fieldset class="field"><legend>Färg</legend><div class="chips" id="colors"></div></fieldset>
    <fieldset class="field"><legend>Smak (alla valda måste matcha)</legend><div class="chips" id="tastes"></div></fieldset>
    <label class="field"><span>Butik</span><select id="shop"><option value="">Alla butiker</option></select></label>
    <label class="field"><span>Max kr/flaska</span><input id="maxper" type="number" min="0" step="10" placeholder="t.ex. 150"></label>
    <label class="field"><span>Sök</span><input id="q" type="search" placeholder="Toscana, Rioja …"></label>
    <label class="toggle"><input id="sbonly" type="checkbox"> Bara viner som finns på Systembolaget</label>
    <label class="toggle"><input id="unknown" type="checkbox"> Visa lådor med okänd frakt eller okänt flaskantal</label>
  </section>

  <section class="sb-note" id="sbnote">
    <h3>Systembolaget</h3>
    <p id="sbtext"></p>
  </section>

  <div class="table-scroll">
    <table>
      <thead><tr>
        <th>Låda</th><th>Butik</th><th class="n">Flaskor</th><th class="n">Pris</th><th class="n">Frakt</th><th class="n">Totalt</th><th class="n">kr/flaska</th>
      </tr></thead>
      <tbody id="rows"></tbody>
    </table>
    <div class="empty" id="empty" hidden></div>
  </div>

  <section style="display:grid;gap:14px" aria-labelledby="shops-h">
    <h2 id="shops-h">Butikerna</h2>
    <p class="lede">Alla butiker nedan säljer till privatpersoner i Sverige med skatten betald. Klicka på namnet för att gå till butiken. Fraktvillkoren gäller postnummer __POSTCODE__.</p>
    <div class="shops" id="shops"></div>
  </section>

  <footer>
    <p>Källa <span class="pill live">live</span> betyder att priset hämtades när rapporten skapades. <span class="pill known">känt pris</span> är inlagt för hand från butikens sida. Kontrollera alltid totalen i kassan.</p>
    <p>Alternativ: Systembolagets hemleverans kostar 120 kr för första paketet om högst 12 flaskor och 80 kr per paket därefter. Att hämta i butik i Helsingborg är gratis.</p>
    <p>Skapad med <code>python -m vinlada --html rapport.html</code>.</p>
  </footer>
</div>

<script type="application/json" id="data">__DATA__</script>
<script>
(function () {
  var data = JSON.parse(document.getElementById("data").textContent);
  var state = { kind: "", color: "", tastes: [], shop: "", maxper: null, q: "", unknown: false, sbonly: false };
  var kr = function (v) { return v == null ? "?" : Math.round(v).toLocaleString("sv-SE") + " kr"; };
  var el = function (tag, attrs, children) {
    var e = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === "text") e.textContent = attrs[k]; else if (k === "cls") e.className = attrs[k]; else e.setAttribute(k, attrs[k]);
    });
    (children || []).forEach(function (c) { if (c) e.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return e;
  };
  var link = function (href, text) { return el("a", { href: href, target: "_blank", rel: "noopener", text: text }); };

  document.getElementById("meta").textContent =
    "Postnummer " + data.postcode + " · skapad " + data.generated + " · " + data.shops.length + " butiker";

  function chipGroup(id, items, isOn, onClick) {
    var box = document.getElementById(id);
    box.textContent = "";
    items.forEach(function (it) {
      var b = el("button", { type: "button", cls: "chip", "aria-pressed": String(isOn(it.value)), title: it.title || "", text: it.label });
      b.addEventListener("click", function () { onClick(it.value); render(); });
      box.appendChild(b);
    });
  }

  var shopSelect = document.getElementById("shop");
  Array.from(new Set(data.offers.map(function (o) { return o.shop; }))).sort().forEach(function (s) {
    shopSelect.appendChild(el("option", { value: s, text: s }));
  });
  shopSelect.addEventListener("change", function () { state.shop = shopSelect.value; render(); });
  document.getElementById("maxper").addEventListener("input", function (e) { state.maxper = e.target.value ? Number(e.target.value) : null; render(); });
  document.getElementById("q").addEventListener("input", function (e) { state.q = e.target.value.trim().toLowerCase(); render(); });
  document.getElementById("unknown").addEventListener("change", function (e) { state.unknown = e.target.checked; render(); });
  document.getElementById("sbonly").addEventListener("change", function (e) { state.sbonly = e.target.checked; render(); });

  var sbMatches = data.offers.filter(function (o) { return o.sb; });
  var sbCheaper = sbMatches.filter(function (o) { return o.perBottle != null && o.sb.price < o.perBottle; });
  var singles = data.offers.filter(function (o) { return o.kind === "flaska"; }).length;
  document.getElementById("sbtext").textContent = data.sbState === "live"
    ? "Enskilda viner har sökts upp på Systembolaget: " + sbMatches.length + " av " + singles + " finns där, och " +
      sbCheaper.length + " är billigare på Systembolaget än hos nätbutiken inklusive frakt. Lagersaldot gäller butikerna i " +
      data.sbCity + (data.sbStores.length ? " (" + data.sbStores.join(", ") + ")" : "") +
      ". Varor som inte finns i lager kan beställas till butiken utan kostnad."
    : "Jämförelsen med Systembolaget kunde inte göras vid den här körningen.";
  if (!singles) document.getElementById("sbnote").hidden = true;

  function visible() {
    return data.offers.filter(function (o) {
      if (!state.unknown && o.perBottle == null) return false;
      if (state.kind && o.kind !== state.kind) return false;
      if (state.sbonly && !o.sb) return false;
      if (state.color && o.color !== state.color) return false;
      if (state.tastes.some(function (t) { return o.tags.indexOf(t) < 0; })) return false;
      if (state.shop && o.shop !== state.shop) return false;
      if (state.maxper != null && (o.perBottle == null || o.perBottle > state.maxper)) return false;
      if (state.q && (o.title + " " + o.shop).toLowerCase().indexOf(state.q) < 0) return false;
      return true;
    }).sort(function (a, b) {
      var ka = a.perBottle == null ? Infinity : a.perBottle, kb = b.perBottle == null ? Infinity : b.perBottle;
      return ka - kb || (a.total || a.price) - (b.total || b.price);
    });
  }

  function render() {
    chipGroup("kinds", [{ value: "", label: "Alla" }, { value: "låda", label: "Vinlådor" }, { value: "flaska", label: "Enstaka viner ×6" }],
      function (v) { return state.kind === v; }, function (v) { state.kind = v; });
    chipGroup("colors", [{ value: "", label: "Alla" }].concat(data.colors.map(function (c) { return { value: c, label: c }; })),
      function (v) { return state.color === v; }, function (v) { state.color = v; });
    chipGroup("tastes", Object.keys(data.profiles).map(function (p) { return { value: p, label: p, title: data.profiles[p] }; }),
      function (v) { return state.tastes.indexOf(v) >= 0; },
      function (v) { var i = state.tastes.indexOf(v); if (i >= 0) state.tastes.splice(i, 1); else state.tastes.push(v); });

    var list = visible();
    var tbody = document.getElementById("rows");
    tbody.textContent = "";
    list.forEach(function (o) {
      var title = el("td", { cls: "title" }, [link(o.url, (o.boxes > 1 ? o.boxes + " × " : "") + o.title)]);
      var sub = [];
      if (o.note) sub.push(o.note);
      if (sub.length) title.appendChild(el("span", { cls: "sub", text: sub.join(" · ") }));
      var tagLine = el("div", {});
      o.tags.forEach(function (t) { tagLine.appendChild(el("span", { cls: "tag" + (state.tastes.indexOf(t) >= 0 ? " hit" : ""), text: t })); });
      tagLine.appendChild(el("span", { cls: "pill " + (o.source === "live" ? "live" : "known"), text: o.source === "live" ? "live" : "känt pris " + (o.checked || "") }));
      title.appendChild(tagLine);
      if (o.sb) {
        var stock = Object.keys(o.sb.stock || {}).filter(function (k) { return o.sb.stock[k] > 0; })
          .map(function (k) { return k + " " + o.sb.stock[k] + " st"; });
        var cheaper = o.perBottle != null && o.sb.price < o.perBottle;
        var line = el("div", { cls: "sb" }, [
          el("span", { cls: "sb-label", text: "Systembolaget" }),
          link(o.sb.url, Math.round(o.sb.price) + " kr/fl" + (o.sb.otherVintage || o.sb.other_vintage ? " (annan årgång " + o.sb.vintage + ")" : "")),
          el("span", { text: stock.length ? "i lager: " + stock.join(", ")
            : (o.sb.in_store_assortment ? "slut i butikerna i " + data.sbCity + " just nu"
               : "finns inte i butikerna i " + data.sbCity + " – beställs gratis till valfri butik") })
        ]);
        if (cheaper) line.appendChild(el("span", { cls: "pill cheaper", text: "billigare på Systembolaget" }));
        title.appendChild(line);
      }
      var shop = data.shops.find(function (s) { return s.name === o.shop; });
      var ship = o.shipping == null ? el("span", { cls: "pill unknown", text: "okänd" })
        : o.shipping === 0 ? el("span", { cls: "pill free", text: "fri" }) : kr(o.shipping);
      tbody.appendChild(el("tr", {}, [
        title,
        el("td", {}, [shop ? link(shop.url, o.shop) : o.shop]),
        el("td", { cls: "n", text: o.bottles == null ? "?" : (o.approx ? "≈" : "") + o.bottles }),
        el("td", { cls: "n", text: kr(o.price) }),
        el("td", { cls: "n" }, [ship]),
        el("td", { cls: "n", text: kr(o.total) }),
        el("td", { cls: "n per", text: o.perBottle == null ? "?" : Math.round(o.perBottle) + " kr" })
      ]));
    });
    var empty = document.getElementById("empty");
    empty.hidden = list.length > 0;
    empty.textContent = data.offers.length
      ? "Inga lådor matchar filtren. Ta bort ett filter eller visa lådor med okänd frakt."
      : "Inga priser kunde hämtas. Gå direkt till butikerna nedan.";

    var best = document.getElementById("best");
    best.textContent = "";
    var top = list.find(function (o) { return o.perBottle != null; });
    if (top) {
      best.appendChild(el("div", { cls: "num", text: Math.round(top.perBottle) + " kr/fl" }));
      best.appendChild(el("div", { cls: "what" }, [
        link(top.url, (top.boxes > 1 ? top.boxes + " × " : "") + top.title),
        el("span", { text: top.shop + " · " + top.bottles + " flaskor · " + kr(top.total) + " totalt inkl. frakt" })
      ]));
    } else {
      best.appendChild(el("div", { cls: "what" }, [el("span", { text: "Inget jämförbart pris med de här filtren." })]));
    }
  }

  var shops = document.getElementById("shops");
  data.shops.forEach(function (s) {
    var status = s.count > 0 ? el("span", { cls: "pill live", text: s.count + " lådor" })
      : s.known > 0 ? el("span", { cls: "pill known", text: "kända priser" })
      : el("span", { cls: "pill unknown", text: s.state });
    var dl = el("dl", {}, [
      el("dt", { text: "Fri frakt" }), el("dd", { text: s.free }),
      el("dt", { text: "Frakt annars" }), el("dd", { text: s.fee })
    ]);
    var card = el("article", { cls: "shop" }, [
      el("div", { cls: "shop-head" }, [link(s.url, s.name), status]), dl
    ]);
    if (s.note) card.appendChild(el("p", { cls: "note", text: s.note }));
    shops.appendChild(card);
  });

  render();
})();
</script>
"""
