"""Telegram message bodies and price display helpers for WooliesBot."""

import datetime
import re

from constants import STORES, _PRICE_UNRELIABLE


def _nl():
    return "\n"


def _sp():
    return "\n\n"  # Section spacing for smartphone


# Weekly Essentials checklist (always buy, regardless of specials)
WEEKLY_ESSENTIALS = [
    "Capsicum, Onions, Spinach",
    "Eggs, Cream, Cheese",
    "Avocado, Zucchini",
]


def _store_badge(store_key):
    s = STORES.get(store_key, {})
    return f"{s.get('emoji', '')} {s.get('label', store_key)}"


def _price_display(item):
    """Format price string based on price_mode."""
    if item.get("price_unavailable"):
        return "❓ price unavailable"
    mode = item.get("price_mode", "each")
    eff = item.get("eff_price", item["price"])
    if mode == "kg":
        return f"${eff:.2f}/kg"
    if mode == "litre":
        return f"${item['price']:.2f} (${eff:.2f}/L)"
    return f"${item['price']:.2f}"


def _multi_store_line(item, compact=False):
    """Show prices from all stores for an item (excludes unreliable prices).
    compact: drop unit suffix when same for all (e.g. $11 vs $16 instead of $11/kg vs $16/kg)."""
    stores = item.get("all_stores", {})
    reliable = {sk: sd for sk, sd in stores.items() if sd["eff_price"] < _PRICE_UNRELIABLE}
    if len(reliable) <= 1:
        return ""
    parts = []
    mode = item.get("price_mode", "each")
    for sk, sd in sorted(reliable.items(), key=lambda x: x[1]["eff_price"]):
        se = STORES[sk]["emoji"]
        if mode == "kg":
            if compact:
                parts.append(f"{se}${sd['eff_price']:.2f}")
            else:
                parts.append(f"{se}${sd['eff_price']:.2f}/kg")
        elif mode == "litre":
            if compact:
                parts.append(f"{se}${sd['eff_price']:.2f}/L")
            else:
                parts.append(f"{se}${sd['price']:.2f} (${sd['eff_price']:.2f}/L)")
        else:
            parts.append(f"{se}${sd['price']:.2f}")
    return "  " + " vs ".join(parts)


def _item_store_prices(item):
    """Get (woolies_price_str, coles_price_str) from item's all_stores. Uses — when missing."""
    stores = item.get("all_stores", {})
    mode = item.get("price_mode", "each")
    woolies_sd = stores.get("woolworths")
    coles_sd = stores.get("coles")

    def fmt(sd):
        if not sd or sd.get("eff_price", 0) >= _PRICE_UNRELIABLE:
            return "—"
        if mode == "kg":
            return f"${sd['eff_price']:.2f}/kg"
        if mode == "litre":
            p = sd.get("price")
            ep = sd.get("eff_price")
            return f"${p:.2f}" if p else f"${ep:.2f}/L"
        return f"${sd['price']:.2f}"

    return (fmt(woolies_sd), fmt(coles_sd))


def _build_run_summary(raw_results, now_dt=None):
    """Build a concise Telegram summary for a completed scrape run."""
    now_dt = now_dt or datetime.datetime.now()
    specials_count = sum(
        1
        for s in raw_results
        if s.get("on_special")
        or (
            not s.get("price_unavailable")
            and (s.get("eff_price") or s.get("price", 0)) <= (s.get("target") or 0) > 0
        )
    )
    items_scraped = len([r for r in raw_results if not r.get("price_unavailable")])
    stale_count = sum(1 for r in raw_results if r.get("stale"))
    scrape_time = now_dt.strftime("%-I:%M %p")
    stale_note = f" · {stale_count} stale" if stale_count > 0 else ""
    return (
        f"🛒 *WooliesBot* updated at {scrape_time}\n"
        f"🏷️ *{specials_count}* deals · {items_scraped} items tracked{stale_note}\n"
        f"👉 [View Dashboard](https://KuschiKuschbert.github.io/wooliesbot/)"
    ).strip()


_ESSENTIAL_SUBCATS = frozenset(
    (
        "root_veg",
        "leafy_greens",
        "cooking_veg",
        "salad_veg",
        "fruit",
        "alliums",
        "herbs",
        "beef",
        "chicken",
        "pork_deli",
        "bakery",
        "breakfast",
    )
)
_DAIRY_ESSENTIAL_KEYWORDS = (
    "egg",
    "milk",
    "butter",
    "yoghurt",
    "yogurt",
    "cream cheese",
    "cheese block",
    "cheese slice",
)


