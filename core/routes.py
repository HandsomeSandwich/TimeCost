"""TimeCost core feature routes.

All the original single-process TimeCost pages - calculator, personal,
timebank, expenses, budget, goals, staples, freelance, household, the
content/SEO pages, and the UI toggles - live here on the `core` blueprint.

The blueprint is registered with no URL prefix, so every path is identical to
before; only the Flask endpoint names gain a `core.` prefix (so templates use
url_for("core.calculator"), etc.).
"""
from __future__ import annotations

import os
import csv
import io
import secrets
from datetime import datetime
from itertools import zip_longest
from collections import defaultdict

from flask import (
    Blueprint, render_template, request, session, redirect, url_for, Response,
)
from sqlalchemy import text

from database import engine, get_db_connection as get_connection
from core.finance import (
    BILLIONAIRES,
    CURRENCY_TO_USD,
    WORKING_HOURS_PER_YEAR,
    safe_float,
    wealth_comparison,
    money_to_time,
    workday_equivalent,
)
from core.auth import make_pin as _make_pin, verify_pin as _verify_pin
from core.timeutil import utc_now_iso as _dinaro_now
from core.profile import (
    DEFAULT_CURRENCY,
    ALLOWED_CURRENCIES,
    _currency,
    _get_weekly_hours_default40,
    _weekly_to_monthly_hours,
    _parse_date,
    _freelance_range_to_start,
    _get_personal_profile,
    _personal_value,
    _blank_to_none,
    _parse_optional_number,
    get_effective_hourly_rate,
    _hourly_from_wage,
    _prefill_wage_from_personal,
    _require_user_key,
    _current_household_id,
)

core_bp = Blueprint("core", __name__)


@core_bp.get("/favicon.ico")
def favicon():
    return redirect(url_for("static", filename="favicon.svg"))


@core_bp.post("/set-view")
def set_view():
    view = request.form.get("view")
    if view in ("dawn", "dusk", "candy", "personality"):
        session["view"] = view
    return redirect(request.referrer or url_for("core.calculator"))


@core_bp.route("/")
def landing():
    subscribed = request.args.get("subscribed") == "1"
    return render_template("landing.html", subscribed=subscribed)


