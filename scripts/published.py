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

Two callers, and the difference between them is deliberate:

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

There is a third failure, and it is not the same as either of the two above. A surface can be
*current* and still disagree with the surface beside it, if the two print the same figure by
different rules. The banner carried the worked example's two multipliers rounded while the page cut
them short, so the same pair from one build read two ways on two surfaces a judge reads in sequence,
and every check passed. `page_agrees_with_banner` is that check, and `to_places` is the single
definition of how a figure is printed, so the two cannot drift apart again.
"""

import datetime
import json
import os
import re
from decimal import ROUND_HALF_UP, Decimal

SURFACES = {
    "banner": "docs/brand/readme-banner.svg",
    "architecture": "docs/architecture.svg",
    "readme": "README.md",
}

# Written by `scripts/deploy.sh` as it makes the comparison, and quoted by the README.
DEPLOY_BLOCK = "docs/deploy.txt"


def _usd(value):
    """A dollar figure as the README and the page print it, to the cent."""
    return "${:,.0f}".format(Decimal(value))


def _bn(value):
    """A dollar total in billions, to two places, as the README prints it."""
    return "$%.2fbn" % (Decimal(value) / Decimal(1000000000))


def _millions(value):
    """A dollar total in millions, to one place, as the video's upload description prints it.

    The description is the one surface that rounds this figure to three significant figures
    (`$11.6m`) where the README prints it to the cent (`$11,588,741`), and it was derived by nothing
    until 20 September 2026. Added beside `_bn` rather than written into the check as a literal,
    because the string a reader sees and the string the check looks for have to come from one place.
    """
    return "$%.1fm" % (Decimal(value) / Decimal(1000000))


def median_activation_age(data):
    """The median age in days of the activation behind each priced token, or None.

    The README's third contribution is a duration: *370 of the 379 tokens priced here carry a past
    activation timestamp, median age 26.7 days*. It was the one figure on the first screen that no
    check covered, and the cause is the shape of the number rather than an oversight in the
    scanner: it carries one decimal, and every class in the absence rule below is money, a
    percentage, an `N of M` pair, or a ratio of three decimals and up. `fetch.py` computes the
    figure and says in a comment that this module checks the README against it. This module did
    not, and the key `fetch.py` writes for it is absent from the committed `data.json`, so nothing
    checked it at all. That is the failure this entry documents, sitting in the sentence about the
    mint discarding its own history, and it is the reason this function exists.

    Derived here rather than read from a stored field, and from `tokens` rather than from `mints`,
    because the sentence names its population: the priced tokens. `read_mint` reports `effective_at`
    as 0 unless the dated value has taken effect, so a truthy one is exactly *this mint has an
    activation behind it*, and the nine zeros are the tokens the sentence's denominator counts and
    its median excludes. Measured on the committed build: 370 of the 379, median 26.68 days.

    `now` is the build's own capture time, not the clock. The figure is a difference between a
    timestamp and the moment of the read, so it grows by a day for every day the build sits still;
    taking it from `datetime.now()` would make the README fail the morning after any capture, which
    is a check reporting the calendar rather than the entry. From `built_utc` it is reproducible
    from the frozen build, which is what `build_page.py` checks against.

    The middle element by index rather than `statistics.median`, because that is the definition
    `fetch.py` already uses and two definitions of one figure is how this file's own docstring says
    two surfaces came to disagree.

    None rather than a guess when the build cannot answer, so the caller can report that instead of
    passing quietly. `test_checks.py` drives this over a fixture that carries neither field, and a
    check that drops its own requirement when the data goes missing is the failure class this whole
    module is written against.
    """
    built = data.get("built_utc")
    if not built:
        return None
    try:
        moment = datetime.datetime.fromisoformat(str(built).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None
    ages = []
    for token in data.get("tokens") or []:
        when = token.get("effective_at")
        if not when:
            continue
        try:
            ages.append((moment - float(when)) / 86400.0)
        except (TypeError, ValueError):
            continue
    if not ages:
        return None
    ages.sort()
    return ages[len(ages) // 2]


def to_places(value, places):
    """`value` at `places` decimals, rounded rather than cut short.

    One definition, used by the page and by the check on the banner, because the two had their own
    and they disagreed. The banner printed the worked example's two multipliers rounded and the page
    printed them truncated, so the same pair from the same build read 1.0344000942 / 1.0268028385 on
    the banner and 1.0344000941 / 1.0268028384 in the page's receipt and its ledger. Two surfaces a
    judge reads one after the other, quoting one build, differing in the last digit. That is the
    failure this entry documents, committed by the entry, and nothing caught it: the banner check
    asked whether its pair was *a* pair in the build, and every other check asked whether a figure
    was present rather than whether two surfaces agreed about it.

    Rounded, not truncated, because rounding is what both docstrings already promised and what
    `"%.10f"` does by default, so it is the behaviour a later change will assume. Half up on the
    decimal string rather than through a float, which is the rule `figures` already applies to the
    pence share, and it is a decision rather than an inheritance. Measured on this build: 757 of the
    758 multipliers round the same either way, and one does not. ROLx's live multiplier is stored as
    1.00338500264999996858250597142614424228668212890625, whose shortest round-tripping decimal is
    1.00338500265, so half up on that decimal gives 1.0033850027 while rounding the stored binary
    gives 1.0033850026. The two differ by 3.1e-17, and the convention here is that the value the API
    reported is the number, the same way the pence share is treated. Ten places is already far past
    what the reconciliation identity needs, so the rule being stated and uniform is what matters,
    not which side of a tie that far down the tail it lands on.

    Trailing zeros are dropped, so a multiplier of exactly 1 prints as `1` rather than
    `1.0000000000`. The page is a poster and the tail is not information.
    """
    text = str(value)
    if "." not in text:
        return text
    quantised = Decimal(text).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    if not quantised:
        # A negative zero is not a figure anyone wants to read.
        return "0"
    return format(quantised.normalize(), "f")


def multiplier(value):
    """A multiplier at ten places, which is the precision the reconciliation identity needs.

    The mint stores an f64, so the exact value has seventeen significant digits. Printing all of
    them is false precision: no reader can use the difference between 1.0268028384810615 and
    1.0268028385, and the long string makes the number look arbitrary.
    """
    return to_places(value, 10)


def pct(value, places=1):
    """A fraction as a percentage string, the one way this build prints one.

    Lived in `build_page.py` until 18 September 2026, when the video script's on-screen figures needed
    the same card value the page renders. Two copies of a formatter is how the page and the banner
    came to disagree about the same multiplier to the last digit, so the card's formatter is shared
    rather than repeated: `build_page.py` imports this, and the check on the video script calls it,
    and a change to either cannot move one without the other.

    `"{:.1f}"` rounds half to even on the binary value, which is why `0.9425` prints as `94.2` and not
    `94.3`. That is the page's existing behaviour and it is preserved deliberately rather than fixed,
    because the figure is already published at that precision.
    """
    return "{:.{p}f}".format(float(value) * 100, p=places)


def worked_example(data):
    """The token whose two multipliers the page shows as its worked example.

    PEPx if it has moved, otherwise the first token that has. Named here rather than in `build_page.py`
    for the same reason as `pct`: the video script's mechanism beat points the camera at the page's
    two-value card and quotes the pair, so the selection is a shared fact about the build and not a
    detail of one renderer.
    """
    return next(
        (t for t in data["tokens"]
         if t["symbol"] == "PEPx" and t["base_multiplier"] != t["live_multiplier"]),
        next((t for t in data["tokens"] if t["base_multiplier"] != t["live_multiplier"]), None),
    )


# The mint the README's proof table is read from. One named mint rather than "the first that has
# moved", because the table names it and the desk opens on it, so a reordering of the build must not
# silently swap the company the sentence is about.
PROOF_SYMBOL = "XOMx"


def proof_token(data):
    """The README's proof mint, or None when the build does not carry it."""
    return next((t for t in data.get("tokens") or [] if t.get("symbol") == PROOF_SYMBOL), None)


