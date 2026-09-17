#!/usr/bin/env python3
"""Every figure the hand-written surfaces carry, and which of them have gone stale.

`data.json` is the build, and `index.html` is generated from it, so the page cannot disagree with
it. Three surfaces are written by hand and quote figures derived from the same run:

    README.md                      the first screen a judge reads
    docs/brand/readme-banner.svg   the image at the top of it
    docs/architecture.svg          the diagram further down

The claim-consistency sweep asserts those figures are *present*. That is not the same as asserting
they are *current*, and the difference is not academic. When this module was written the committed
README said `1.479` where its own committed `data.json` said `1.478`, and `1.53%` where the same file
said `1.54%`. Every check in the workspace passed, because every check in the workspace was asking
whether the string was there rather than whether it was right.

That is the failure this entry is about, committed by the entry itself: a hand-maintained copy of a
number that moves, drifting away from the thing it copies, with nothing able to notice. So the copies
are checked here rather than trusted.

Two callers, and the difference between them is the whole design:

  * `scripts/fetch.py` calls this over the run it has just made, for the two diagrams only. The
    diagrams carry rounded figures on purpose, so a run-to-run wobble does not fail them; only a
    figure that has genuinely moved does.
  * `scripts/build_page.py` calls this over `data.json` as written, the README included. The README
    quotes exact figures for a named build, so it is checked against that build and not against
    whatever the endpoints said a minute later. This is the gate that stops a stale README shipping.
    It is deliberately not part of the fetch: comparing the README against a fresh fetch is circular,
    because the fetch is the thing that moves it.

Only figures that *move* are listed. A count such as 927 cannot go stale without the code that
produces it changing, and the sweep covers those already. A money total or an exchange rate can go
stale overnight with nothing else changing, and that is the case worth a check.
"""

import json
import os
import re
from decimal import ROUND_HALF_UP, Decimal

SURFACES = {
    "banner": "docs/brand/readme-banner.svg",
    "architecture": "docs/architecture.svg",
    "readme": "README.md",
}


def _usd(value):
    """A dollar figure as the README and the page print it, to the cent."""
    return "${:,.0f}".format(Decimal(value))


def _bn(value):
    """A dollar total in billions, to two places, as the README prints it."""
    return "$%.2fbn" % (Decimal(value) / Decimal(1000000000))