@core_bp.get("/sitemap.xml")
def sitemap():
    base = "https://thetimecost.com"
    pages = [
        ("/",           "weekly",  "1.0"),
        ("/calculate",  "weekly",  "0.9"),
        ("/personal",   "monthly", "0.7"),
        ("/freelance",  "monthly", "0.7"),
        ("/expenses",   "monthly", "0.7"),
        ("/timebank",   "monthly", "0.7"),
        ("/budget",     "monthly", "0.7"),
        ("/goals",      "monthly", "0.7"),
        ("/staples",    "monthly", "0.7"),
        ("/dinaro",     "weekly",  "0.8"),
        ("/support",    "monthly", "0.5"),
        ("/formulas",   "monthly", "0.6"),
        ("/trillionaire", "monthly", "0.6"),
        ("/celebration/sources", "monthly", "0.5"),
    ]
    xml_lines = ['<?xml version="1.0" encoding="UTF-8"?>',
                 '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for path, freq, priority in pages:
        xml_lines += [
            "  <url>",
            f"    <loc>{base}{path}</loc>",
            f"    <changefreq>{freq}</changefreq>",
            f"    <priority>{priority}</priority>",
            "  </url>",
        ]
    xml_lines.append("</urlset>")
    return "\n".join(xml_lines), 200, {"Content-Type": "application/xml"}


@core_bp.get("/robots.txt")
def robots():
    lines = [
        "User-agent: *",
        "Allow: /",
        "Disallow: /admin/",
        "Disallow: /dinaro/parent/",
        "Disallow: /dinaro/child/",
        "",
        "Sitemap: https://thetimecost.com/sitemap.xml",
    ]
    return "\n".join(lines), 200, {"Content-Type": "text/plain"}


@core_bp.route("/support")
def support():
    links = {
        "min15": os.environ.get("STRIPE_LINK_15MIN", ""),
        "hour1": os.environ.get("STRIPE_LINK_1HOUR", ""),
        "halfday": os.environ.get("STRIPE_LINK_HALFDAY", ""),
    }
    subscribed = request.args.get("subscribed") == "1"
    return render_template("support.html", links=links, subscribed=subscribed)


@core_bp.get("/formulas")
def formulas():
    return render_template("formulas.html")


def _humanize_big(n: float) -> str:
    n = float(n)
    if n >= 1e12:
        return f"{n/1e12:.1f} trillion"
    if n >= 1e9:
        return f"{n/1e9:.1f} billion"
    if n >= 1e6:
        return f"{n/1e6:.1f} million"
    if n >= 1e3:
        return f"{n/1e3:,.0f} thousand"
    return f"{n:,.0f}"


# Projected first-trillionaire annual income (hypothetical $1T-er), USD.
TRILLIONAIRE_GROWTH_USD = 250_000_000_000

# Region-localised everyday baskets for the trillionaire page. Each region carries
# its own currency symbol, USD conversion (so the $-symbol collisions between US/CA/AU
# don't break the math), a rough median hourly wage, an average family-home price (all
# in local currency), and a basket priced + unit-labelled the local way (gallon vs litre,
# "gas" vs "petrol"). Prices are ballpark - this is a satirical page, not an index.
REGIONS = {
    "US": {
        "label": "United States", "flag": "🇺🇸", "currency": "$",
        "usd_rate": 1.00, "default_hourly": 23.0, "home_price": 420_000,
        "basket": [
            {"emoji": "🥛", "name": "a gallon of milk",     "price": 4.00},
            {"emoji": "🥚", "name": "a dozen eggs",          "price": 3.50},
            {"emoji": "🍞", "name": "a loaf of bread",       "price": 2.50},
            {"emoji": "⛽", "name": "a tank of gas",          "price": 52.00, "basis": "≈14 gal × $3.70/gal"},
            {"emoji": "🛒", "name": "a weekly grocery run",  "price": 90.00},
            {"emoji": "🏠", "name": "a month's rent",        "price": 1800.00},
        ],
    },
    "UK": {
        "label": "United Kingdom", "flag": "🇬🇧", "currency": "£",
        "usd_rate": 1.27, "default_hourly": 15.0, "home_price": 290_000,
        "basket": [
            {"emoji": "🥛", "name": "4 pints of milk",       "price": 1.45},
            {"emoji": "🥚", "name": "6 eggs",                 "price": 1.50},
            {"emoji": "🍞", "name": "a loaf of bread",       "price": 1.40},
            {"emoji": "⛽", "name": "a tank of petrol",       "price": 75.00, "basis": "≈50 L × £1.50/L"},
            {"emoji": "🛒", "name": "a weekly food shop",    "price": 75.00},
            {"emoji": "🏠", "name": "a month's rent",        "price": 1300.00},
        ],
    },
    "EU": {
        "label": "Eurozone", "flag": "🇪🇺", "currency": "€",
        "usd_rate": 1.08, "default_hourly": 17.0, "home_price": 320_000,
        "basket": [
            {"emoji": "🥛", "name": "a litre of milk",       "price": 1.10},
            {"emoji": "🥚", "name": "10 eggs",                "price": 2.60},
            {"emoji": "🍞", "name": "a loaf of bread",       "price": 1.60},
            {"emoji": "⛽", "name": "a tank of petrol",       "price": 90.00, "basis": "≈50 L × €1.80/L"},
            {"emoji": "🛒", "name": "a weekly food shop",    "price": 80.00},
            {"emoji": "🏠", "name": "a month's rent",        "price": 1100.00},
        ],
    },
    "CA": {
        "label": "Canada", "flag": "🇨🇦", "currency": "CA$",
        "usd_rate": 0.73, "default_hourly": 25.0, "home_price": 700_000,
        "basket": [
            {"emoji": "🥛", "name": "4 L of milk",           "price": 5.50},
            {"emoji": "🥚", "name": "a dozen eggs",          "price": 3.80},
            {"emoji": "🍞", "name": "a loaf of bread",       "price": 3.00},
            {"emoji": "⛽", "name": "a tank of gas",          "price": 80.00, "basis": "≈50 L × CA$1.60/L"},
            {"emoji": "🛒", "name": "a weekly grocery run",  "price": 110.00},
            {"emoji": "🏠", "name": "a month's rent",        "price": 2000.00},
        ],
    },
    "AU": {
        "label": "Australia", "flag": "🇦🇺", "currency": "A$",
        "usd_rate": 0.66, "default_hourly": 35.0, "home_price": 920_000,
        "basket": [
            {"emoji": "🥛", "name": "2 L of milk",           "price": 3.10},
            {"emoji": "🥚", "name": "a dozen eggs",          "price": 5.50},
            {"emoji": "🍞", "name": "a loaf of bread",       "price": 3.50},
            {"emoji": "⛽", "name": "a tank of petrol",       "price": 95.00, "basis": "≈50 L × A$1.90/L"},
            {"emoji": "🛒", "name": "a weekly food shop",    "price": 120.00},
            {"emoji": "🏠", "name": "a month's rent",        "price": 2200.00},
        ],
    },
    "IN": {
        "label": "India", "flag": "🇮🇳", "currency": "₹",
        "usd_rate": 0.012, "default_hourly": 150.0, "home_price": 8_000_000,
        "basket": [
            {"emoji": "🥛", "name": "a litre of milk",       "price": 60.0},
            {"emoji": "🥚", "name": "a dozen eggs",          "price": 80.0},
            {"emoji": "🍞", "name": "a loaf of bread",       "price": 50.0},
            {"emoji": "⛽", "name": "a tank of petrol",       "price": 4000.0, "basis": "≈40 L × ₹100/L"},
            {"emoji": "🛒", "name": "a weekly grocery run",  "price": 2500.0},
            {"emoji": "🏠", "name": "a month's rent",        "price": 20000.0},
        ],
    },
}

# Eurozone members (ISO territory codes) collapse to the "EU" basket.
_EU_TERRITORIES = {
    "DE", "FR", "ES", "IT", "NL", "IE", "PT", "AT", "BE", "FI", "GR", "SK",
    "SI", "LU", "LV", "LT", "EE", "CY", "MT", "HR",
}
# When locale tells us nothing, fall back from the app's saved currency to a region.
_CURRENCY_TO_REGION = {"$": "US", "£": "UK", "€": "EU", "₹": "IN"}
# How many working hours one "per X" wage period spans (2080h working year).
_PERIOD_HOURS = {"hour": 1.0, "month": WORKING_HOURS_PER_YEAR / 12.0, "year": float(WORKING_HOURS_PER_YEAR)}


def _detect_region(override: str | None) -> str:
    """Resolve the region: explicit override → browser locale → saved currency → US."""
    if override:
        code = override.strip().upper()
        if code in REGIONS:
            return code
        if code == "GB":
            return "UK"
    try:
        for lang, _q in request.accept_languages:
            parts = lang.replace("_", "-").split("-")
            if len(parts) > 1:
                terr = parts[-1].upper()
                if terr in REGIONS:
                    return terr
                if terr == "GB":
                    return "UK"
                if terr in _EU_TERRITORIES:
                    return "EU"
    except Exception:
        pass
    return _CURRENCY_TO_REGION.get(_currency(), "US")


@core_bp.get("/trillionaire")
def trillionaire():
    """Sarcastic campaign page: your everyday spend vs. what the first trillionaire
    pockets in the exact same slice of your working life - localised by region."""
    region_arg = request.args.get("region")
    region = _detect_region(region_arg)
    reg = REGIONS[region]
    currency = reg["currency"]
    usd_rate = reg["usd_rate"]

    # Wage can be entered per hour / month / year; convert to an hourly rate.
    period = request.args.get("period", "hour")
    if period not in _PERIOD_HOURS:
        period = "hour"
    divisor = _PERIOD_HOURS[period]

    wage_q = request.args.get("wage")
    used_default = False
    if wage_q and safe_float(wage_q, 0.0) > 0:
        wage_value = safe_float(wage_q, 0.0)
        hourly = wage_value / divisor
    else:
        used_default = True
        hourly = reg["default_hourly"]
        wage_value = round(reg["default_hourly"] * divisor, 2)

    trill_hourly = (TRILLIONAIRE_GROWTH_USD / WORKING_HOURS_PER_YEAR) / usd_rate
    home_price = reg["home_price"]

    rows = []
    for it in reg["basket"]:
        your_hours = it["price"] / hourly
        earns = trill_hourly * your_hours
        # Same loot expressed in average family homes - varies per item and lands
        # harder than "N of the same grocery" (which is a flat ratio).
        homes = earns / home_price
        # Rounded to a clean figure and shown as digits ("24,000"), which read as a
        # giant scoreboard number - punchier and far more compact than spelled-out words.
        if homes < 1000:
            homes_round = round(homes)
        elif homes < 10000:
            homes_round = round(homes, -2)
        else:
            homes_round = round(homes, -3)
        rows.append({
            **it,
            "minutes": your_hours * 60.0,
            "pct_workday": your_hours / 8.0 * 100.0,
            "earns": earns,
            "earns_h": _humanize_big(earns),
            "homes_h": _humanize_big(homes),
            "homes_round": f"{homes_round:,.0f}",
        })

    return render_template(
        "trillionaire.html",
        currency=currency,
        hourly=hourly,
        used_default=used_default,
        rows=rows,
        climax=rows[-1],
        region=region,
        region_flag=reg["flag"],
        region_label=reg["label"],
        region_overridden=bool(region_arg),
        regions=[(code, r["flag"], r["label"]) for code, r in REGIONS.items()],
        period=period,
        wage_value=wage_value,
        home_price=reg["home_price"],
        default_hourly=reg["default_hourly"],
        trill_per_hour_usd=TRILLIONAIRE_GROWTH_USD / WORKING_HOURS_PER_YEAR,
        trillionaire_growth_usd=TRILLIONAIRE_GROWTH_USD,
        working_hours_year=WORKING_HOURS_PER_YEAR,
    )


# The human cost behind the celebration - real, sourced figures, presented as
# affected POPULATIONS and PROJECTIONS (never named individuals). The satire lives
# in the celebration; these lines are deliberately plain and respectful.
TRILLIONAIRE_CUTS = [
    {
        "program": "USAID - global health, nutrition & humanitarian aid",
        "served": "the poorest communities across 130+ countries",
        "impact": "Up to 14 million additional deaths projected by 2030 - including over 4.5 million children under five.",
        "source": "The Lancet, 2025",
        "url": "https://www.thelancet.com/journals/lancet/article/PIIS0140-6736(25)01186-9/fulltext",
    },
    {
        "program": "PEPFAR - HIV treatment & prevention",
        "served": "20.6 million people who relied on it for HIV treatment",
        "impact": "Up to ~4 million additional AIDS-related deaths projected for 2025–2029, including roughly 300,000 children.",
        "source": "UNAIDS, 2025",
        "url": "https://www.unaids.org/en/impact-US-funding-cuts",
    },
    {
        "program": "President's Malaria Initiative - roughly 47% cut",
        "served": "families across malaria-endemic Africa",
        "impact": "An estimated 15 million more malaria cases and about 107,000 additional deaths in a single year.",
        "source": "WHO, 2025",
        "url": "https://www.cnn.com/2025/06/11/africa/malaria-us-foreign-aid-cuts-africa-intl",
    },
    {
        "program": "Therapeutic food for severe malnutrition (RUTF)",
        "served": "children with severe acute malnutrition - the US funded about half the global supply",
        "impact": "Around 1 million children left untreated - an estimated 163,500 additional deaths each year.",
        "source": "Maternal & Child Nutrition, 2025",
        "url": "https://onlinelibrary.wiley.com/doi/10.1111/mcn.70028",
    },
]


# Full citation list for the public reference sheet (/celebration/sources). VERIFIED
# sources only - peer-reviewed studies, UN/health agencies, institutional trackers,
# and major outlets. Each entry: the claim as used on the pages + every source.
REFERENCE_REVIEWED = "June 2026"
TRILLIONAIRE_REFERENCES = [
    {
        "claim": "Elon Musk became the world's first trillionaire.",
        "detail": "His net worth crossed $1 trillion on 12 June 2026, when SpaceX began "
                  "trading on the Nasdaq (the largest IPO on record); with his Tesla stake, "
                  "roughly $1.05 trillion.",
        "sources": [
            {"pub": "CNBC", "kind": "News", "date": "Jun 2026",
             "title": "Elon Musk becomes world's first trillionaire as SpaceX begins trading on the Nasdaq",
             "url": "https://www.cnbc.com/2026/06/12/elon-musk-trillionaire-spacex.html"},
            {"pub": "The Washington Post", "kind": "News", "date": "Jun 2026",
             "title": "The world has its first trillionaire after Elon Musk's SpaceX market debut",
             "url": "https://www.washingtonpost.com/technology/2026/06/12/spacex-start-trading-historic-test-elon-musks-rocket-company/"},
        ],
    },
    {
        "claim": "USAID cuts: up to 14 million additional deaths by 2030, including over 4.5 million children under five.",
        "detail": "A peer-reviewed forecast of mortality from defunding USAID across health, "
                  "nutrition, water/sanitation and humanitarian programmes, covering 133 countries.",
        "sources": [
            {"pub": "The Lancet", "kind": "Peer-reviewed study", "date": "Jul 2025",
             "title": "Evaluating the impact of two decades of USAID interventions and projecting the effects of defunding on mortality up to 2030",
             "url": "https://www.thelancet.com/journals/lancet/article/PIIS0140-6736(25)01186-9/fulltext"},
            {"pub": "Boston University School of Public Health", "kind": "Institutional tracker", "date": "2025",
             "title": "Tracking Anticipated Deaths from USAID Funding Cuts",
             "url": "https://www.bu.edu/sph/news/articles/2025/tracking-anticipated-deaths-from-usaid-funding-cuts/"},
            {"pub": "Center for Global Development", "kind": "Policy analysis", "date": "2025",
             "title": "Update on Lives Lost from USAID Cuts",
             "url": "https://www.cgdev.org/blog/update-lives-lost-usaid-cuts"},
        ],
    },
    {
        "claim": "PEPFAR / HIV cuts: up to ~4 million additional AIDS-related deaths (2025-2029), including ~300,000 children.",
        "detail": "The ~4 million / ~300,000-children figure is the UNAIDS projection if US-supported "
                  "HIV programmes permanently collapse (PEPFAR had supported HIV treatment for about "
                  "20.6 million people). A separate Lancet HIV modelling study estimates a different, "
                  "wider range - roughly 0.8 to 2.9 million additional HIV deaths by 2030 (up to ~120,000 children).",
        "sources": [
            {"pub": "UNAIDS", "kind": "UN agency", "date": "2025",
             "title": "Impact of US funding cuts on the global HIV response",
             "url": "https://www.unaids.org/en/impact-US-funding-cuts"},
            {"pub": "The Lancet HIV", "kind": "Peer-reviewed study", "date": "2025",
             "title": "Impact of an international HIV funding crisis on HIV infections and mortality in low-income and middle-income countries: a modelling study",
             "url": "https://www.thelancet.com/journals/lanhiv/article/PIIS2352-3018(25)00074-8/fulltext"},
        ],
    },
    {
        "claim": "Malaria: ~15 million more cases and ~107,000 additional deaths in a single year.",
        "detail": "Impact of US cuts to malaria programmes, flagged by the WHO (Dr Tedros Adhanom "
                  "Ghebreyesus); the US had been the largest bilateral donor to malaria control. "
                  "The ~107,000 figure sits within the projected range of ~71,000-166,000 additional "
                  "deaths per year.",
        "sources": [
            {"pub": "World Health Organization (reported by CNN)", "kind": "UN agency / News", "date": "Jun 2025",
             "title": "US foreign aid cuts threaten decades of progress on driving down malaria",
             "url": "https://www.cnn.com/2025/06/11/africa/malaria-us-foreign-aid-cuts-africa-intl"},
        ],
    },
    {
        "claim": "Child malnutrition: ~1 million children denied treatment for severe acute malnutrition → ~163,500 additional child deaths per year.",
        "detail": "USAID funded about half the global supply of ready-to-use therapeutic food "
                  "(RUTF). Combined with other donors' cuts, the projected toll roughly doubles to ~369,000/yr.",
        "sources": [
            {"pub": "Maternal & Child Nutrition (Wiley)", "kind": "Peer-reviewed study", "date": "2025",
             "title": "Children at Risk: The Growing Impact of USAID Cuts on Pediatric Malnutrition and Death Rates",
             "url": "https://onlinelibrary.wiley.com/doi/10.1111/mcn.70028"},
            {"pub": "Nature", "kind": "Scientific journal (news)", "date": "Mar 2025",
             "title": "The full lethal impact of massive cuts to international food aid",
             "url": "https://www.nature.com/articles/d41586-025-00898-3"},
        ],
    },
]


@core_bp.get("/celebration")
def trillionaire_credits():
    """A satirical 80s-terminal 'celebration' of the first trillionaire, with the
    real human cost of the funding cuts rolling underneath as respectful credits.
    Standalone, shareable page (own URL, share buttons, link to the calculator)."""
    return render_template("trillionaire_credits.html", cuts=TRILLIONAIRE_CUTS)


@core_bp.get("/trillionaire/credits")
def trillionaire_credits_legacy():
    """Old nested path → keep any existing links working."""
    return redirect(url_for("core.trillionaire_credits"), code=301)


@core_bp.get("/celebration/sources")
def celebration_sources():
    """Public reference sheet - every figure on the trillionaire/celebration pages
    with its verified source(s). Shareable by link."""
    return render_template(
        "celebration_sources.html",
        references=TRILLIONAIRE_REFERENCES,
        reviewed=REFERENCE_REVIEWED,
    )


@core_bp.route("/subscribe", methods=["POST"])
def subscribe():
    email = request.form.get("email", "").strip().lower()
    if not email or "@" not in email or "." not in email.split("@")[-1]:
        return redirect(url_for("core.landing"))
    source = request.form.get("source", "landing")
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO email_signups (email, source, signed_up_at) "
                    "VALUES (:email, :source, :now)"
                ),
                {"email": email, "source": source, "now": datetime.utcnow().isoformat()},
            )
    except Exception:
        pass  # Duplicate email - silently succeed
    if source == "support":
        return redirect(url_for("core.support") + "?subscribed=1")
    if source == "dinaro_multichild":
        return redirect(url_for("dinaro.dinaro_parent_upgrade") + "?subscribed=1")
    return redirect(url_for("core.landing") + "?subscribed=1")


