#!/usr/bin/env python3
"""Record Date: turn data.json into index.html.

    python3 scripts/fetch.py && python3 scripts/build_page.py

Numbers only. The markup lives in scripts/page_template.html so the design can be changed
without going near the arithmetic, and every figure is written into the HTML as its resting
state: the page is complete and correct with scripting off, and the script only animates.
"""

import html
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TEMPLATE = os.path.join(HERE, "page_template.html")


def check_assets(page):
    """Refuse to ship a page that links something broken.

    Three failures this catches, all of which happened. A relative href that resolves to nothing
    is a 404 for the reader. An .svg containing a named HTML entity such as &ndash; is not valid
    XML, so the browser shows a parse error instead of the diagram, and the file looks fine in
    the editor. And an .svg containing <br/> renders as nothing, because SVG has no such element,
    so the two halves of the sentence run together with no space.
    """
    problems = []
    for match in re.finditer(r'href="([^"#][^"]*)"', page):
        target = match.group(1)
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        if not os.path.exists(os.path.join(ROOT, target)):
            problems.append("relative link resolves to nothing: %s" % target)

    for base, _, names in os.walk(os.path.join(ROOT, "docs")):
        for name in sorted(names):
            if not name.endswith(".svg"):
                continue
            path = os.path.join(base, name)
            with open(path) as handle:
                text = handle.read()
            try:
                ET.fromstring(text)
            except ET.ParseError as exc:
                problems.append("%s is not valid XML: %s"
                                % (os.path.relpath(path, ROOT), exc))
            if re.search(r"<\s*br\s*/?\s*>", text):
                problems.append("%s contains <br/>, which SVG ignores, so the line runs on"
                                % os.path.relpath(path, ROOT))
    if problems:
        raise SystemExit("asset problems:\n  " + "\n  ".join(problems))
    return len(problems)


def money(value):
    return "{:,}".format(int(Decimal(str(value)).to_integral_value()))


def short_money(value):
    """A headline figure, rounded the way a person would say it."""
    n = Decimal(str(value))
    if n >= 1_000_000:
        return "${:.1f}m".format(n / Decimal(1_000_000))
    if n >= 1_000:
        return "${:.0f}k".format(n / Decimal(1_000))
    return "${:.0f}".format(n)


def pct(value, places=1):
    return "{:.{p}f}".format(float(value) * 100, p=places)


def esc(text):
    return html.escape(str(text), quote=True)


def multiplier(value):
    """A multiplier at ten places.

    The mint stores an f64, so the exact value has seventeen significant digits. Printing all of
    them is false precision: no reader can use the difference between 1.0268028384810615 and
    1.0268028385, and the long string makes the number look arbitrary. Ten places is more than
    the reconciliation needs to reproduce its own result.
    """
    return trim(repr(float(value)), 10)