def _build_weekly_shopping_reminder(raw_results, now_dt=None):
    """Build a richer Sunday-reminder Telegram message for planning the weekly shop.

    Sections:
      1. Headline deal / item counts.
      2. Cola battle — compare_group 'cola' winner + compact runners-up ($/L).
      3. Essentials on special — staples from clean grocery types / subcategories.
      4. Best deals — top 5 genuine promotions by dollar saving (any category).
    """
    now_dt = now_dt or datetime.datetime.now()
    dashboard_url = "https://KuschiKuschbert.github.io/wooliesbot/"
    store_emoji = {"woolworths": "\U0001f7e2", "coles": "\U0001f534"}

    active = [r for r in raw_results if not r.get("price_unavailable") and not r.get("stale")]

    specials_count = sum(
        1
        for r in active
        if r.get("on_special")
        or ((r.get("eff_price") or r.get("price", 0)) <= (r.get("target") or 0) > 0)
    )
    items_scraped = len(active)

    cola_active = [
        r
        for r in active
        if r.get("compare_group") == "cola" and (r.get("eff_price") or 0) < 5.0
    ]

    def _size_label(name):
        m = re.search(r"(\d+[Xx]\d+[Mm][Ll]|\d+\.?\d*[Ll]|\d+[Pp][Kk])", name)
        return m.group(1) if m else name

    def _best(items_list):
        return min(items_list, key=lambda r: r.get("eff_price") or 9999) if items_list else None

    _pepsi = [
        r
        for r in cola_active
        if "pepsi" in (r.get("name") or "").lower() and "max" not in (r.get("name") or "").lower()
    ]
    _coke_cls = [
        r
        for r in cola_active
        if ("coca cola" in (r.get("name") or "").lower() or "coke" in (r.get("name") or "").lower())
        and "zero" not in (r.get("name") or "").lower()
        and "no sugar" not in (r.get("name") or "").lower()
    ]
    _pepsi_max = [r for r in cola_active if "pepsi max" in (r.get("name") or "").lower()]
    _coke_zero = [
        r
        for r in cola_active
        if "coca cola zero" in (r.get("name") or "").lower()
        or "coke no sugar" in (r.get("name") or "").lower()
        or "coke zero" in (r.get("name") or "").lower()
    ]
    _cola_battles = [
        ("Regular", _best(_pepsi), _best(_coke_cls)),
        ("Sugar-free", _best(_pepsi_max), _best(_coke_zero)),
    ]

    def _is_essential(item):
        subcat = (item.get("subcategory") or "").lower()
        itype = (item.get("type") or "").lower()
        name = (item.get("name") or "").lower()
        if subcat == "snacks":
            return False
        if subcat in _ESSENTIAL_SUBCATS:
            return True
        if itype == "bakery":
            return True
        if itype == "pantry" and subcat == "grains_pasta":
            return True
        if itype == "dairy" and any(kw in name for kw in _DAIRY_ESSENTIAL_KEYWORDS):
            return True
        return False

    def _saving(item):
        wp = item.get("was_price") or 0
        ep = item.get("eff_price") or item.get("price") or wp
        return wp - ep

    essential_specials = sorted(
        [
            r
            for r in active
            if r.get("on_special")
            and r.get("was_price")
            and r.get("compare_group") != "cola"
            and _is_essential(r)
        ],
        key=_saving,
        reverse=True,
    )
    top_essentials = essential_specials[:5]

    essential_names = {i.get("name") for i in top_essentials}
    all_promos = sorted(
        [r for r in active if r.get("on_special") and r.get("was_price")],
        key=_saving,
        reverse=True,
    )
    top_deals = [r for r in all_promos if r.get("name") not in essential_names][:5]

    lines = [
        "\U0001f6d2 *Time to plan your shop!*",
        f"Fresh prices are in \u2014 {specials_count} deals across {items_scraped} tracked items\\.",
        "",
    ]

    _any_cola = any(a or b for _, a, b in _cola_battles)
    if _any_cola:
        lines.append("🧃 *Cola battle (\$/L):*")
        _brand_labels = {
            id(_best(_pepsi)): "Pepsi",
            id(_best(_coke_cls)): "Coke",
            id(_best(_pepsi_max)): "Pepsi Max",
            id(_best(_coke_zero)): "Coke Zero",
        }

        def _fmt(item, is_winner):
            if not item:
                return None
            ep = item.get("eff_price") or item.get("price") or 0
            emoji = store_emoji.get((item.get("store") or "").lower(), "🩊")
            size = _size_label(item.get("name", ""))
            brand = _brand_labels.get(id(item), "")
            disc = " 🔻" if item.get("on_special") else ""
            crown = " 🏆" if is_winner else ""
            return f"{emoji} {brand} {size} \\${ep:.2f}{crown}{disc}"

        for _cat, _side_a, _side_b in _cola_battles:
            if not _side_a and not _side_b:
                continue
            if _side_a and _side_b:
                _winner = (
                    _side_a
                    if (_side_a.get("eff_price") or 9999) <= (_side_b.get("eff_price") or 9999)
                    else _side_b
                )
                lines.append(f"  *{_cat}:* {_fmt(_winner, True)}")
            elif _side_a:
                lines.append(f"  *{_cat}:* {_fmt(_side_a, True)}")
            else:
                lines.append(f"  *{_cat}:* {_fmt(_side_b, True)}")
        lines.append("")

    if top_deals:
        lines.append("\U0001f525 *Best deals:*")
        for item in top_deals:
            ep = item.get("eff_price") or item.get("price") or 0
            wp = item.get("was_price") or 0
            emoji = store_emoji.get((item.get("store") or "").lower(), "\U0001fa4a")
            lines.append(f"  {emoji} {item.get('name', '?')} \u2014 \\${ep:.2f} _\\(-\\${wp - ep:.2f})_")
        lines.append("")

    lines.append(f"\U0001f449 [Open Dashboard]({dashboard_url})")

    return "\n".join(lines)