@core_bp.route("/admin/subscribers")
def admin_subscribers():
    key = request.args.get("key", "")
    admin_key = os.environ.get("ADMIN_KEY", "")
    if not admin_key or key != admin_key:
        return "Unauthorized", 401
    with engine.begin() as conn:
        rows = conn.execute(
            text("SELECT email, source, signed_up_at FROM email_signups ORDER BY signed_up_at DESC")
        ).mappings().all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["email", "source", "signed_up_at"])
    for row in rows:
        writer.writerow([row["email"], row["source"], row["signed_up_at"]])
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=subscribers.csv"},
    )


@core_bp.route("/calculate", methods=["GET", "POST"])
def calculator():
    pre_wage_type, pre_wage_amount, _source = _prefill_wage_from_personal()

    result = None
    error = None
    item_name = ""
    item_cost = ""
    time_cost = None
    workday_text = ""

    display_wage_type = pre_wage_type
    display_wage_amount = pre_wage_amount
    prefill_source = "personal" if (pre_wage_amount or "").strip() else None

    if request.method == "POST":
        item_name = request.form.get("itemName", "")
        item_cost = (request.form.get("itemCost") or "").strip()

        wage_type = (request.form.get("wageType") or pre_wage_type).strip().lower()
        wage_amount_raw = (request.form.get("wageAmount") or "").strip()

        display_wage_type = wage_type
        display_wage_amount = wage_amount_raw if wage_amount_raw != "" else pre_wage_amount
        prefill_source = "personal" if wage_amount_raw == "" and (pre_wage_amount or "").strip() else None

        try:
            item_cost_f = float(item_cost)
            if item_cost_f < 0:
                raise ValueError("Cost can't be negative")

            if wage_amount_raw == "":
                effective_hourly = get_effective_hourly_rate()
                if effective_hourly is None or effective_hourly <= 0:
                    raise ValueError("No wage available")
                hourly_rate = float(effective_hourly)
            else:
                wage_amount_f = float(wage_amount_raw)
                if wage_amount_f <= 0:
                    raise ValueError("Wage must be > 0")

                hourly_rate = _hourly_from_wage(wage_amount_f, wage_type)
                if hourly_rate <= 0:
                    raise ValueError("Converted wage must be > 0")

            result = item_cost_f / hourly_rate
            time_cost = money_to_time(item_cost_f, hourly_rate)

            # Remember the hourly rate so other pages can detect a wage
            session["hourlyRate"] = str(round(hourly_rate, 4))

            if time_cost["ok"]:
                workday_text = workday_equivalent(time_cost["total_hours"])

        except (ValueError, TypeError, ZeroDivisionError):
            result = None
            error = "Invalid input."
            time_cost = {"ok": False, "error": "Invalid input.", "human": ""}

    # Build wealth comparison only when we have a valid numeric result
    wealth_rows = None
    if isinstance(result, float) and result > 0:
        wealth_rows = wealth_comparison(float(item_cost), session.get("currency", DEFAULT_CURRENCY), user_hourly=hourly_rate)

    return render_template(
        "calculator.html",
        result=result,
        error=error,
        item_name=item_name,
        item_cost=item_cost,
        pre_wage_type=pre_wage_type,
        pre_wage_amount=pre_wage_amount,
        prefill_source=prefill_source,
        time_cost=time_cost,
        workday_text=workday_text,
        display_wage_type=display_wage_type,
        display_wage_amount=display_wage_amount,
        wealth_rows=wealth_rows,
        now_month_year=datetime.now().strftime("%B %Y"),
    )