def proof_figures(token):
    """The exact strings the README's proof table carries for `token`, all derived from the build."""
    base, live = token["base_multiplier"], token["live_multiplier"]
    when = datetime.datetime.fromtimestamp(token["effective_at"], datetime.timezone.utc)
    return [
        "| the field named `multiplier` | **%.7f** |" % base,
        "| the live value, in force since %s UTC | **%.7f** |" % (when.strftime("%Y-%m-%d %H:%M"), live),
        "as a wallet reading the obvious field shows it | **%.2f** |" % (100 * base),
        "as the mint actually holds it | **%.2f** |" % (100 * live),
        "$%s a share paid gross and $%s reinvested" % (token["gross_per_unit"], token["net_per_unit"]),
        "**$%s a share withheld at 30%%**" % token["withheld_per_unit"],
        "%s across the float" % _usd(token["withheld_usd"]),
    ]


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
        # The first bold line's own words, pinned exactly. The absence rule below cannot reach
        # these three: a withholding rate, a rated pair and a session count each appear elsewhere
        # in the build carrying a different meaning, so a sentence reading "370 of the 400 lose
        # 26.2%" built clean while being false. 26.2% is withheld over gross, and 628 is the
        # UTC-window count sitting one key away from the calendar one in the same object. This is
        # the headline claim of the whole entry, and until now it was the one thing on the first
        # screen with no check behind it at all.
        "%d of the %d tokenized stocks" % (data["withholding"]["top_rate_symbols"],
                                           data["withholding"]["rated_symbols"]),
        "lose %d%% of every dividend" % int(Decimal(data["withholding"]["top_rate"]) * 100),
        "%d fall outside the US regular session" % timing["outside_calendar"],
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

    # The median activation age. It is a duration, it carries one decimal, and no class in the
    # absence rule below reaches it, so it was the one figure on the README's first screen with no
    # check behind it at all. Two strings rather than one sentence, so a failure names the half
    # that moved. Asked for only when the build can answer: a `%.1f` on None would raise here and
    # read as a broken check rather than as a missing one, which is what `missing_activation_age`
    # reports instead.
    median_age = median_activation_age(data)
    if median_age is not None:
        readme.extend([
            "%d of the %d tokens priced here carry a past activation timestamp"
            % (sum(1 for token in data["tokens"] if token.get("effective_at")),
               len(data["tokens"])),
            "median age %.1f days" % median_age,
        ])

    # The two price-divergence figures, which the absence rule cannot reach: they are bare integers
    # and the README prints them as `**15**` and `**30**`. Two strings rather than one sentence, so
    # a failure names the half that moved. Asked for only when the build can answer, with
    # `missing_divergence` reporting the case where it cannot, for the reason set out there.
    widest = widest_divergence(data)
    if widest is not None:
        readme.extend([
            "differed by more than 20%% on **%d** mints" % len(data["price_diverged"]),
            "The widest is a factor of **%d**" % widest,
        ])

    # The proof table: one real mint, every cell from the build, so the table cannot drift from it.
    token = proof_token(data)
    if token is not None:
        readme.extend(proof_figures(token))

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
                in_build.add(multiplier(token[key]))
    return ["%s shows %s, which is not a multiplier in this build"
            % (SURFACES["banner"], value) for value in sorted(shown - in_build)]


def page_agrees_with_banner(page, root):
    """Complaints when the page and the banner print the same figure differently.

    The banner carries one mint's two multipliers to ten decimals and the page carries the same pair
    twice, in the receipt and again in the ledger row for that mint. They come from one build, so a
    reader moving from the banner to the page must see the same digits.

    They did not, and this is the check that would have caught it. The banner was written by hand
    from the rounded value while the page truncated its own, so the banner read 1.0344000942 /
    1.0268028385 and the page read 1.0344000941 / 1.0268028384: the banner's two figures appeared
    nowhere in `index.html` at all. Nothing noticed, because every check in the workspace asked
    whether a figure was *present* somewhere rather than whether two surfaces that quote one build
    agree with each other.

    Only ten-decimal figures are compared. They are the ones the two surfaces have in common and the
    only ones precise enough for a last-digit difference to be visible to a reader.
    """
    with open(os.path.join(root, SURFACES["banner"])) as handle:
        banner = handle.read()
    shown = sorted(set(re.findall(r"\b\d+\.\d{10}\b", banner)))
    return ["%s prints %s and index.html does not, so the two surfaces disagree about the same "
            "figure" % (SURFACES["banner"], value) for value in shown if value not in page]


def stale_deploy_block(root):
    """Complaints when the README's deploy block is not the one the last deploy wrote.

    `scripts/deploy.sh` writes the three lines it prints to `docs/deploy.txt`, and the README
    quotes them. It did not always: the block was hand-copied, and by 17 September the README
    carried a built size and a padding count from a build two deploys earlier. The claim beside it
    stayed true the whole time, which is what made it easy to miss, and a judge running `solana
    program dump` would see numbers the README does not carry. The fix is that the figure now comes
    out of the deploy rather than out of the person who ran it.
    """
    path = os.path.join(root, DEPLOY_BLOCK)
    if not os.path.exists(path):
        return ["%s does not exist, so the README's deploy block cannot be checked; run "
                "scripts/deploy.sh" % DEPLOY_BLOCK]
    with open(path) as handle:
        lines = [line.rstrip("\n") for line in handle if line.strip()]
    with open(os.path.join(root, SURFACES["readme"])) as handle:
        readme = handle.read()
    # The first line names the cluster and the time. The three after it are the block, and each is
    # compared verbatim, because a size or a hash that is nearly right is wrong.
    return ["%s should quote the deploy block line %r, which %s carries"
            % (SURFACES["readme"], line, DEPLOY_BLOCK) for line in lines[1:] if line not in readme]