def figures(data):
    """The figure strings each hand-written surface should carry, from a build.

    Returns `{surface: [string, ...]}`. Every string is compared with `in`, so it has to appear
    verbatim; the strings are chosen to be specific enough that a match means the figure is right
    rather than that the number happens to appear somewhere else on the page.
    """
    money = data["money"]
    recon = data["reconciliation"]
    cur = data["currency"]
    mints = data["mints"]
    timing = data["activation_timing"]

    gross = Decimal(money["gross_usd"])
    withheld = Decimal(money["withheld_usd"])
    net = Decimal(money["net_usd"])
    withheld_pct = withheld / gross * 100
    reinvested_pct = net / gross * 100

    # A FTSE share is quoted in pence and the feed carries the figure through as though it were
    # pounds, so the published price sits at 100 divided by the GBP rate. The same number for every
    # one of the pence-priced listings, which is why it is one ratio and not a range.
    pence_ratio = Decimal(100) / Decimal(str(cur["usd_per"]["GBP"]))

    # Rounded half up with a Decimal rather than through a float, because the paths disagree at a
    # boundary and this figure sits on one. Measured, not assumed: 0.01535 prints as 1.54 through a
    # float and as 1.53 through `%f` on a Decimal, and 0.02675 prints as 2.67 through a float where
    # half up gives 2.68. The committed README carried 1.53%, which is the second of those paths.
    # The README is the only surface that carries this figure, so the rule is written out here
    # rather than inherited from whichever representation happened to be in hand.
    pence_share = (Decimal(str(cur["pence_share_of_book"])) * 100).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP)

    # The diagrams are posters: the withheld total is rounded to the nearest hundred thousand,
    # because the per-symbol totals are rounded before they are summed and the headline therefore
    # moves by a dollar between two runs on the same day. A figure that moves by a dollar cannot be
    # pinned at full precision, and a check that can never converge is not a check.
    banner = [
        "%d mints read on chain" % mints["total"],
        "$%.1fm withheld in %s months" % (withheld / Decimal(1000000), data["actions"]["months"]),
        "%d of %d mints stale" % (mints["live_differs_from_base"], mints["total"]),
        "%.1f%% reinvested" % reinvested_pct,
        "%.1f%% withheld" % withheld_pct,
    ]

    architecture = [
        "on all %d mints" % mints["total"],
        "%d of %d mints differ here" % (mints["live_differs_from_base"], mints["total"]),
    ]

    readme = [
        # The headline, and the annual figure beside it.
        "%s withheld" % _usd(withheld),
        "about %s a year" % _usd(money["annualised_withheld_usd"]),
        # The read rule, which is the claim the entry rests on, and its control.
        "%d agreed, %d disagreed, %d published nothing to compare against"
        % (data["read_rule"]["agreed"], data["read_rule"]["disagreed"], data["read_rule"]["no_value"]),
        "%.3f" % recon["net_over_market"],
        "%.3f" % recon["gross_over_market"],
        # The age gradient: the summary pair, then the table it comes from.
        "%.1f%% within ten days against %.1f%% beyond sixty"
        % (recon["fresh_median"] * 100, recon["stale_median"] * 100),
        # The currency finding: the two totals, the pence ratio and the slice of the book.
        _bn(cur["market_value_as_read_usd"]),
        _bn(cur["market_value_converted_usd"]),
        "%.1f times" % pence_ratio,
        "%s%% of the book" % pence_share,
        "%s%% of the converted book" % pence_share,
        "%.2f times too large" % cur["read_over_converted"],
        cur["fx_date"],
        # The window, and what is in it.
        "%s of the %s gross is dated on or before today"
        % (_usd(money["paid_gross_usd"]), _usd(money["gross_usd"])),
        "%s is still forward" % _usd(money["forward_gross_usd"]),
        # The activation timing.
        "%d activations" % timing["n"],
        "%d of them take effect on a day" % timing["on_a_non_trading_day"],
    ]
    for bucket in recon["buckets"]:
        readme.append("%s | %d | %.1f%%"
                      % (bucket["label"].replace("-", " to "), bucket["n"], bucket["median"] * 100))

    return {"banner": banner, "architecture": architecture, "readme": readme}


def stale_banner_example(data, root):
    """Complaints about the worked example on the banner, if it is not a pair this build holds.

    The banner's receipt shows one mint's two candidate values to ten decimals. Which mint is not
    recorded anywhere, so the check is that the pair shown is a pair this build actually contains,
    rather than that it is a particular one: a pair that has stopped existing in the build is the
    failure worth catching, and pinning an index would fail on a reordering that changed nothing.

    Ten decimals is not decoration. The two values differ in the third, so the point of the receipt
    is that the difference is small, real, and invisible to anyone reading the field that looks
    authoritative. A pair rounded to three places would not make that point.
    """
    with open(os.path.join(root, SURFACES["banner"])) as handle:
        text = handle.read()
    shown = set(re.findall(r"\b1\.\d{10}\b", text))
    if not shown:
        return ["%s shows no ten-decimal value, so the receipt has lost its worked example"
                % SURFACES["banner"]]
    in_build = set()
    for token in data["tokens"]:
        for key in ("base_multiplier", "live_multiplier"):
            if token.get(key) is not None:
                in_build.add("%.10f" % token[key])
    return ["%s shows %s, which is not a multiplier in this build"
            % (SURFACES["banner"], value) for value in sorted(shown - in_build)]


def stale(data, root, surfaces=("banner", "architecture", "readme")):
    """Complaints about hand-written surfaces that no longer carry this build's figures.

    Empty when every figure on every requested surface matches. `surfaces` is a parameter so the
    fetch can check the diagrams without the README, which it cannot check without being circular.
    """
    wanted = figures(data)
    complaints = []
    for name in surfaces:
        path = os.path.join(root, SURFACES[name])
        with open(path) as handle:
            text = handle.read()
        for want in wanted[name]:
            if want not in text:
                complaints.append("%s should say %r" % (SURFACES[name], want))
    return complaints


def load(root):
    """`data.json` as written, for the callers that check against the build rather than a fetch."""
    with open(os.path.join(root, "data.json")) as handle:
        return json.load(handle)