# ----------------------------
# Routes: Personal
# ----------------------------
@core_bp.route("/personal", methods=["GET", "POST"])
def personal():
    error = None
    profile = _get_personal_profile()

    if request.method == "POST":
        action = (request.form.get("action") or "save").strip().lower()
        profile_name = (request.form.get("profile_name") or "").strip()
        profile_pin = (request.form.get("profile_pin") or "").strip()

        if not profile_name or not profile_pin:
            error = "Profile name and PIN are required."
        else:
            conn = get_connection()
            try:
                existing = conn.execute(
                    text("SELECT * FROM personal_profiles WHERE profile_name = :name"),
                    {"name": profile_name},
                ).mappings().first()
            finally:
                conn.close()

            if action == "load":
                if not existing or not _verify_pin(profile_pin, existing["pin_hash"], existing["pin_salt"]):
                    error = "Wrong profile name or PIN."
                else:
                    session["personal_profile_id"] = int(existing["id"])
                    if existing.get("currency"):
                        session["currency"] = existing["currency"]
                    session["username"] = existing.get("display_name") or ""
                    return redirect(url_for("core.personal"))
            else:
                if existing and not _verify_pin(profile_pin, existing["pin_hash"], existing["pin_salt"]):
                    error = "Wrong profile name or PIN."
                else:
                    pin_hash = existing["pin_hash"] if existing else None
                    pin_salt = existing["pin_salt"] if existing else None
                    if not existing:
                        pin_hash, pin_salt = _make_pin(profile_pin)

                    display_name = _blank_to_none(request.form.get("username"))
                    work_hours = safe_float(request.form.get("workHours"), 40.0)

                    c = request.form.get("currency", session.get("currency", DEFAULT_CURRENCY))
                    currency = c if c in ALLOWED_CURRENCIES else DEFAULT_CURRENCY

                    annual_rate = _parse_optional_number(request.form.get("annualRate"))
                    hourly_rate = _parse_optional_number(request.form.get("hourlyRate"))
                    pay_frequency = _blank_to_none(request.form.get("payFrequency"))
                    paycheck_amount = _parse_optional_number(request.form.get("paycheckAmount"))

                    now = _dinaro_now()

                    with engine.begin() as conn:
                        if existing:
                            conn.execute(
                                text(
                                    """
                                    UPDATE personal_profiles
                                    SET display_name = :display_name,
                                        currency = :currency,
                                        work_hours = :work_hours,
                                        annual_rate = :annual_rate,
                                        hourly_rate = :hourly_rate,
                                        pay_frequency = :pay_frequency,
                                        paycheck_amount = :paycheck_amount,
                                        updated_at = :updated_at
                                    WHERE id = :id
                                    """
                                ),
                                {
                                    "display_name": display_name,
                                    "currency": currency,
                                    "work_hours": work_hours,
                                    "annual_rate": annual_rate,
                                    "hourly_rate": hourly_rate,
                                    "pay_frequency": (pay_frequency or "").lower() or None,
                                    "paycheck_amount": paycheck_amount,
                                    "updated_at": now,
                                    "id": existing["id"],
                                },
                            )
                            profile_id = existing["id"]
                        else:
                            row = conn.execute(
                                text(
                                    """
                                    INSERT INTO personal_profiles
                                    (profile_name, pin_hash, pin_salt, display_name, currency, work_hours,
                                     annual_rate, hourly_rate, pay_frequency, paycheck_amount, created_at, updated_at)
                                    VALUES
                                    (:profile_name, :pin_hash, :pin_salt, :display_name, :currency, :work_hours,
                                     :annual_rate, :hourly_rate, :pay_frequency, :paycheck_amount, :created_at, :updated_at)
                                    RETURNING id
                                    """
                                ),
                                {
                                    "profile_name": profile_name,
                                    "pin_hash": pin_hash,
                                    "pin_salt": pin_salt,
                                    "display_name": display_name,
                                    "currency": currency,
                                    "work_hours": work_hours,
                                    "annual_rate": annual_rate,
                                    "hourly_rate": hourly_rate,
                                    "pay_frequency": (pay_frequency or "").lower() or None,
                                    "paycheck_amount": paycheck_amount,
                                    "created_at": now,
                                    "updated_at": now,
                                },
                            ).mappings().first()
                            profile_id = row["id"] if row else None
                            if profile_id is None:
                                profile_id = conn.execute(text("SELECT last_insert_rowid() AS id")).mappings().first()["id"]

                    session["personal_profile_id"] = int(profile_id)
                    session["currency"] = currency
                    session["username"] = display_name or ""
                    return redirect(url_for("core.calculator"))

    owner_key = _personal_value("profile_name") or session.get("user_key")
    conn = get_connection()
    try:
        row = conn.execute(
            text("SELECT COALESCE(SUM(amount), 0) AS total FROM expenses WHERE owner_key = :uk"),
            {"uk": owner_key}
        ).mappings().first()
        expenses_total = float(row["total"]) if row and row["total"] is not None else 0.0
    except Exception:
        expenses_total = 0.0
    finally:
        conn.close()

    profile_name = profile["profile_name"] if profile else ""
    if error:
        profile_name = (request.form.get("profile_name") or "").strip()
    return render_template(
        "personal.html",
        error=error,
        profile_name=profile_name,
        username=profile.get("display_name") if profile else session.get("username", ""),
        annualRate=profile.get("annual_rate") if profile else session.get("annualRate", ""),
        hourlyRate=profile.get("hourly_rate") if profile else session.get("hourlyRate", ""),
        workHours=profile.get("work_hours") if profile else session.get("workHours", 40),
        expenses=expenses_total,
        paycheckAmount=profile.get("paycheck_amount") if profile else session.get("paycheckAmount", ""),
        payFrequency=profile.get("pay_frequency") if profile else session.get("payFrequency", ""),
        currency=_currency(),
    )