def missing_activation_age(data):
    """Complaints when this build cannot produce the median activation age the README quotes.

    `figures` asks the README for that figure only when the build can answer. That is what keeps
    `test_checks.py`'s fixtures usable, and it is also a way for a check to disappear without saying
    so: take `tokens` or `built_utc` out of a build and the requirement is simply never made, and an
    entry with no check on one of its three headline contributions reports exactly like an entry
    with a check that passes. This module exists because that class of silence is the failure it
    documents, so the silence is turned into a complaint here, the same way `stale_deploy_block`
    reports a missing `docs/deploy.txt` rather than passing it.

    Reported for the README only, because the README is the only surface that carries the figure.
    """
    if median_activation_age(data) is not None:
        return []
    return ["%s quotes a median activation age and this build cannot produce one, so the figure is "
            "unchecked; it needs `built_utc` and a `tokens` list carrying `effective_at`"
            % SURFACES["readme"]]


def widest_divergence(data):
    """How far the pool quote is from the reference on the worst mint, rounded, or None.

    The README's second contribution is the two price fields, and it rests on two bare integers:
    *they differed by more than 20% on 15 mints* and *the widest is a factor of 30*. Neither was
    required of the README and neither was reachable by the absence rule, because both are printed
    as `**15**` and `**30**` with no `$` and no `%`, and those are the only two classes that would
    have caught them. A README that said `**47**` passed every check in this file on 18 September
    2026, which is the same defect as a pool depth no build could produce: a figure on the page
    with nothing behind it.

    The factor is the larger of the two directions, so a pool quote that is far below the reference
    counts as a disagreement rather than as a small number. Rounded to the integer the README
    states: the widest row is 395.01 against 13.355, which is 29.58 and is printed as 30.

    None when the build carries no divergences, so the caller can report that rather than quietly
    making no requirement.
    """
    rows = data.get("price_diverged") or []
    widest = None
    for row in rows:
        try:
            reference = float(row["reference"])
            pool = float(row["pool"])
        except (KeyError, TypeError, ValueError):
            continue
        if reference <= 0 or pool <= 0:
            continue
        factor = max(reference / pool, pool / reference)
        if widest is None or factor > widest:
            widest = factor
    return None if widest is None else int(round(widest))


def missing_divergence(data):
    """Complaints when this build cannot produce the two price-divergence figures.

    The same silence `missing_activation_age` exists to break. `figures` asks for the pair only
    when the build can answer, so a build without `price_diverged` would drop both requirements
    without saying so, and the README's second contribution would be unchecked in a way that looks
    exactly like being checked.
    """
    if widest_divergence(data) is not None:
        return []
    return ["%s quotes a count of mints whose two price fields disagree and the width of the worst, "
            "and this build can produce neither, so both figures are unchecked; they need a "
            "`price_diverged` list carrying `reference` and `pool`" % SURFACES["readme"]]


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
    if "readme" in surfaces:
        complaints.extend(stale_deploy_block(root))
        complaints.extend(missing_activation_age(data))
        complaints.extend(missing_divergence(data))
        with open(os.path.join(root, SURFACES["readme"])) as handle:
            text = handle.read()
        complaints.extend(underivable(data, text))
        complaints.extend(unused_allowances(data, text))
    return complaints


# --- The absence rule ---------------------------------------------------------------------------
#
# Everything above asks whether a surface *carries* the figures this build has. Presence is not
# enough, and the gap is not theoretical: the review of 17 September changed the second `1.033` in
# the README to `1.133`, `631 of the 640, or 98.6%` to `531 of the 640, or 88.6%`, and `377 of the
# 400` to `277 of the 400`, and the page built clean all three times. The first copy of each figure
# was still present, so every check passed while the README said two different things about one
# build.
#
# The rule here is the other direction. Every money token, percentage, `N of M` pair and
# four-decimal ratio on the README has to be derivable from `data.json`, or be on the list below.
# A figure that is in the build is fine however many times it appears; a figure that is not in the
# build fails by name, wherever it sits.
#
# Only five classes are scanned, and each was chosen because it is narrow enough that a match means
# something. Bare integers are not scanned: `434` matches inside a multiplier, which is the trap the
# sweep spec already records. The ratio class is three decimals and up, not four: the README's only
# three-decimal figures are the net, gross and control ratios from `reconciliation`, while two
# decimals are prices and rates, and they are covered by the money and percent classes. Ten-decimal
# multipliers are covered by `page_agrees_with_banner` and `stale_banner_example`.
#
# The duration class was added on 18 September 2026 and it is the one that had been missing. The
# README's third contribution is an age in days, `26.7 days`, which is a single decimal and so was
# reached by none of the other four: not money, not a percentage, not an `N of M` pair, and not a
# ratio of three decimals. The figure is asked for by `figures` now, which catches it moving, and
# that check cannot catch a *second* copy disagreeing with the first, which is the exact failure
# this rule exists for and the one the review of 17 September produced with `1.135`. A class was the
# only way to cover that direction, so it is here rather than left as the one figure on the first
# screen with nothing behind it.
#
# The rule runs both ways, and only the first direction was implemented until 18 September 2026.
# `underivable` asks whether every figure on the README has an explanation; `unused_allowances`
# asks whether every explanation is about a figure that is on the README. The second is not a
# formality. An allow-list entry is itself a claim, and nothing was checking it, so an entry could
# outlive its sentence (`74%`) or describe a different figure from the one it let through
# (`$1.8m`), and the gate would pass either way. Both are the same defect the rest of this file
# exists to catch, one level up: a description of a check that is not a check.

MONEY = re.compile(r"\$[\d,]+(?:\.\d+)?(?:bn|m|k)?")
PERCENT = re.compile(r"\b(?:\d[\d,]*\d|\d)(?:\.\d+)?%")
PAIR = re.compile(r"\b(\d[\d,]*\d|\d) of (?:the )?(\d[\d,]*\d|\d)")
RATIO = re.compile(r"(?<![\d.$])(\d+\.\d{3,})(?![\d%])")
DAYS = re.compile(r"\b(\d+\.\d) days\b")