def timing_slots(timing):
    """The four modal activation times, each labelled with the New York clock.

    The label is what makes the block land: a reader who knows when the US market trades can see
    the problem from `23:55 -> 19:55 ET` without doing arithmetic. Eastern time is computed as
    UTC-4, which is daylight time and applies to every activation in the window this build reads.
    """
    slots = []
    for clock, count in timing.get("top_times", []):
        hh, mm = int(clock[:2]), int(clock[3:])
        et = hh * 60 + mm - 4 * 60
        if et < 0:
            et += 24 * 60
            when = "%02d:%02d ET the previous day" % (et // 60, et % 60)
        else:
            when = "%02d:%02d ET, after the close" % (et // 60, et % 60)
        slots.append(
            '<div class="slot off"><b>{clock}</b><i>{count} activations</i>'
            "<u>{when}</u></div>".format(clock=esc(clock), count=count, when=esc(when))
        )
    return "\n".join(slots)


def recon_rows(rows):
    """The reconciliation, as rows: the arithmetic and the gap against the market.

    The gap is coloured, and it is larger further down the table because those activations are
    older. That gradient is the point of the section, so the rows are ordered by age and nothing
    is selected for looking good.
    """
    out = []
    for r in rows:
        err = r["error"]
        cls = "good" if abs(err) < 0.05 else ("mid" if abs(err) < 0.15 else "off")
        out.append(
            '<tr>'
            '<td class="sym">{sym}</td>'
            '<td class="m">{when}<span class="age">{age}d</span></td>'
            '<td class="m">{step}</td>'
            '<td class="m">{net}</td>'
            '<td class="imp">${imp}</td>'
            '<td class="m">${mkt}</td>'
            '<td class="m {cls}">{err}</td>'
            "</tr>".format(
                sym=esc(r["symbol"]),
                when=esc(r["activated"]),
                age=esc(str(r["age_days"])),
                step=esc(trim(r["step"], 10)),
                net=esc(r["net_per_unit"]),
                imp=esc(r["implied_price"]),
                mkt=esc(r["market_price"]),
                err=esc("%+.1f%%" % (err * 100)),
                cls=cls,
            )
        )
    return "\n".join(out)


def bucket_rows(buckets):
    """Median gap by age of activation. This is the evidence, not the eight rows above it."""
    widest = max((b["median"] for b in buckets), default=1) or 1
    out = []
    for b in buckets:
        pct = b["median"] * 100
        out.append(
            '<tr>'
            '<td class="m">{label}</td>'
            '<td class="m">{n}</td>'
            '<td class="m"><span class="dwrap"><i class="dbar" style="width:{w:.1f}%"></i>'
            '</span><span class="dpct">{pct:.1f}%</span></td>'
            "</tr>".format(
                label=esc(b["label"]),
                n=esc(str(b["n"])),
                w=min(100.0, b["median"] / widest * 100),
                pct=pct,
            )
        )
    return "\n".join(out)


def token_cards(tokens, limit=30):
    top = tokens[:limit]
    biggest = max(Decimal(t["withheld_usd"]) for t in top) if top else Decimal(1)
    out = []
    for t in top:
        withheld = Decimal(t["withheld_usd"])
        bar = float(withheld / biggest * 100) if biggest else 0.0
        price = t.get("price")
        out.append(
            '<li title="{mint}">'
            '<span class="lg-top"><b>{sym}</b><em>{events} event{plural}</em></span>'
            '<span class="lg-row"><span>withheld</span><b class="taken">${wh}</b></span>'
            '<span class="lg-row"><span>per unit</span><b>{pu}</b></span>'
            '<span class="lg-row"><span>multiplier</span><b>{mult}</b></span>'
            '<span class="lg-row"><span>price</span><b>{price}</b></span>'
            '<span class="lg-bar"><i data-w="{bar:.1f}" style="width:{bar:.1f}%"></i></span>'
            "</li>".format(
                mint=esc(t["mint"]),
                sym=esc(t["symbol"]),
                events=t["events"],
                plural="" if int(t["events"]) == 1 else "s",
                wh=money(withheld),
                pu=esc(trim(t["withheld_per_unit"])),
                mult=esc(multiplier(t["live_multiplier"])),
                price=("${:,.2f}".format(price) if price else "no quote"),
                bar=bar,
            )
        )
    return "\n".join(out)


def trim(value, places=6):
    """A per-unit figure without the float tail the API carries.

    The API hands back f64 values as 17 significant digits, which is not information: a reader
    cannot use the difference between 1.0216625054701978 and 1.0216625055. Six places for a
    per-unit cash amount, ten for a multiplier step, which is the precision the identity needs.
    """
    text = str(value)
    if "." in text:
        whole, frac = text.split(".", 1)
        frac = frac[:places].rstrip("0")
        return whole + ("." + frac if frac else "")
    return text


INSTRUCTIONS = [
    ("init_registry", "Once per deployment. Holds the authority and the two counters."),
    ("register_mint", "Reads an xStock mint and stores the live multiplier alongside the value "
                      "in the field the mint calls multiplier, so the gap between them is "
                      "auditable."),
    ("record_activation", "Permissionless. If the multiplier has moved, writes a Receipt: "
                          "previous, new, the issuer's own timestamp, the supply. Ordered by "
                          "sequence and never edited."),
    ("read_entitlement", "Raw units in the units a wallet should display. Return data is a "
                         "little-endian u128. Integer arithmetic throughout."),
    ("activation_pending", "One byte: has the multiplier moved since this program last looked? "
                           "A cranker calls this first so it does not pay for a transaction "
                           "that reverts."),
    ("settlement_window", "Seconds since the last activation. A protocol that wants to follow "
                          "the issuer's advice to pause refuses to settle while that is inside "
                          "900."),
]


def instruction_cards():
    return "\n".join(
        '<div><b>{name}</b><p>{text}</p></div>'.format(name=esc(name), text=esc(text))
        for name, text in INSTRUCTIONS
    )


def check_cards(checks):
    tick = ('<span class="tick"><svg width="10" height="10" viewBox="0 0 10 10" fill="none">'
            '<path d="M1.6 5.2l2.2 2.2 4.6-4.9" stroke="#fff" stroke-width="1.6" '
            'stroke-linecap="round" stroke-linejoin="round"/></svg></span>')
    return "\n".join(
        '<div class="check">{tick}<div><b>{name}</b><p><em>{detail}</em></p></div></div>'.format(
            tick=tick, name=esc(c["name"]), detail=esc(c["detail"])
        )
        for c in checks
    )


def source_rows(sources):
    labels = {
        "assets": "mint list",
        "actions": "corporate actions",
        "multiplier": "multiplier history",
        "chain": "mint accounts",
        "prices": "prices",
        "docs": "published mechanics",
    }
    order = ["assets", "actions", "multiplier", "chain", "prices", "docs"]
    return "\n".join(
        "<div><b>{label}</b><code>{url}</code></div>".format(
            label=esc(labels.get(key, key)), url=esc(sources[key])
        )
        for key in order
        if key in sources
    )


def render(data):
    with open(TEMPLATE) as handle:
        page = handle.read()

    mints = data["mints"]
    read_rule = data["read_rule"]
    with_holding = data["withholding"]
    money_block = data["money"]
    actions = data["actions"]
    recon = data["reconciliation"]
    timing = data["activation_timing"]

    # The other rates, so the page does not hardcode "twenty at 0%". JSON turns the numeric keys
    # into strings, hence the float round trip.
    def rate_count(target):
        for key, value in with_holding["by_rate"].items():
            if abs(float(key) - target) < 1e-9:
                return value
        return 0

    gross = Decimal(money_block["gross_usd"])
    net = Decimal(money_block["net_usd"])
    withheld = Decimal(money_block["withheld_usd"])
    keep = float(net / gross * 100)
    take = float(withheld / gross * 100)

    example = next(
        (t for t in data["tokens"] if t["symbol"] == "PEPx" and t["base_multiplier"] != t["live_multiplier"]),
        next((t for t in data["tokens"] if t["base_multiplier"] != t["live_multiplier"]), None),
    )
    if example is None:
        raise SystemExit("no token with a moved multiplier; nothing to show as the example")
    effective = example.get("effective_at") or 0

    values = {
        "BUILT": data["built"],
        "MINT_TOTAL": str(mints["total"]),
        "MINT_WITH_EVENT": str(mints["with_event"]),
        "MINT_NO_EVENT": str(mints["no_event"]),
        "MINT_LIVE_DIFFERS": str(mints["live_differs_from_base"]),
        "READ_RULE_AGREED": str(read_rule["agreed"]),
        "READ_RULE_DISAGREED": str(read_rule["disagreed"]),
        "READ_RULE_NO_VALUE": str(read_rule["no_value"]),
        "TIMING_N": str(timing["n"]),
        # The UTC window, which is the loose bound.
        "TIMING_OUTSIDE": str(timing["outside"]),
        # `pct` takes a fraction and does the scaling, so passing a percentage here double-counts
        # it. It did, and the page read "9808.0% outside US trading hours".
        "TIMING_OUTSIDE_PCT": pct(1 - timing["inside_share"]),
        # The exchange's own calendar, which is the authoritative definition. Both are printed:
        # two independent measurements that agree are worth more than one stated alone, and if
        # they ever diverge the reader sees it rather than reading only the flattering one.
        "TIMING_INSIDE_CALENDAR": str(timing["inside_calendar"]),
        "TIMING_OUTSIDE_CALENDAR": str(timing["outside_calendar"]),
        "TIMING_OUTSIDE_CALENDAR_PCT": pct(1 - timing["inside_calendar_share"]),
        "TIMING_OUTSIDE_HOURS_TRADING_DAY": str(timing["outside_hours_on_a_trading_day"]),
        "TIMING_ON_A_NON_TRADING_DAY": str(timing["on_a_non_trading_day"]),
        "TIMING_TZ": esc(timing["timezone"]),
        "TIMING_SESSION": esc(timing["session"]),
        "TIMING_OVERRIDES": str(timing["overrides"]),
        "TIMING_CALENDAR_FEEDS": str(timing["feeds_on_calendar"]),
        "TIMING_USD_FEEDS": str(timing["usd_equity_feeds"]),
        # The share of the sample sitting on the four modal minutes, so the block cannot claim
        # "most activations land here" without saying how much of the field that actually is.
        "TIMING_TOP_SHARE": pct(
            sum(count for _, count in timing["top_times"]) / timing["n"]
        ),
        "TIMING_SLOTS": timing_slots(timing),
        "RATED_SYMBOLS": str(with_holding["rated_symbols"]),
        "TOP_RATE_SYMBOLS": str(with_holding["top_rate_symbols"]),
        "TOP_RATE_PCT": pct(with_holding["top_rate_share"]),
        "TOP_RATE": "%d" % round(float(with_holding["top_rate"]) * 100),
        "ZERO_RATE_SYMBOLS": str(rate_count(0.0)),
        "FIVE_RATE_SYMBOLS": str(rate_count(0.05)),
        "CA_TOTAL": str(actions["total"]),
        # Not `by_status["Scheduled"]`, which counts every action type: 551. The sentence this
        # fills is about scheduled cash dividends, of which there are 537 distinct events.
        "CA_SCHEDULED": str(actions.get("cash_events", 0)),
        "CA_CANCELLED": str(actions["by_status"].get("Cancelled", 0)),
        "RECONCILED": str(with_holding["records_reconciled"]),
        "CHECKED": str(with_holding["records_checked"]),
        "MONTHS": str(actions["months"]),
        "WINDOW_FROM": (actions["window_from"] or "")[:10],
        "WINDOW_TO": (actions["window_to"] or "")[:10],
        "MARKET_VALUE": money(money_block["market_value_usd"]),
        "PAYING_SYMBOLS": str(money_block["paying_symbols"]),
        "GROSS": money(gross),
        "NET": money(net),
        "WITHHELD": money(withheld),
        "PAID_GROSS": money(money_block["paid_gross_usd"]),
        "FORWARD_GROSS": money(money_block["forward_gross_usd"]),
        "ANNUAL_WITHHELD": money(money_block["annualised_withheld_usd"]),
        "ANNUAL_WITHHELD_SHORT": short_money(money_block["annualised_withheld_usd"]),
        "WITHHELD_SHARE": pct(money_block["withheld_share_of_gross"]),
        "IMPLIED_YIELD": "{:.2f}".format(float(money_block["implied_gross_yield"]) * 100),
        "KEEP_PCT": "{:.3f}".format(keep),
        "TAKE_PCT": "{:.3f}".format(take),
        "KEEP_SHARE": "{:.1f}".format(keep),
        "TAKE_SHARE": "{:.1f}".format(take),
        "YIELD_SYMBOLS": str(money_block.get("yield_symbols", 0)),
        "EXAMPLE_BASE": esc(multiplier(example["base_multiplier"])),
        "EXAMPLE_LIVE": esc(multiplier(example["live_multiplier"])),
        "EXAMPLE_EFFECTIVE_AT": "unix %d" % int(effective) if effective else "never dated",
        "RECON_ROWS": recon_rows(recon["illustrated"]),
        "BUCKET_ROWS": bucket_rows(recon["buckets"]),
        "RECON_TOTAL": str(recon["total"]),
        "RECON_NAMES": str(recon["names"]),
        "RECON_FRESH": "{:.1f}".format(recon["fresh_median"] * 100),
        "RECON_FRESH_N": str(recon["fresh_n"]),
        "RECON_STALE": "{:.1f}".format(recon["stale_median"] * 100),
        "RECON_STALE_N": str(recon["stale_n"]),
        "RECON_MEDIAN": "{:.1f}".format(recon["median_error"] * 100),
        "TOKEN_CARDS": token_cards(data["tokens"]),
        "INSTRUCTION_CARDS": instruction_cards(),
        "CHECKS": check_cards(data["checks"]),
        "SOURCES": source_rows(data["sources"]),
    }

    for key, value in values.items():
        page = page.replace("{{%s}}" % key, value)

    left = re.findall(r"\{\{[A-Z_]+\}\}", page)
    if left:
        raise SystemExit("template placeholders left unfilled: %s" % ", ".join(sorted(set(left))))

    check_assets(page)

    out = os.path.join(ROOT, "index.html")
    with open(out, "w") as handle:
        handle.write(page)

    non_ascii = sum(1 for ch in page if ord(ch) > 127)
    print("wrote index.html  %d bytes  %d lines  %d non-ascii characters"
          % (len(page.encode()), page.count("\n") + 1, non_ascii))
    print("  %d ledger cards, %d reconciliation rows over %d names, %d age buckets, %d checks"
          % (min(30, len(data["tokens"])), recon["total"], recon["names"],
             len(recon["buckets"]), len(data["checks"])))
    return 0


if __name__ == "__main__":
    with open(os.path.join(ROOT, "data.json")) as handle:
        sys.exit(render(json.load(handle)))