# ----------------------------
# Routes: Timebank
# ----------------------------
@core_bp.route("/timebank", methods=["GET", "POST"])
def timebank():
    currency = _currency()
    owner_key = _personal_value("profile_name") or session.get("user_key")

    def fetch_savings_total() -> float:
        conn = get_connection()
        try:
            row = conn.execute(
            text("SELECT COALESCE(SUM(amount), 0) AS total FROM expenses WHERE category = 'Nest Egg' AND owner_key = :uk"),
            {"uk": owner_key}
            ).mappings().first()
            return float(row["total"]) if row and row["total"] is not None else 0.0
        finally:
            conn.close()

    def fetch_all_expenses():
        conn = get_connection()
        try:
            return conn.execute(
                text("SELECT amount, category FROM expenses WHERE owner_key = :uk"),
                {"uk": owner_key}
            ).mappings().all()
        finally:
            conn.close()

    if request.method == "POST":
        income = safe_float(request.form.get("income"), 0.0)
        expenses = safe_float(request.form.get("expenses"), 0.0)
        hoursWorked = safe_float(request.form.get("hoursWorked"), 0.0)

        savings_value = fetch_savings_total()

        return render_template(
            "timebank.html",
            income=income,
            expenses=expenses,
            hoursWorked=hoursWorked,
            savings_value=savings_value,
            currency=currency,
        )

    income = safe_float(_personal_value("annual_rate", session.get("annualRate")), 0.0) / 12.0

    all_rows = fetch_all_expenses()
    expenses_total = sum((row.get("amount") or 0) for row in all_rows)
    savings_value = sum((row.get("amount") or 0) for row in all_rows if row.get("category") in ("Nest Egg", "Savings")
)

    weekly_hours = safe_float(_personal_value("work_hours", session.get("workHours")), 0.0)
    hoursWorked = _weekly_to_monthly_hours(weekly_hours) if weekly_hours > 0 else 0.0

    return render_template(
        "timebank.html",
        income=income,
        expenses=expenses_total,
        hoursWorked=hoursWorked,
        savings_value=savings_value,
        currency=currency,
    )