# Figures about the world rather than about this build, so nothing in `data.json` produces them.
# Each group is named, because an allow-list whose entries cannot be explained is where a wrong
# number goes to hide.
README_ALLOWED = {
    "money": {
        "$9,000": "Tomas's holding, the named user on the first screen. A person's position, not a "
                  "figure from the build.",
        "$500": "the subscription price, stated as policy in the business section. A price this "
                "entry chooses rather than a figure it measures, so no build can produce it and "
                "the build would have no way to notice it changing.",
    },
    "percent": {
        "0%": "the bottom of a rate table, written as prose",
        "100%": "a whole, written as prose",
        "20%": "the divergence threshold the README states as its own rule",
        "30%": "the withholding rate, which is `withholding.top_rate` but is quoted as policy",
        "5%": "a rounded statement of the age gradient, where the exact pair is also given",
        "63%": "the Solana Foundation's own published figure, cited as theirs",
        "93.8%": "the share of the 640 activations landing on the four minutes after the close, "
                 "which the sentence states as `93.8% of the sample lands on those four minutes`. "
                 "It is 600 of 640, and this build does not render it: the count is held as a "
                 "fraction, and `derivable` renders percentages from a stored fraction only when "
                 "the fraction itself is a leaf, which this one is not. The two figures it was "
                 "previously explained as, the tokenized-equity share of market value and 377 of "
                 "400, are both wrong. 377 of 400 is 94.25%.",
    },
    "pair": {
        "27 of 108": "the competitive field: 27 on-chain programs among the 108 repositories other "
                     "entries had produced, counted on 20 September 2026 over one dated tree "
                     "snapshot. Measured by the field sweep, not by this build, which is why no "
                     "figure in `data.json` stands behind it.",
        "99 of 99": "the verification table's expected output for `scripts/test_checks.py`, which "
                    "is the suite's own pass line rather than a figure from `data.json`. The suite "
                    "is run by a reader and its count is 99 in the stat row, the verification "
                    "table, the reproduce block and the track record; `data.json` is the output of "
                    "`fetch.py` and has never carried it.",
    },
    "days": {
        "33.6": "the age of the committed devnet Pyth price account when it was captured, quoted in "
                "the paragraph about `verify_against_pyth` and the staleness refusal it tests. A "
                "property of the fixture in the tree, not a figure this build computes, and the only "
                "duration on the README that the build cannot produce.",
    },
    "four_dp": {},
}

# Entries that do not fire, and why that is deliberate rather than an oversight.
#
# An entry *fires* when it is the only reason an underivable figure on the README passes. An entry
# listed here is documentation the gate never consults, so deleting it would change no verdict.
# Both kinds are legitimate and the difference is the point: an entry nobody needs is a claim
# about the README that nothing tests, which is the same defect as a comment describing a check
# that does not exist. `unused_allowances` fails the build on any entry that neither fires nor
# appears below, so a stale entry cannot accumulate silently.
#
# The three percentages are prose forms the build happens to produce as well, kept so that the next
# reader asking "why is `0%` on the page" finds an answer.
#
# `27 of 108` was `20 of 82` until 20 September 2026, and the move is the reason this set is worth
# having. `20 of 82` was admitted by coincidence: two unrelated counts in the build happened to be
# 20 and 82, so the gate never reached the entry, the entry never fired, and it was documentation
# rather than a guard. `27 of 108` is not derivable at all, measured rather than assumed, so the
# entry now fires and is the only thing letting the figure through. The comment above predicted
# exactly this move, and the prediction is why the entry was written down rather than left implicit:
# the day the two counts moved, the allowance became load-bearing and its explanation became true.
README_ALLOWED_DOC_ONLY = {
    ("percent", "0%"): "a prose form the build also produces",
    ("percent", "100%"): "a prose form the build also produces",
    ("percent", "5%"): "a prose form the build also produces",
}


def _renditions(value):
    """Every way one number from the build can be printed on the README."""
    out = set()
    if isinstance(value, bool) or value is None:
        return out
    if isinstance(value, (int, float)):
        out.add(str(value))
        out.add("{:,}".format(int(value)))
        if isinstance(value, float):
            for places in (1, 2, 3):
                out.add("%.*f" % (places, value))
        return out
    if isinstance(value, str):
        try:
            dec = Decimal(value)
        except Exception:  # noqa: BLE001 - a non-numeric string carries no figure
            return out
        out.add(str(value))
        out.add("{:,}".format(int(dec)))
        out.add(_usd(value))
        out.add(_bn(value))
        for places in (1, 2, 3):
            out.add("%.*f" % (places, dec))
    return out