# ----------------------------
# Routes: Expenses
# ----------------------------
@core_bp.route("/expenses", methods=["GET", "POST"])
def expenses():
    owner_key = _personal_value("profile_name") or session.get("user_key")

    if request.method == "POST":
        # If user clicked "Add expense", insert a blank row and bounce back
        if "add" in request.form:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO expenses (name, amount, category, scope, owner_key) "
                        "VALUES (:name, :amount, :category, :scope, :owner_key)"
                    ),
                    {"name": "", "amount": 0.0, "category": "House & Light", "scope": "personal", "owner_key": owner_key},
                )
            return redirect(url_for("core.expenses"))

        # Otherwise treat as Save
        expense_names = request.form.getlist("expense_name[]")
        expense_amounts = request.form.getlist("expense_amount[]")
        expense_categories = request.form.getlist("expense_category[]")
        expense_scopes = request.form.getlist("expense_scope[]")

        with engine.begin() as conn:
            conn.execute(text("DELETE FROM expenses WHERE owner_key = :uk"), {"uk": owner_key})

            for name, amount, category, scope in zip_longest(
                expense_names, expense_amounts, expense_categories, expense_scopes
            ):
                name = (name or "").strip()
                category = (category or "").strip()
                scope = (scope or "personal").strip() or "personal"

                try:
                    amt = float(amount)
                except (TypeError, ValueError):
                    continue

                # keep blank rows from being saved forever
                if not name:
                    continue

                conn.execute(
                    text(
                        "INSERT INTO expenses (name, amount, category, scope, owner_key) "
                        "VALUES (:name, :amount, :category, :scope, :owner_key)"
                    ),
                    {"name": name, "amount": amt, "category": category, "scope": scope, "owner_key": owner_key},
                )

        return redirect(url_for("core.expenses"))

    # GET
    conn = get_connection()
    try:
        saved_expenses = conn.execute(
            text("SELECT * FROM expenses WHERE owner_key = :uk ORDER BY id ASC"),
            {"uk": owner_key}
        ).mappings().all()
        category_totals = conn.execute(
            text("SELECT category, COALESCE(SUM(amount), 0) AS total FROM expenses WHERE owner_key = :uk GROUP BY category"),
            {"uk": owner_key}
        ).mappings().all()
        hourly_value = get_effective_hourly_rate() or 0.0
    finally:
        conn.close()

    return render_template(
        "expenses.html",
        saved_expenses=saved_expenses,
        category_totals=category_totals,
        currency=_currency(),
        hourly_value=hourly_value,
    )


@core_bp.post("/expenses/reset")
def expenses_reset():
    owner_key = _personal_value("profile_name") or session.get("user_key")
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM expenses WHERE owner_key = :uk"), {"uk": owner_key})
    return redirect(url_for("core.expenses"))

@core_bp.route("/update_expense_category", methods=["POST"])
def update_expense_category():
    expense_id = request.form.get("expense_id")
    new_category = request.form.get("new_category")
    owner_key = _personal_value("profile_name") or session.get("user_key")
    if not expense_id or not new_category:
        return redirect(url_for("core.expenses"))

    with engine.begin() as conn:
        conn.execute(
            text("UPDATE expenses SET category = :cat WHERE id = :id AND owner_key = :uk"),
            {"cat": new_category, "id": int(expense_id), "uk": owner_key},
        )

    return redirect(url_for("core.expenses"))


@core_bp.route("/remove_expense/<int:index>", methods=["POST"])
def remove_expense(index):
    owner_key = _personal_value("profile_name") or session.get("user_key")
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM expenses WHERE id = :id AND owner_key = :uk"), {"id": index, "uk": owner_key})
    return redirect(url_for("core.expenses"))


# ----------------------------
# Routes: Budget
# ----------------------------
@core_bp.route("/budget", methods=["GET", "POST"])
def budget():
    currency = _currency()
    owner_key = _personal_value("profile_name") or session.get("user_key")

    conn = get_connection()
    try:
        row = conn.execute(
            text("SELECT COALESCE(SUM(amount), 0) AS total FROM expenses WHERE owner_key = :uk"),
            {"uk": owner_key}
        ).mappings().first()
        expenses_total = float(row["total"]) if row and row["total"] is not None else 0.0
    except Exception:
        expenses_total = 0.0
    finally:
        conn.close()

    def fetch_goals():
        conn2 = get_connection()
        try:
            return conn2.execute(
                text("SELECT * FROM goals WHERE owner_key = :uk"),
                {"uk": owner_key}
            ).mappings().all()
        except Exception:
            return []
        finally:
            conn2.close()

    if request.method == "POST":
        income_input = request.form.get("income")
        hours_input = request.form.get("weeklyHours")

        income = safe_float(
            income_input,
            safe_float(_personal_value("annual_rate", session.get("annualRate")), 0.0) / 12.0,
        )
        weekly_hours = safe_float(
            hours_input,
            safe_float(_personal_value("work_hours", session.get("workHours")), 0.0),
        )
        monthly_hours = weekly_hours * 4.33 if weekly_hours > 0 else 0.0

        discretionary_income = income - expenses_total
        hourly_value = (discretionary_income / monthly_hours) if monthly_hours > 0 else 0.0

        savings_goal = safe_float(request.form.get("savingsGoal"), 0.0)
        current_savings = safe_float(request.form.get("currentSavings"), 0.0)

        remaining_to_save = (savings_goal - current_savings) if savings_goal > 0 else 0.0
        progress_percent = (current_savings / savings_goal) * 100.0 if savings_goal > 0 else 0.0

        goals_rows = fetch_goals()

        return render_template(
            "budget.html",
            income=income,
            expenses=expenses_total,
            weekly_hours=weekly_hours,
            monthly_hours=monthly_hours,
            discretionary_income=discretionary_income,
            hourly_value=hourly_value,
            savings_goal=savings_goal,
            current_savings=current_savings,
            remaining_to_save=remaining_to_save,
            progress_percent=progress_percent,
            goals=goals_rows,
            currency=currency,
        )

    income = safe_float(_personal_value("annual_rate", session.get("annualRate")), 0.0) / 12.0
    weekly_hours = safe_float(_personal_value("work_hours", session.get("workHours")), 0.0)
    monthly_hours = weekly_hours * 4.33 if weekly_hours > 0 else 0.0
    discretionary_income = income - expenses_total
    hourly_value = (discretionary_income / monthly_hours) if monthly_hours > 0 else 0.0

    goals_rows = fetch_goals()

    return render_template(
        "budget.html",
        income=income,
        expenses=expenses_total,
        weekly_hours=weekly_hours,
        monthly_hours=monthly_hours,
        discretionary_income=discretionary_income,
        hourly_value=hourly_value,
        savings_goal=0,
        current_savings=0,
        remaining_to_save=0,
        progress_percent=0,
        goals=goals_rows,
        currency=currency,
    )


# ----------------------------
# Routes: Goals
# ----------------------------
@core_bp.route("/goals", methods=["GET", "POST"])
def goals():
    owner_key = _personal_value("profile_name") or session.get("user_key")

    if request.method == "POST":
        with engine.begin() as conn:
            if "new_goal" in request.form:
                name = (request.form.get("goal_name") or "").strip()
                target = safe_float(request.form.get("target_amount"), 0.0)
                current = safe_float(request.form.get("current_savings"), 0.0)

                if name:
                    conn.execute(
                        text("INSERT INTO goals (owner_key, name, target, current) VALUES (:uk,:n,:t,:c)"),
                        {"uk": owner_key, "n": name, "t": target, "c": current},
                    )

            elif "update_goal" in request.form:
                goal_id = int(safe_float(request.form.get("goal_index"), 0))
                add_amount = safe_float(request.form.get("savings_to_add"), 0.0)

                goal_row = conn.execute(
                    text("SELECT current FROM goals WHERE id = :id AND owner_key = :uk"),
                    {"id": goal_id, "uk": owner_key},
                ).mappings().first()

                if goal_row:
                    new_total = safe_float(goal_row.get("current"), 0.0) + add_amount
                    conn.execute(
                        text("UPDATE goals SET current = :c WHERE id = :id AND owner_key = :uk"),
                        {"c": new_total, "id": goal_id, "uk": owner_key},
                    )

        return redirect(url_for("core.goals"))

    conn = get_connection()
    try:
        goals_rows = conn.execute(
            text("SELECT * FROM goals WHERE owner_key = :uk"),
            {"uk": owner_key}
        ).mappings().all()
    finally:
        conn.close()

    return render_template("goals.html", goals=goals_rows, currency=_currency())


@core_bp.route("/delete_goal/<int:goal_id>", methods=["POST"])
def delete_goal(goal_id):
    owner_key = _personal_value("profile_name") or session.get("user_key")
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM goals WHERE id = :id AND owner_key = :uk"), {"id": goal_id, "uk": owner_key})
    return redirect(url_for("core.goals"))


# ----------------------------
# Routes: Staples
# ----------------------------
@core_bp.route("/staples", methods=["GET"])
def staples():
    currency = _currency()
    hr = _personal_value("hourly_rate", session.get("hourlyRate")) or ""

    if not hr:
        eff = get_effective_hourly_rate()
        hr = f"{eff:.2f}" if eff and eff > 0 else ""

    # Billionaires data for client-side JS
    b_data = []
    for b in BILLIONAIRES:
        b_data.append({
            "name": b["name"],
            "hourly_net_worth": b["net_worth_usd"] / WORKING_HOURS_PER_YEAR,
            "hourly_growth": b["annual_growth_usd"] / WORKING_HOURS_PER_YEAR
        })

    # GET view
    conn = get_connection()
    owner_key = _personal_value("profile_name") or session.get("user_key")
    try:
        saved_staples = conn.execute(
            text("SELECT name, cost FROM staples WHERE owner_key = :uk"),
            {"uk": owner_key}
        ).mappings().all()
    finally:
        conn.close()

    return render_template(
        "staples.html",
        hourlyRate=hr,
        currency=currency,
        billionaires_data=b_data,
        currency_to_usd=CURRENCY_TO_USD,
        now_month_year=datetime.now().strftime("%B %Y"),
        saved_staples=saved_staples
    )


@core_bp.route("/staples", methods=["POST"])
def staples_post():
    names = request.form.getlist("staple_name[]")
    costs = request.form.getlist("staple_cost[]")
    rate = request.form.get("staple_hourly_rate")
    owner_key = _personal_value("profile_name") or session.get("user_key")

    if not owner_key:
        return redirect(url_for("core.staples"))

    # Optional: Persist the rate to the session/profile if provided
    if rate:
        try:
            f_rate = float(rate)
            if f_rate > 0:
                # If we have a profile, update it
                profile_id = session.get("personal_profile_id")
                if profile_id:
                    with engine.begin() as conn:
                        conn.execute(
                            text("UPDATE personal_profiles SET hourly_rate = :hr, updated_at = :now WHERE id = :id"),
                            {"hr": f_rate, "now": _dinaro_now(), "id": profile_id}
                        )
                else:
                    # Fallback to session
                    session["hourlyRate"] = f"{f_rate:.2f}"
        except (ValueError, TypeError):
            pass

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM staples WHERE owner_key = :uk"), {"uk": owner_key})
        for n, c in zip(names, costs):
            if n.strip():
                conn.execute(
                    text("INSERT INTO staples (owner_key, name, cost) VALUES (:uk, :n, :c)"),
                    {"uk": owner_key, "n": n.strip(), "c": safe_float(c)}
                )

    return redirect(url_for("core.staples"))