def _walk(node, into, skip=()):
    """Collect numeric leaves, skipping per-mint lists that would make anything derivable."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key in skip:
                continue
            _walk(value, into)
    elif isinstance(node, list):
        for item in node:
            _walk(item, into)
    else:
        into.add(node)


def derivable(data):
    """The money, percentage, pair and four-decimal strings this build can account for."""
    # `tokens` is 927 mints deep. Including it would put every multiplier, price and cashflow of
    # every mint into the derivable set, which is how a figure that is not in the build would find
    # something to match.
    values = set()
    for key in ("money", "currency", "withholding", "reconciliation", "mints",
                "activation_timing", "read_rule", "actions", "layout", "supply_witness",
                "price_diverged"):
        if key in data:
            _walk(data[key], values, skip=("tokens",))

    money = set()
    percent = set()
    numbers = set()
    for value in values:
        for text in _renditions(value):
            money.add(text)
            money.add("$" + text)
            numbers.add(text)
        if isinstance(value, (int, float)) and 0 <= value <= 1:
            for text in _renditions(value * 100):
                percent.add(text + "%")
        if isinstance(value, str):
            try:
                dec = Decimal(value)
            except Exception:  # noqa: BLE001
                continue
            if 0 <= dec <= 1:
                for text in _renditions(dec * 100):
                    percent.add(text + "%")

    # Percentages the README quotes that are a ratio of two build counts rather than a stored
    # field: 98.6% is 631 of 640, 8.9% is 57 of 640.
    #
    # This loop is deliberately loose, because a ratio the README states in prose has no single
    # field behind it. The looseness has a cost and it is worth naming: with a few hundred integers
    # across these four blocks it admits any percentage that any pair happens to produce, so a
    # figure passing here means "some pair in the build yields this number", not "this number means
    # what the sentence says". `20 of 82` below is admitted the same way and for the same reason.
    #
    # An earlier version of this comment claimed 93.8% was 377 of 400. It is not. 377 of 400 is
    # 94.25%, and the README's 93.8% is the 600 of 640 activations landing on four minutes, which
    # no pair in these blocks produces. That figure is on the allow-list, and the comment was
    # explaining a derivation that does not exist.
    for block in (data.get("activation_timing") or {}, data.get("withholding") or {},
                  data.get("mints") or {}, data.get("read_rule") or {}):
        ints = [v for v in block.values() if isinstance(v, int) and not isinstance(v, bool)]
        for a in ints:
            for b in ints:
                if b:
                    for text in _renditions(round(a / b * 100, 3)):
                        percent.add(text + "%")

    # `N of M`: both sides have to be a count this build holds. Sums of the small named tables are
    # included, because 174 of the 927 is the non-dollar mints and no single field holds it.
    counts = set()
    for block in (data.get("currency") or {}, data.get("withholding") or {},
                  data.get("mints") or {}, data.get("activation_timing") or {},
                  data.get("read_rule") or {}, data.get("actions") or {},
                  data.get("money") or {}):
        ints = [v for v in block.values() if isinstance(v, int) and not isinstance(v, bool)]
        counts.update(ints)
        counts.add(sum(ints))
        for value in ints:
            counts.add(sum(ints) - value)
        for nested in block.values():
            if isinstance(nested, dict):
                inner = [v for v in nested.values() if isinstance(v, int) and not isinstance(v, bool)]
                counts.update(inner)
                counts.add(sum(inner))
                for value in inner:
                    counts.add(sum(inner) - value)
    # A length is a count too. `price_diverged` has no total field, so "15 mints" is the length of
    # the list rather than a number stored anywhere.
    for key in ("price_diverged", "buckets", "illustrated"):
        node = data.get(key)
        if node is None and key in (data.get("reconciliation") or {}):
            node = data["reconciliation"][key]
        if isinstance(node, list):
            counts.add(len(node))
    # The two totals the README pairs other counts against. Guarded rather than indexed, because
    # `test_checks.py` calls this over hand-built fixtures that carry only the fields the check
    # under test needs, and a derivation that assumes the whole build turns a fixture into a
    # KeyError rather than into a verdict.
    for block, key in (("mints", "total"), ("read_rule", "checked")):
        node = data.get(block)
        if isinstance(node, dict) and isinstance(node.get(key), int):
            counts.add(node[key])
    pairs = {"%s of %s" % ("{:,}".format(a), "{:,}".format(b))
             for a in counts for b in counts if a and b}

    four_dp = set()
    for value in (data.get("reconciliation") or {}).values():
        if isinstance(value, (int, float)):
            for text in _renditions(value):
                four_dp.add(text)
            four_dp.add("%.3f" % value)
            four_dp.add(str(round(value, 3)))

    # The one duration this build can produce. Derived rather than stored, for the reason
    # `median_activation_age` sets out at length, and left empty when the build cannot answer, which
    # is the same guard `figures` applies and the case `missing_activation_age` reports instead of
    # passing. An empty set here is not a hole: a README carrying a duration would then be reported
    # by name, which is the correct verdict for a build that cannot say where the number came from.
    days = set()
    median_age = median_activation_age(data)
    if median_age is not None:
        days.add("%.1f" % median_age)

    # The proof mint's own figures. One named token, not the 927-deep list the walk above skips,
    # and each is also pinned as an exact string by `proof_figures`, so admitting it here cannot let
    # a different figure through.
    token = proof_token(data)
    if token is not None:
        for key in ("gross_per_unit", "net_per_unit", "withheld_per_unit", "withheld_usd"):
            for text in _renditions(token[key]):
                money.add("$" + text)
        for key in ("base_multiplier", "live_multiplier"):
            four_dp.add("%.7f" % token[key])

    return {"money": money, "percent": percent, "pair": pairs, "four_dp": four_dp, "days": days}


def underivable(data, readme):
    """Complaints about figures on the README that this build cannot account for.

    Returns one complaint per distinct token, naming it, so a failure reads as "this number is not
    in the build" rather than as a diff a reader has to interpret.
    """
    known = derivable(data)
    found = {
        "money": set(MONEY.findall(readme)),
        "percent": set(PERCENT.findall(readme)),
        "pair": {"%s of %s" % (a, b) for a, b in PAIR.findall(readme)},
        "four_dp": set(RATIO.findall(readme)),
        "days": set(DAYS.findall(readme)),
    }
    complaints = []
    for kind, tokens in found.items():
        allowed = README_ALLOWED[kind]
        for token in sorted(tokens - known[kind]):
            if token in allowed:
                continue
            complaints.append(
                "%s carries %r, which data.json does not produce and the allow-list does not "
                "explain" % (SURFACES["readme"], token)
            )
    return complaints


def unused_allowances(data, readme):
    """Allow-list entries that neither admit a figure nor say why they do not.

    `underivable` asks whether every figure has an explanation. This asks the other direction, and
    it is the half that was missing. An entry can be dead, or reachable only by accident, and both
    pass every other check in this file: nothing was looking.

    Two entries were wrong on 18 September 2026 and neither was caught. `74%` explained a sentence
    the README no longer carried, so it could not fire and nobody noticed. `$1.8m` was explained as
    the withholding attributed to one issuer while the README used it as a pool depth, so the entry
    fired for a figure it did not describe. The first is a dead entry; the second is a live entry
    with a false explanation, which is worse, because the gate passes and the reason printed on
    failure would have been about a different number.

    An entry fires when it is the only reason an underivable figure passes, which is `token in
    found[kind] and token not in known[kind]`. Anything else has to be declared in
    `README_ALLOWED_DOC_ONLY` with its reason, so the only way to keep an inert entry is to state
    in the code that it is inert.
    """
    known = derivable(data)
    found = {
        "money": set(MONEY.findall(readme)),
        "percent": set(PERCENT.findall(readme)),
        "pair": {"%s of %s" % (a, b) for a, b in PAIR.findall(readme)},
        "four_dp": set(RATIO.findall(readme)),
        "days": set(DAYS.findall(readme)),
    }
    complaints = []
    for kind, table in README_ALLOWED.items():
        for token in sorted(table):
            if token in found[kind] and token not in known[kind]:
                continue  # it fires: the allow-list is what lets this figure through
            if (kind, token) in README_ALLOWED_DOC_ONLY:
                continue
            if token in known[kind]:
                why = "the build derives it, so the entry is never consulted"
            elif token not in found[kind]:
                why = "the README does not carry it"
            else:
                why = "neither admits a figure nor is declared as documentation"
            complaints.append(
                "%s allow-list explains %r, which does not fire: %s. Delete the entry, or record "
                "it in README_ALLOWED_DOC_ONLY with the reason."
                % (SURFACES["readme"], token, why)
            )
    for kind, token in sorted(README_ALLOWED_DOC_ONLY):
        if token not in README_ALLOWED.get(kind, {}):
            complaints.append(
                "README_ALLOWED_DOC_ONLY declares %r under %r, which the allow-list does not "
                "carry, so the declaration is about nothing"
                % (token, kind)
            )
    return complaints


def load(root):
    """`data.json` as written, for the callers that check against the build rather than a fetch."""
    with open(os.path.join(root, "data.json")) as handle:
        return json.load(handle)


# --- The video script's spoken figures ------------------------------------------------------------
#
# The recording is the one artefact here that cannot be rebuilt. Every other surface is either
# generated from `data.json` or re-verified against a live source on submission day. The video is a
# performance: a figure that moves after the take is only fixable with another take, and the take
# costs the forty-five minutes of prep that precede it. Nothing checked the script's numbers, so on
# 18 September 2026 it could have been recorded against a build whose figures had already moved, and
# no gate in this repo would have said so.
#
# The script says its numbers out loud rather than printing them, so this cannot look for `370 of
# 927`. It derives the word form from the build and looks for that. That is the whole point: a check
# that looks for a sentence someone typed is a check that agrees with whatever was typed, which is
# the defect `required` was already caught defending once.
#
# `SPOKEN` names, per beat, what each figure is and a function that renders the build's value the way
# the script says it. Two failure modes are deliberately covered: a figure that has moved, and a
# figure this module cannot render, because the second would be a check that passes without looking.

_UNITS = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
          "fifteen sixteen seventeen eighteen nineteen").split()
_TENS = ("zero ten twenty thirty forty fifty sixty seventy eighty ninety").split()


def _cardinal(value):
    """A whole number in the British form the script speaks: `927` -> `nine hundred and twenty-seven`."""
    n = int(value)
    if n < 0:
        raise ValueError("no spoken form for a negative cardinal: %r" % value)
    if n < 20:
        return _UNITS[n]
    if n < 100:
        tens, unit = divmod(n, 10)
        return _TENS[tens] + ("-" + _UNITS[unit] if unit else "")
    if n < 1000:
        hundreds, rest = divmod(n, 100)
        return _UNITS[hundreds] + " hundred" + (" and " + _cardinal(rest) if rest else "")
    thousands, rest = divmod(n, 1000)
    return _cardinal(thousands) + " thousand" + (" " + _cardinal(rest) if rest else "")


def _spoken_percent(fraction):
    """A fraction as the nearest whole percent in words: `0.9425` -> `ninety-four percent`."""
    whole = (Decimal(fraction) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return _cardinal(whole) + " percent"


def _spoken_millions(value):
    """A dollar total in millions, to one place, in words: `11588741` -> `eleven point six million`."""
    millions = (Decimal(value) / Decimal(1000000)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    tenths = int((millions * 10).to_integral_value(rounding=ROUND_HALF_UP))
    whole, tenth = divmod(tenths, 10)
    if not tenth:
        return _cardinal(whole) + " million"
    return "%s point %s million" % (_cardinal(whole), _UNITS[tenth])


def _spoken_months(value):
    """A month count to the nearest half: `6.6` -> `six and a half months`."""
    halves = int((Decimal(value) * 2).to_integral_value(rounding=ROUND_HALF_UP))
    whole, half = divmod(halves, 2)
    return _cardinal(whole) + (" and a half" if half else "") + " months"


def _spoken_time_utc(hhmm):
    """The modal activation time as the script speaks it: `00:30` -> `half past midnight`.

    Returns None for any time this module has no spoken form for, and the caller reports that rather
    than passing. A silent None here would be the check switching itself off the moment the modal
    time moved, which is exactly when it is needed.
    """
    try:
        hour, minute = (int(part) for part in str(hhmm).split(":"))
    except ValueError:
        return None
    if minute == 30 and hour == 0:
        return "half past midnight"
    if minute == 30 and hour == 12:
        return "half past noon"
    return None


def _spoken_bucket_days(label):
    """A bucket label as the script speaks its boundary: `over 60 days` -> `sixty days`."""
    found = re.search(r"(\d+)", str(label))
    return _cardinal(found.group(1)) + " days" if found else None


# (the beat's start time as its heading prints it, what the figure is, build -> the spoken words)
#
# **Two rows may name the same beat, and two do.** The 21 September 2026 rewrite cut the video from
# 630 words to 431 and dropped the standalone withholding beat, folding its money figures into the
# opening beat, which already carried the rate and the size of the rated field. The figures did not
# move house, they moved beat, and the row that carried them moved with them. Splitting them into two
# rows keyed on one beat rather than one row carrying six phrases is what keeps each row's description
# answerable: a row called "the money over the issuer's own window" that also demanded the field size
# would name neither of them accurately, and the `what` string is the only thing a failure prints.
#
# **The field size left this table's money row for the mechanism beat**, because that is where the
# script now says it. `three hundred and seventy` and `nine hundred and twenty-seven` are checked
# against the beat that speaks them and nowhere else, so a rewrite that moves the sentence without
# moving the row fails rather than passing on a stale pointer.
SPOKEN = (
    ("0:00", "the top withholding rate and the size of the rated field",
     lambda d: (_spoken_percent(d["withholding"]["top_rate_share"]),
                _cardinal(d["withholding"]["rated_symbols"]) + " stocks",
                _spoken_percent(d["withholding"]["top_rate"]),
                _spoken_millions(d["money"]["annualised_withheld_usd"]))),
    ("0:00", "the money over the issuer's own window",
     lambda d: (_spoken_months(d["actions"]["months"]),
                _spoken_millions(d["money"]["withheld_usd"]),
                _spoken_millions(d["money"]["annualised_withheld_usd"]))),
    ("1:00", "the mints whose live value differs from the field named multiplier",
     lambda d: (_cardinal(d["mints"]["live_differs_from_base"]),
                _cardinal(d["mints"]["total"]))),
    ("1:20", "the read rule checked on every mint",
     lambda d: (_cardinal(d["read_rule"]["agreed"]) + " agreed",
                "none disagreed" if not d["read_rule"]["disagreed"]
                else _cardinal(d["read_rule"]["disagreed"]) + " disagreed")),
    ("1:40", "when the activations land",
     lambda d: (_spoken_time_utc(d["activation_timing"]["top_times"][0][0]),
                _cardinal(d["activation_timing"]["n"]) + " activations",
                _cardinal(d["activation_timing"]["outside_calendar"]),
                _cardinal(d["activation_timing"]["on_a_non_trading_day"]))),
    ("2:00", "the age gradient, fresh against the oldest bucket",
     lambda d: (_spoken_percent(d["reconciliation"]["buckets"][0]["median"]) + " fresh",
                _spoken_percent(d["reconciliation"]["buckets"][-1]["median"]),
                _spoken_bucket_days(d["reconciliation"]["buckets"][-1]["label"]))),
)

# The figures the script puts on screen rather than speaks. They are digit strings, because that is
# what a card carries, and they are derived from the build for the same reason the spoken ones are.
# This table exists because the first version of the script check read only the blockquotes, and the
# reviewer named "the 94.2% card" among the figures to verify: a card is read by a viewer, so a stale
# card is the same defect as a stale sentence and it was uncovered until 18 September 2026.
ON_SCREEN = (
    ("0:00", "the card in the hero, which the opening beat holds the camera on",
     lambda d: (pct(d["withholding"]["top_rate_share"]) + "%",)),
    ("1:00", "the two-value card's multiplier pair, left value then right",
     lambda d: (multiplier(worked_example(d)["base_multiplier"]),
                multiplier(worked_example(d)["live_multiplier"]))),
    ("1:20", "the agreed count the paragraph under the card ends on",
     lambda d: ("%d agreed" % d["read_rule"]["agreed"],
                "%d disagreed" % d["read_rule"]["disagreed"])),
)

# The figures the prep section tells the reader to confirm on the page before recording. Weaker than
# the other two, because nothing here reaches the recording, and not worthless: a prep step naming a
# figure the page no longer shows costs the reader time on the morning they have least of it.
#
# The first field is the beat, and it is None here because the prep section is one block rather than a
# sequence of beats. Kept as a three-tuple rather than a two-tuple so that all three tables have one
# shape and `_figures` cannot be handed a row it misreads: a table that unpacks differently is how a
# check comes to silently read the wrong field.
PREP = (
    (None, "the hero chip's withheld share",
     lambda d: (pct(Decimal(d["money"]["withheld_usd"]) / Decimal(d["money"]["gross_usd"])) + "%",)),
    (None, "the first card",
     lambda d: (pct(d["withholding"]["top_rate_share"]) + "%",)),
    (None, "the hero lede's mint count",
     lambda d: ("%d of %d mints" % (d["mints"]["live_differs_from_base"], d["mints"]["total"]),)),
    (None, "the read-rule sentence",
     lambda d: ("%d agreed, %d disagreed"
                % (d["read_rule"]["agreed"], d["read_rule"]["disagreed"]),)),
)

# The figures in the upload notes: the watch-back checklist, the title, and the description. Nothing
# read these lines until 20 September 2026, and they are the most judge-visible lines in the whole
# script, because the title and the description are what a judge reads before deciding whether to
# press play at all.
#
# The shape of the gap, because it is the entry's own recurring one and worth naming rather than
# closing quietly. `_script_body` cuts the trailing notes off before the beat checks run, and the
# reason it gives is right: a figure down there is not in the video. Nothing took over. So the
# `94.2%` card the checklist tells the reader to look for, and the `370 of 927` that the title and
# the description each carry, were derived by nothing. The README's copy of `370 of 927` was gated
# and the script's two were not, which is the two-surfaces-current-and-disagreeing defect this entry
# has shipped before, in the page and the banner.
#
# The first field is a region rather than a beat, and that is the second half of the same point. The
# notes are three hand-written surfaces, and the title and the description each carry the mint count,
# so one region covering the whole section would let either be edited without the other being
# noticed. Measured rather than assumed: with a single region, mutating the title's copy left the
# check silent, because the description's copy still satisfied a presence test over the section.
# Naming the regions costs one string per row and makes each surface answerable on its own.
#
# Found by mutating one occurrence of a checked figure at a time rather than all of them at once: a
# single mutation of every occurrence cannot tell a surface that is covered from one that is not,
# because the build refuses on the first problem it can see and the uncovered line is never reached.
#
# A three-tuple, and the region name is not a beat, for the reason PREP gives: one shape for every
# table, so `_figures` cannot be handed a row it misreads.
AFTER = (
    ("checklist", "the watch-back checklist's first item",
     lambda d: (pct(d["withholding"]["top_rate_share"]) + "%",)),
    ("title", "the upload title's mint count",
     lambda d: ("%d of %d" % (d["mints"]["live_differs_from_base"], d["mints"]["total"]),)),
    ("description", "the description's mint count, which repeats the title's",
     lambda d: ("%d of %d" % (d["mints"]["live_differs_from_base"], d["mints"]["total"]),)),
    ("description", "the description's rated field and the rate it applies",
     lambda d: ("%d of the %d" % (d["withholding"]["top_rate_symbols"],
                                  d["withholding"]["rated_symbols"]),
                pct(d["withholding"]["top_rate"], places=0) + "%")),
    ("description", "the description's annualised figure",
     lambda d: (_millions(d["money"]["annualised_withheld_usd"]),)),
)

# What the script carries that these checks deliberately do not cover, and why. Named rather than left
# as a silence, because "not checked" and "checked and fine" print the same otherwise. This list was
# itself incomplete until 18 September 2026: it named two exceptions and omitted the largest class,
# the on-screen directions, which is how a declared gap becomes a real one. Extended on 20 September
# 2026 for the same reason, when the upload notes' four derived figures moved out of this list and
# into `AFTER`: what remains here is what the section carries that is not a build figure at all.
# Rewritten again on 21 September 2026 when the terminal demo left the video and the desk replaced it:
# the first entry named the demo's own figures, and the demo is no longer spoken, so the entry was
# rewritten rather than kept. A declared gap that names a surface the script no longer has is worse
# than a missing one, because it reads as coverage of something that is not there.
SCRIPT_NOT_CHECKED = (
    "the desk's own live figures, which come from the page's reads at recording time and not from "
    "`data.json`: the withheld figure on the reader's holding, the seconds counter on the devnet "
    "verdict, and the verdict itself, which is `HOLD` only because a mint was activated minutes "
    "before the camera rolled",
    "`fifteen minutes`, which is a quotation of the issuer's own documentation rather than a figure "
    "this build computes; EVIDENCE.md carries the page it was read from",
    "the on-screen directions that name a region rather than a figure (the timing block, the meter "
    "and its four figures), because there is no derived string to compare with a pointer",
    "the beat timings and the word counts, which are counted by `VIDEO-SCRIPT.md`'s own header and "
    "recounted by hand rather than derived from the build",
    "the upload notes' references that are not build figures: the desk's two status chip labels, "
    "which are literals in `desk_template.html` rather than values this build derives, the camera "
    "timings in the watch-back list, and the documentation dates quoted from the issuer and the "
    "programme",
)

# Kept under the old name because the response document and the commit that introduced it both refer
# to `SPOKEN_NOT_CHECKED`, and a rename would leave those references pointing at nothing.
SPOKEN_NOT_CHECKED = SCRIPT_NOT_CHECKED


def _script_body(script):
    """The beat-by-beat part of the script, with the trailing upload notes cut off.

    Both the spoken check and the on-screen check read this, so the cut is defined once: a figure in
    the trailing notes is not in the video, and two parsers disagreeing about where the beats end is
    the same class of defect as two formatters disagreeing about a digit.
    """
    lines = script.split("\n")
    for i, line in enumerate(lines):
        if re.match(r"^\*\*(Total spoken|\[DROP|\[WAIT)", line):
            return lines[:i]
    return lines


def _part_text(script, start, end):
    """The text between two markers, or None when the first is not there.

    Named for the parts of the script, which is what it was written for, and used for the upload
    notes' labels as well since 20 September 2026: a part heading and a bold label are the same thing
    to this function, and the second caller needs the same None rather than "".

    None rather than an empty string, so the caller can tell "this region carries no figures" from
    "this region is missing". The first is nothing to check; the second is a check that has stopped
    looking, and the two would print the same if this returned "".
    """
    if start not in script:
        return None
    body = script.split(start, 1)[1]
    if end in body:
        body = body.split(end, 1)[0]
    return body


def _spoken_beats(script):
    """The script's beats as `{start time: spoken text}`.

    Only blockquotes are collected. The `**On screen:**` directions, the wait notes and the trailing
    upload metadata are not spoken, so a figure on one of those lines is not a figure the viewer
    hears and counting it here would let a moved number pass on a line nobody says. The on-screen
    lines carry figures of their own and are checked separately, by `_screen_text`.
    """
    beats, current = {}, None
    for line in _script_body(script):
        heading = re.match(r"^### (\d+:\d+) ", line)
        if heading:
            current = heading.group(1)
            beats[current] = []
        elif current and line.startswith(">"):
            beats[current].append(line.lstrip(">").strip())
    return {beat: " ".join(text) for beat, text in beats.items()}


def _screen_text(script):
    """The script's on-screen directions as `{start time: text}`, continuation lines included.

    These lines are not spoken, and they are still read by a viewer: the card the camera holds on, the
    two-value card's multiplier pair, the agreed count a paragraph ends on. A stale figure here fails
    in exactly the way a stale spoken one does, because the recording cannot be rebuilt from the tree
    either, so the two are checked with the same machinery and reported with the same force.

    **Continuation lines are collected, and that is not a detail.** The mechanism beat's pair sits on
    the line after its `**On screen:**` marker. A parser that read only the marker's own line cannot
    see it, and the consequence runs the opposite way from the substring bug above: it would refuse a
    correct script rather than pass a wrong one, because the check asks whether the derived figure is
    present and a figure it cannot see is reported as missing. So the continuation handling is what
    makes the check usable at all, and both directions are kept in the controls, because a check can
    fail for the wrong reason as easily as it can pass for one and only one of those is loud.
    """
    screen, current, taking = {}, None, False
    for line in _script_body(script):
        heading = re.match(r"^### (\d+:\d+) ", line)
        if heading:
            current = heading.group(1)
            screen[current] = []
            taking = False
            continue
        if line.startswith("**On screen:**"):
            taking = True
            screen[current].append(line[len("**On screen:**"):].strip())
            continue
        if taking:
            if not line.strip() or line.startswith((">", "**", "#")):
                taking = False
                continue
            screen[current].append(line.strip())
    return {beat: " ".join(text) for beat, text in screen.items()}


def _phrase_present(words, text):
    """Whether the script carries exactly this phrase, rather than merely containing its letters.

    A substring test passes on the wrong figure, and did: `three hundred and seventy` is a substring
    of `three hundred and seventy-one`, so the count moved by one and the check stayed green. The
    phrase has to end where it ends, which for a number means the next character cannot be a word
    character or a hyphen, because that is how the next digit or the next word would arrive.

    `\\b` is not enough here for the same reason: a hyphen is not a word character, so `\\bseventy\\b`
    matches inside `seventy-one`. The lookarounds below reject it.

    Used for the on-screen figures as well as the spoken ones. The name is about phrases rather than
    about speech, because `94.2%` and `927 agreed` need the same boundary rule that
    `three hundred and seventy` needs.
    """
    pattern = r"(?<![-\w])" + re.escape(words.lower()) + r"(?![-\w])"
    return re.search(pattern, text.lower()) is not None


def _figures(data, table, target, missing, wrong):
    """One table of derived figures, checked against the text that should carry them.

    Shared by the four surfaces the video script has: what is spoken, what is on screen, what the
    prep section tells the reader to confirm, and what the upload notes carry. They read different
    lines and word their complaints differently, and the part that must not differ is this one: the
    derivation, the guard against a figure this module cannot render, and the boundary rule. Four
    copies of that would be four chances for one of them to be the blind one, which is exactly how the
    first version of the spoken check came to pass on a moved count.
    """
    complaints = []
    for beat, what, render in table:
        try:
            wanted = render(data)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            complaints.append(
                "the video script cannot be checked against %s: this build has no such figure, so "
                "the check is not looking (%r)" % (what, exc))
            continue
        text = target(beat)
        if not text:
            complaints.append(missing(beat, what))
            continue
        for words in wanted:
            if words is None:
                complaints.append(
                    "the video script says nothing for %s: this module has no rendering for the "
                    "build's current value, so the check would pass without looking" % what)
            elif not _phrase_present(words, text):
                complaints.append(wrong(beat, words, what))
    return complaints


def spoken_figures(data, script):
    """Complaints about a spoken figure the build no longer supports. Empty is the pass."""
    beats = _spoken_beats(script)
    return _figures(
        data, SPOKEN, beats.get,
        lambda beat, what: "the video script has no beat starting at %s, so %s is unchecked"
                           % (beat, what),
        lambda beat, words, what: "the video script's beat %s should say %r for %s, and does not"
                                  % (beat, words, what))


def screen_figures(data, script):
    """Complaints about a figure the script puts on screen. Empty is the pass.

    Separate entry point from the spoken one because the two read different lines and a complaint has
    to say which kind of line it is about: a figure wrong on screen and a figure wrong in the
    narration are different defects with the same consequence, and whoever fixes it needs to know
    which line to open.
    """
    screens = _screen_text(script)
    return _figures(
        data, ON_SCREEN, screens.get,
        lambda beat, what: "the video script has no on-screen direction at %s, so %s is unchecked"
                           % (beat, what),
        lambda beat, words, what:
            "the video script's on-screen direction at %s should carry %r for %s, and does not"
            % (beat, words, what))


def prep_figures(data, script):
    """Complaints about a figure the prep section tells the reader to confirm. Empty is the pass.

    Not in the recording and not on screen, so this is the weakest of the three and it is still worth
    checking. The prep section is a list of things to confirm on the page before the camera is turned
    on, and a step that says "confirm the first card reads 94.2%" while the card reads 94.3% sends the
    reader looking for something that is not there, on the one morning when there is no time to
    investigate. The page is generated so it cannot drift; the prep text is hand-written so it can.
    """
    prep = _part_text(script, "## Part 1", "## Part 2")
    return _figures(
        data, PREP, lambda beat: prep,
        lambda beat, what: "the video script has no prep section, so %s is unchecked" % what,
        lambda beat, words, what:
            "the video script's prep section should tell the reader to confirm %r for %s, and does not"
            % (words, what))


def after_figures(data, script):
    """Complaints about a figure in the upload notes. Empty is the pass.

    The weakest of the four in one sense, because none of it is in the recording, and the strongest
    in another: the title and the description are the copy a judge reads before the video, and a
    stale figure there is read by more people than a stale beat is. That is the argument for checking
    it, and it is not the argument that settled it. The prep section was already checked on the
    weaker ground that a stale instruction costs the reader time, and this section is the same kind
    of hand-written text over the same generated page, so leaving it unread made the coverage claim
    narrower than the sentence describing it.

    The notes are read as three named regions rather than as one block, because the title and the
    description carry the same mint count and a single block cannot see one of them move. The region
    markers are the script's own labels, so each one is findable, and a region whose marker has gone
    is reported as unchecked rather than skipped: the difference between "this region carries no
    figures" and "this region is missing" is the difference between a pass and a check that has
    stopped looking.

    The last region runs to the end of the file, so the end marker is a label that does not exist.
    That is deliberate. Everything after the description is upload metadata or a note to the reader,
    and a real end marker would leave whatever follows it read by no check the moment a tag or a
    footer is added.
    """
    regions = {
        "checklist": _part_text(script, "**Watch it back in full before uploading.**", "\n\n"),
        "title": _part_text(script, "**Title:**", "\n"),
        "description": _part_text(script, "**Description:**", "**Tags:**"),
    }
    return _figures(
        data, AFTER, regions.get,
        lambda region, what:
            "the video script's upload notes have no %s, so %s is unchecked" % (region, what),
        lambda region, words, what:
            "the video script's upload notes should carry %r for %s, and do not" % (words, what))