# ----------------------------
# Routes: Freelance
# ----------------------------
@core_bp.route("/freelance", methods=["GET", "POST"])
def freelance():
    owner_key = _personal_value("profile_name") or session.get("user_key")
    range_key = (request.args.get("range") or "month").strip().lower()
    start_date = _freelance_range_to_start(range_key)

    date_col = "work_date"

    # POST: Use this effective rate in TimeCost
    if request.method == "POST":
        action = (request.form.get("action") or "").strip().lower()
        if action == "use_effective_rate":
            conn = get_connection()
            try:
                rows = conn.execute(
                    text(
                        f"""
                        SELECT hours, hourly_rate
                        FROM freelance_entries
                        WHERE {date_col} >= :start AND owner_key = :uk
                        """
                    ),
                    {"start": start_date, "uk": owner_key},
                ).mappings().all()
            finally:
                conn.close()

            total_hours = sum(safe_float(r.get("hours"), 0.0) for r in rows)
            total_earned = sum(
                safe_float(r.get("hours"), 0.0) * safe_float(r.get("hourly_rate"), 0.0)
                for r in rows
            )
            effective = (total_earned / total_hours) if total_hours > 0 else 0.0

            if effective > 0:
                session["wageSource"] = "freelance"
                session["freelanceHourlyRate"] = f"{effective:.2f}"

        return redirect(url_for("core.freelance", range=range_key))

    # GET view
    conn = get_connection()
    try:
        entries = conn.execute(
            text(
                f"""
                SELECT
                  id,
                  work_date,
                  hours,
                  hourly_rate AS rate,
                  (hours * hourly_rate) AS total,
                  notes,
                  client AS job_name
                FROM freelance_entries
                WHERE {date_col} >= :start AND owner_key = :uk
                ORDER BY work_date DESC, id DESC
                """
            ),
            {"start": start_date, "uk": owner_key},
        ).mappings().all()

    finally:
        conn.close()

    total_hours = round(sum(safe_float(e.get("hours"), 0.0) for e in entries), 2)
    total_earned = round(sum(safe_float(e.get("total"), 0.0) for e in entries), 2)
    effective_rate = round((total_earned / total_hours) if total_hours > 0 else 0.0, 2)

    using_freelance = session.get("wageSource") == "freelance"
    freelance_hourly = session.get("freelanceHourlyRate", "")

    # Breakdown: how much time/earned per "job/client"
    by_client = defaultdict(lambda: {"hours": 0.0, "earned": 0.0})

    for e in entries:
        client = (e.get("job_name") or "Private").strip()
        hours = float(e.get("hours") or 0)
        rate = float(e.get("rate") or 0)
        earned = float(e.get("total") or (hours * rate) or 0)

        by_client[client]["hours"] += hours
        by_client[client]["earned"] += earned

    client_rows = []
    for client, v in by_client.items():
        h = v["hours"]
        earned = v["earned"]
        avg_rate = (earned / h) if h > 0 else 0.0
        equiv_hours = (earned / effective_rate) if effective_rate > 0 else 0.0

        client_rows.append(
            {
                "client": client,
                "hours": round(h, 2),
                "earned": round(earned, 2),
                "avg_rate": round(avg_rate, 2),
                "equiv_hours": round(equiv_hours, 2),
            }
        )

    client_rows.sort(key=lambda r: r["earned"], reverse=True)

    equiv_total_hours = round((total_earned / effective_rate) if effective_rate > 0 else 0.0, 2)

    return render_template(
        "freelance.html",
        currency=_currency(),
        range_key=range_key,
        jobs=[],
        entries=entries,
        total_hours=total_hours,
        total_earned=total_earned,
        effective_rate=effective_rate,
        using_freelance=using_freelance,
        freelance_hourly=freelance_hourly,
        client_rows=client_rows,
        equiv_total_hours=equiv_total_hours,
    )


@core_bp.post("/freelance/add_job")
def freelance_add_job():
    return redirect(url_for("core.freelance"))


@core_bp.post("/freelance/add_entry")
def freelance_add_entry():
    owner_key = _personal_value("profile_name") or session.get("user_key")
    client = (request.form.get("client") or "").strip()

    if not client:
        client = "Private"

    work_date = _parse_date((request.form.get("work_date") or "").strip())
    hours = safe_float(request.form.get("hours"), 0.0)
    rate = safe_float(request.form.get("rate"), 0.0)
    notes = (request.form.get("notes") or "").strip()

    if hours <= 0 or rate <= 0:
        return redirect(url_for("core.freelance"))

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO freelance_entries
                  (owner_key, work_date, client, hours, hourly_rate, notes)
                VALUES
                  (:uk, :work_date, :client, :hours, :hourly_rate, :notes)
                """
            ),
            {
                "uk": owner_key,
                "work_date": work_date,
                "client": client,
                "hours": hours,
                "hourly_rate": rate,
                "notes": notes or None,
            },
        )

    return redirect(url_for("core.freelance"))


@core_bp.post("/freelance/delete_entry/<int:entry_id>")
def freelance_delete_entry(entry_id: int):
    owner_key = _personal_value("profile_name") or session.get("user_key")
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM freelance_entries WHERE id = :id AND owner_key = :uk"),
            {"id": entry_id, "uk": owner_key},
        )
    return redirect(url_for("core.freelance"))


@core_bp.route("/household", methods=["GET", "POST"])
def household():
    user_key = _require_user_key()

    if request.method == "POST":
        action = (request.form.get("action") or "").strip()

        if action == "create":
            invite_code = secrets.token_urlsafe(6)

            with engine.begin() as conn:
                row = conn.execute(
                    text("INSERT INTO households (invite_code) VALUES (:c) RETURNING id"),
                    {"c": invite_code},
                ).mappings().first()

                household_id = row["id"] if row else None

                if household_id is None:
                    household_id = conn.execute(text("SELECT last_insert_rowid() AS id")).mappings().first()["id"]

                conn.execute(
                    text("""
                        INSERT INTO household_members (household_id, user_key, display_name)
                        SELECT :hid, :uk, :dn WHERE NOT EXISTS (
                            SELECT 1 FROM household_members WHERE household_id = :hid AND user_key = :uk
                        )
                    """),
                    {"hid": household_id, "uk": user_key, "dn": (_personal_value("display_name", session.get("username")) or "Me")},
                )

            session["household_id"] = int(household_id)
            session["household_invite_code"] = invite_code
            return redirect(url_for("core.expenses"))

        if action == "join":
            code = (request.form.get("invite_code") or "").strip()
            if not code:
                return redirect(url_for("core.household"))

            with engine.begin() as conn:
                hh = conn.execute(
                    text("SELECT id, invite_code FROM households WHERE invite_code = :c"),
                    {"c": code},
                ).mappings().first()

                if not hh:
                    return redirect(url_for("core.household"))

                household_id = int(hh["id"])

                conn.execute(
                    text("""
                        INSERT INTO household_members (household_id, user_key, display_name)
                        SELECT :hid, :uk, :dn WHERE NOT EXISTS (
                            SELECT 1 FROM household_members WHERE household_id = :hid AND user_key = :uk
                        )
                    """),
                    {"hid": household_id, "uk": user_key, "dn": (_personal_value("display_name", session.get("username")) or "Me")},
                )

            session["household_id"] = household_id
            session["household_invite_code"] = code
            return redirect(url_for("core.expenses"))

    invite_code = session.get("household_invite_code")
    return render_template("household.html", household_id=_current_household_id(), invite_code=invite_code)


# ----------------------------
# UI toggles
# ----------------------------
@core_bp.route("/set_currency", methods=["POST"])
def set_currency():
    c = request.form.get("currency", DEFAULT_CURRENCY)
    session["currency"] = c if c in ALLOWED_CURRENCIES else DEFAULT_CURRENCY
    return redirect(request.referrer or url_for("core.calculator"))


@core_bp.route("/set_perspective", methods=["POST"])
def set_perspective():
    allowed = {"river", "leslie", "edie"}
    p = request.form.get("perspective", "leslie")
    session["perspective"] = p if p in allowed else "leslie"
    return redirect(request.referrer or url_for("core.calculator"))
