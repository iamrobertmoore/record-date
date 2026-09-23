#!/usr/bin/env python3
"""Negative controls for the checks in fetch.py that were rewritten, and for the session calendar.

A check that cannot fail for the reason it claims is worse than no check, because it looks like
rigour. Both of the rewritten ones were in that state. This file holds the old form of each next to
the new form, on inputs built to separate them, so the difference is auditable rather than asserted.

Check C is different in kind: it exercises the real `parse_schedule` and `session_open` from
fetch.py, imported rather than copied, against dates whose answer is known from the exchange
calendar itself. A copy of the parser would only prove the copy agrees with itself.

Run it with `python3 scripts/test_checks.py`. It needs no network and no dependencies.

    check A, the price source. Jupiter carries two price fields for a tokenized equity and they
    disagree badly on a few mints. The entry has to say which one it used and why, and the
    reconciliation is the third opinion that decides it, because it uses no price at all.

    The old form selected the rows where the rejected field was already more than 50% out, then
    reported how far out it was. Two things are wrong with that. The reported number is a
    restatement of the filter, since no row below 50% can survive it. And the selection is a
    function of the outcome: `error_alt` is defined relative to which field the run chose, so
    swapping the choice swaps the selection. A check whose rows are chosen using the result it is
    testing is not testing the result.

    The new form selects on the two fields disagreeing with each other, which is a property of the
    data and is unchanged if the choice is swapped. It also requires the chosen field to clear a
    loose absolute bound, because relative-only lets a field that is 100% out pass against one that
    is 1,000% out.

    check B, the feed dedupe. The feed republishes an amended event under the same eventId with a
    higher version. The survivor must be the highest version. The old form compared the deduped
    list against the set of its own keys, which is true by construction and says nothing about
    which row survived. The new form compares each survivor against the highest version actually
    published for that eventId.
"""

import datetime
import importlib.util
import json
import os
import re
import sys
import tempfile
import zoneinfo
from statistics import median

HERE = os.path.dirname(os.path.abspath(__file__))

# The figures the hand-written surfaces carry, imported rather than copied. A control that drove a
# copy would prove the copy agrees with itself, which is the failure this file exists to catch.
sys.path.insert(0, HERE)
import published  # noqa: E402


def load_calendar():
    """`parse_schedule` and `session_open` as defined in fetch.py, not as redefined here.

    fetch.py runs its whole pipeline at import, so it cannot simply be imported. It is loaded with
    `__name__` set to something other than `__main__` and the module body is allowed to run only as
    far as its function definitions, which is what `spec.loader.exec_module` does. If that ever
    stops working the check reports itself skipped rather than passing silently, because a skipped
    control that reads as a pass is the exact failure this file exists to catch.
    """
    path = os.path.join(HERE, "fetch.py")
    try:
        spec = importlib.util.spec_from_file_location("fetch_under_test", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules["fetch_under_test"] = module
        spec.loader.exec_module(module)
        return module.parse_schedule, module.session_open
    except Exception as exc:  # noqa: BLE001 - a skip must be visible, not fatal
        print("  could not load fetch.py: %s: %s" % (type(exc).__name__, exc))
        return None


def load_fetch():
    """fetch.py as a module, for a control that has to call a real function rather than a copy.

    Unused at the moment: check D moved to `published.py` when the README joined the surfaces it
    checks, and nothing else needs the module. Kept because the loader is the fiddly part, and the
    next control that wants a real function should not have to rediscover that fetch.py runs its
    whole pipeline at import and has to be loaded with `__name__` set to something else.
    """
    path = os.path.join(HERE, "fetch.py")
    try:
        spec = importlib.util.spec_from_file_location("fetch_under_test", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules["fetch_under_test"] = module
        spec.loader.exec_module(module)
        return module
    except Exception as exc:  # noqa: BLE001 - a skip must be visible, not fatal
        print("  could not load fetch.py: %s: %s" % (type(exc).__name__, exc))
        return None


# --------------------------------------------------------------------------- check A

def divergent_new(recon):
    """The current selection: on the two fields disagreeing, not on the error."""
    return [
        r for r in recon
        if r["alt_price"] and r["chosen_price"]
        and max(r["alt_price"] / r["chosen_price"], r["chosen_price"] / r["alt_price"]) > 1.2
    ]


def divergent_old(recon):
    """The old selection: on the rejected field already being over 50% out."""
    return [r for r in recon if r["error_alt"] is not None and abs(r["error_alt"]) > 0.5]


def check_relative(div):
    """The old test: beat the other field by four, with no bound on either."""
    return (
        len(div) >= 3
        and median([abs(r["error"]) for r in div]) < median([abs(r["error_alt"]) for r in div]) / 4
    )


def check_new(div):
    """The current test: beat the other field by four, and clear 25% in absolute terms."""
    if len(div) < 3:
        return False
    chosen = median([abs(r["error"]) for r in div])
    rejected = median([abs(r["error_alt"]) for r in div])
    return chosen < 0.25 and chosen < rejected / 4


def row(symbol, chosen, alt, error, error_alt):
    return {
        "symbol": symbol,
        "chosen_price": chosen, "alt_price": alt,
        "error": error, "error_alt": error_alt,
    }


def swap(r):
    """The same row with the other price field chosen."""
    return row(r["symbol"], r["alt_price"], r["chosen_price"], r["error_alt"], r["error"])


# --------------------------------------------------------------------------- check B

def dedupe_check(cash_rows, scheduled):
    """The current dedupe check: every survivor is the highest version published for its event."""
    highest = {}
    for a in cash_rows:
        key = a.get("eventId")
        if key is None:
            continue
        highest[key] = max(highest.get(key, -1), a.get("version") or 0)
    wrong = [a for a in scheduled
             if a.get("eventId") in highest and (a.get("version") or 0) != highest[a["eventId"]]]
    return len(cash_rows) - len(scheduled) > 0 and not wrong


def dedupe_check_old(scheduled):
    """The old form: compare the deduped list against the set of its own keys."""
    return len(scheduled) == len({a.get("eventId") for a in scheduled})


# --------------------------------------------------------------------------- harness

def report(name, got, want):
    # Equality, not identity. `got is want` is true only for the same object, so it reported a
    # failure for every list and tuple that had the right contents. The first version of this file
    # used `is` and was wrong twice because of it.
    ok = got == want
    print("  %-62s got %-5s want %-5s %s" % (name, got, want, "ok" if ok else "FAIL"))
    return ok


# --------------------------------------------------------------- check D, the old form

def to_places_old(value, places):
    """The old form of `published.to_places`: cut the tail off rather than round it.

    Kept here so the difference between the two is auditable rather than asserted, the same way
    check A keeps the old selection beside the new one. It lived in `build_page.py` and was deleted
    when the page and the check on the banner were made to share one definition; this file is now
    the only place it exists, which is deliberate. An old form that has been deleted everywhere
    cannot be compared against anything.
    """
    text = str(value)
    if "." in text:
        whole, frac = text.split(".", 1)
        frac = frac[:places].rstrip("0")
        return whole + ("." + frac if frac else "")
    return text


def spoken_present_old(words, text):
    """The first form of `published._phrase_present`: plain substring containment.

    Kept here so the difference between the two is auditable rather than asserted, the same way this
    file keeps `to_places_old` and `divergent_old`. It is the form that was in the tree, and it is
    wrong in the direction that matters: `three hundred and seventy` is a substring of `three
    hundred and seventy-one`, so a count moved by one kept the check green.
    """
    return words.lower() in text.lower()


def screen_text_old(script):
    """The first form of `published._screen_text`: the `**On screen:**` marker's own line only.

    Also kept so the difference is auditable. It is not a substring bug like the one above, it is a
    continuation bug, and it fails in the opposite direction: the mechanism beat's multiplier pair sits
    on the line *after* its marker, so this parser cannot see it, and a check over it would refuse a
    correct script. A false refusal rather than a silent pass, which is worth having both forms in view
    for: the two failure modes are mirrors and only one of them is loud.

    The direction is stated rather than assumed, and it was assumed wrong first. The docstring here
    originally claimed this parser "would pass on a script that had lost it", which is the opposite of
    what a presence check does with text it cannot see. Running it is what corrected the claim, which is
    the same rule this entry applies to every figure in it.
    """
    screen, current = {}, None
    for line in script.split("\n"):
        heading = re.match(r"^### (\d+:\d+) ", line)
        if heading:
            current = heading.group(1)
            screen[current] = ""
        elif current and line.startswith("**On screen:**"):
            screen[current] = line[len("**On screen:**"):].strip()
    return screen


def main():
    passed = total = 0
    def case(name, got, want):
        nonlocal passed, total
        total += 1
        passed += report(name, got, want)

    print("check A: the price source")
    print()

    # The real shape, from the run on 16 September 2026. PYPLx was quoted at 3,337.04 by the pool
    # field against a reference of 53.16; the reference reconciles to 56.69 and the pool quote does
    # not. SCHFx is the same pattern by 3.8x. The other two rows are ordinary disagreements.
    good = [
        row("PYPLx", 53.16, 3337.04, 0.066, 61.8),
        row("SCHFx", 27.865, 106.34, 0.013, 2.87),
        row("MUx", 20.0, 25.0, 0.03, 0.28),
        row("PYPLx", 52.86, 3323.65, 0.072, 61.9),
    ]
    bad_choice = [swap(r) for r in good]

    print("  the selection")
    case("new selection takes all four disagreeing rows",
         len(divergent_new(good)), 4)
    case("old selection takes three, because one disagreement is only 25% out",
         len(divergent_old(good)), 3)

    # The property that matters. The new selection is unchanged when the choice is swapped, so it
    # is a property of the two fields. The old selection collapses to nothing, because it was
    # reading the outcome.
    case("new selection is unchanged by swapping the choice",
         [r["symbol"] for r in divergent_new(bad_choice)],
         [r["symbol"] for r in divergent_new(good)])
    case("old selection collapses to empty when the choice is swapped",
         len(divergent_old(bad_choice)), 0)

    print()
    print("  the test")
    case("new test passes when the field that reconciles was chosen",
         check_new(divergent_new(good)), True)
    case("negative control: new test fails when the pool quote was chosen",
         check_new(divergent_new(bad_choice)), False)

    # The old form on the swapped input fails too, but it fails with an empty selection, so it
    # reports nothing about the choice. That is the weakness: a failure and an absence of evidence
    # are the same output.
    case("old form on the same input fails only because it selected no rows",
         check_relative(divergent_old(bad_choice)), False)
    case("  ... and it would have passed on the unswapped input",
         check_relative(divergent_old(good)), True)

    case("negative control: two disagreeing rows is not a pattern",
         check_new(divergent_new(good[:2])), False)
    agreeing = [row("A", 100.0, 100.5, 0.01, 0.015), row("B", 50.0, 50.1, 0.02, 0.018),
                row("C", 75.0, 74.8, 0.005, 0.007), row("D", 20.0, 20.05, 0.01, 0.012)]
    case("negative control: the fields agree, so there is nothing to decide",
         check_new(divergent_new(agreeing)), False)

    # Why the absolute bound is there. Three rows where the chosen field is 100% out and the
    # rejected field is 1,000% out. Beating the other field by ten times is not evidence that the
    # chosen field is a price, and relative-only calls this a pass.
    both_bad = [row("X", 100.0, 10.0, 1.0, 10.0) for _ in range(3)]
    case("negative control: relative-only passes a chosen field that is 100% out",
         check_relative(divergent_new(both_bad)), True)
    case("  ... and the absolute bound rejects it",
         check_new(divergent_new(both_bad)), False)

    print()
    print("check B: the feed dedupe")
    raw = [
        {"eventId": "a", "version": 1}, {"eventId": "a", "version": 2},
        {"eventId": "b", "version": 1}, {"eventId": "c", "version": 1},
        {"eventId": "c", "version": 3},
    ]
    kept_right = [{"eventId": "a", "version": 2}, {"eventId": "b", "version": 1},
                  {"eventId": "c", "version": 3}]
    kept_wrong = [{"eventId": "a", "version": 1}, {"eventId": "b", "version": 1},
                  {"eventId": "c", "version": 3}]

    case("the highest version survives",
         dedupe_check(raw, kept_right), True)
    case("negative control: a superseded version survived",
         dedupe_check(raw, kept_wrong), False)
    # The old form cannot tell the two apart, because a deduped list has unique keys either way.
    case("old form cannot tell the right survivor from the wrong one",
         (dedupe_check_old(kept_right), dedupe_check_old(kept_wrong)), (True, True))
    case("negative control: nothing was superseded, so there is nothing to collapse",
         dedupe_check([{"eventId": "a", "version": 1}], [{"eventId": "a", "version": 1}]), False)

    print()
    print("check C: the exchange calendar")
    print()

    # The real functions, imported from fetch.py rather than copied. A copy would only prove the
    # copy agrees with itself, and the whole point of this check is that the parser in the build is
    # the one being tested.
    calendar = load_calendar()
    if calendar is None:
        print("  fetch.py could not be imported; check C skipped")
        print()
        print("%d of %d as expected" % (passed, total))
        return 0 if passed == total else 1

    parse_schedule, session_open = calendar
    # The schedule Pyth publishes for US equities, as read on 16 September 2026. Kept here as a
    # literal so this file needs no network, and asserted against the live feed by fetch.py itself.
    SCHEDULE = (
        "America/New_York;0930-1600,0930-1600,0930-1600,0930-1600,0930-1600,C,C;"
        "0907/C,1126/C,1127/0930-1300,1224/0930-1300,1225/C"
    )
    tz, weekly, overrides = parse_schedule(SCHEDULE)
    cal = {"timezone": tz, "weekly": weekly, "overrides": overrides}

    case("the timezone is the exchange's", tz, "America/New_York")
    case("the week is Monday first", [s is not None for s in weekly],
         [True, True, True, True, True, False, False])
    case("Monday opens at 09:30 and closes at 16:00",
         weekly[0], (9 * 60 + 30, 16 * 60))
    case("overrides are parsed, including the half days", len(overrides), 5)

    print()
    print("  the session test, on dates whose answer is known")
    # Each case is (instant, open?, what it is). The two that matter most are the holiday and the
    # half day, because those are the cases the weekly pattern alone gets wrong.
    for iso, want, label in (
        ("2026-09-16T18:00:00+00:00", True, "Wednesday 14:00 ET, mid session"),
        ("2026-09-16T00:30:00+00:00", False, "the real activation time, 20:30 ET the day before"),
        ("2026-09-15T23:55:00+00:00", False, "the other real activation time, 19:55 ET"),
        ("2026-09-07T15:00:00+00:00", False, "Labour Day 11:00 ET: the override must beat the weekday"),
        ("2026-11-27T18:00:00+00:00", True, "half day, 13:00 ET: still open"),
        ("2026-11-27T19:00:00+00:00", False, "half day, 14:00 ET: closed"),
        ("2026-12-25T18:00:00+00:00", False, "Christmas Day"),
        ("2026-09-19T18:00:00+00:00", False, "Saturday"),
    ):
        case(label, session_open(datetime.datetime.fromisoformat(iso), cal), want)

    # The negative control for the whole idea. If the calendar added nothing over the weekday
    # pattern, it would not be worth fetching. Labour Day is the case that separates them: the
    # weekly pattern says Monday trades, and the override says it does not.
    labour_day = datetime.datetime.fromisoformat("2026-09-07T15:00:00+00:00")
    local = labour_day.astimezone(zoneinfo.ZoneInfo("America/New_York"))
    case("negative control: the weekly pattern alone would say Labour Day is open",
         weekly[local.weekday()] is not None, True)
    case("  ... and the calendar correctly says it is shut",
         session_open(labour_day, cal), False)

    # ------------------------------------------------------------------ check D
    # The hand-written surfaces. This one differs in kind from A and B: there is no old form to set
    # beside a new one, because the old form was nothing at all. The README, the banner and the
    # architecture diagram are written by hand, so they are the figures on the surface a judge reads
    # first that do not follow data.json, and the claim-consistency sweep asserts only that those
    # strings are *present*. A figure that moved would therefore ship and pass every other check in
    # this workspace.
    #
    # That is not hypothetical. The committed tree carried it in two places when this control was
    # written: the README said `1.479` where its own data.json said `1.478`, and `1.53%` where the
    # same file said `1.54%`.
    #
    # The control drives the real `published.stale` over files written to a temporary directory,
    # from a fixture this file builds. That is what makes it a control rather than a restatement:
    # the fixture is this file's numbers, so this file can move one and see it reported.
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "docs", "brand"))
        # Deliberately round numbers, so the fixture is visibly a fixture and a failure cannot be
        # confused with the real build. They still exercise every formatting path, including the
        # half-up rounding on the pence share, which sits on a boundary at 1.535.
        sample = {
            "money": {
                "gross_usd": "20000000", "net_usd": "15000000", "withheld_usd": "5000000",
                "annualised_withheld_usd": "9000000",
                "paid_gross_usd": "19000000", "forward_gross_usd": "1000000",
            },
            "actions": {"months": 6.6},
            "read_rule": {"agreed": 927, "disagreed": 0, "no_value": 0},
            "mints": {"total": 927, "live_differs_from_base": 370},
            # The three figures on the README's first bold line. `figures` asks for them by name now,
            # so a fixture without them reports the fixture rather than the entry. The rate is the
            # feed's own top rate and the pair is the symbols it applies to, and those two are what
            # make "lose 26.2%" a different figure rather than a typo: 26.2% is withheld over gross.
            "withholding": {"top_rate": "0.3", "top_rate_symbols": 377, "rated_symbols": 400,
                            "top_rate_share": 0.9425},
            # `outside_calendar` is the count against the exchange's own calendar and `outside` is
            # the UTC-window one sitting beside it. They differ by three, so a fixture carrying only
            # the second would let the first be asked for and never found.
            "activation_timing": {"n": 640, "on_a_non_trading_day": 57,
                                  "outside_calendar": 631, "outside": 628},
            "reconciliation": {
                "net_over_market": 1.0349, "gross_over_market": 1.4785,
                "fresh_median": 0.0213, "stale_median": 0.0520,
                "buckets": [{"label": "0-2 days", "n": 41, "median": 0.0198}],
            },
            "currency": {
                "usd_per": {"GBP": 1.345587},
                "pence_share_of_book": 0.01535,
                "market_value_as_read_usd": "13638378887",
                "market_value_converted_usd": "6416996434",
                "read_over_converted": 2.1254,
                "fx_date": "2020-01-01",
            },
            # The median activation age is derived from `built_utc` and each token's
            # `effective_at`, so the fixture has to carry both. Without them
            # `missing_activation_age` reports the fixture rather than the entry, which is the
            # correct behaviour and the wrong fixture. One token dated ten days before the capture,
            # so the figure is 10.0 days and the string the README is asked for reads "1 of the 1
            # tokens priced here ... median age 10.0 days": visibly a fixture, which is the point of
            # the round numbers above.
            "built_utc": "2020-01-01T00:00:00Z",
            "tokens": [{"base_multiplier": 1.0268028384810615,
                        "live_multiplier": 1.0344000941634355,
                        "effective_at": 1576972800}],
            # The two price-divergence figures are derived from this list, so the fixture carries it
            # or `missing_divergence` reports the fixture instead of the entry. Two rows, and the
            # second is the one that matters: the pool quote is ten times the reference, while the
            # first is only twice. A derivation that took `reference / pool` and never the other
            # direction would answer 2 here and be wrong by a factor of five, which is why the
            # assertion below is on the value rather than on the README fixture, since the README
            # fixture is generated from `figures` and would agree with either.
            "price_diverged": [{"mint": "X" * 32, "reference": 100.0, "pool": 50.0},
                               {"mint": "Y" * 32, "reference": 10.0, "pool": 100.0}],
        }
        want = published.figures(sample)

        def write(path, text):
            with open(os.path.join(tmp, path), "w") as handle:
                handle.write(text)

        # Every surface written to carry exactly the fixture's figures. The join is what a file with
        # no line breaks looks like; a figure has to appear verbatim and the separator is irrelevant.
        for name, path in published.SURFACES.items():
            write(path, "\n".join(want[name]))

        # The deploy block is written by `scripts/deploy.sh` and quoted by the README, so the
        # fixture has to carry both ends or the check reports the fixture rather than the entry.
        # The first line names the cluster and the time and is not quoted; the three after it are.
        deploy = ["cluster devnet, 2020-01-01T00:00:00Z",
                  "built            1000 bytes, sha256 0123456789abcdef",
                  "deployed         1100 bytes, 100 of trailing padding",
                  "deployed ELF     identical to the tested binary"]
        write(published.DEPLOY_BLOCK, "\n".join(deploy) + "\n")

        # `stale` checks the allow-list in both directions now, so a fixture README carrying only
        # the build's own figures reports every allowance as dead. The check is right and the
        # fixture is too small, so the fixture carries the allow-listed prose as well, each figure in
        # a form its scanner reaches: percentages bare, the pair as `27 of the 108` rather than
        # `27 of 108`, and the duration as `33.6 days`. It lives inside `readme_fixture` rather than
        # being appended once at the call site, because most cases below rewrite the README, and a
        # variant that dropped this line would report eight dead allowances instead of the one
        # figure the case is about. The three prose-only percentages are absent on purpose: they are
        # declared as documentation and are expected not to fire.
        # `25%` and `1,000%` were on this line until 21 September 2026, when the README rebuild
        # dropped the two sentences they belonged to and the entries went with them. `$500`,
        # `$800` and `99 of 99` arrived in the same rebuild, from the business paragraph, the track
        # record and the verification table. The fixture has to move with the allow-list in both
        # directions or the suite reports the fixture rather than the entry, which is what it did
        # when the three new entries were added and this line was not.
        allowances = ("$9,000. 20% or 30% or 63% or 93.5%. $500 a month. "
                      "99 of 99 as expected. 27 of the 108 repositories. 33.6 days.")

        def readme_fixture(*replace):
            """The README fixture, deploy block and allow-listed prose included."""
            text = "\n".join(want["readme"] + deploy[1:]) + "\n" + allowances
            for old, new in replace:
                text = text.replace(old, new)
            return text

        write(published.SURFACES["readme"], readme_fixture())

        print()
        print("  the hand-written surfaces, against a build whose figures they carry")
        case("every figure matches, so nothing is reported", published.stale(sample, tmp), [])

        # The two price-divergence figures, which no class in the absence rule reaches because the
        # README prints them as bare integers. Asserted on the value, because the README fixture is
        # generated from `figures` and would agree with a wrong derivation.
        case("the widest divergence is the wider of the two directions, not reference over pool",
             published.widest_divergence(sample), 10)
        case("  ... and the count is the length of the list", len(sample["price_diverged"]), 2)

        # The guard, which is the half that turns a dropped requirement into a complaint. A build
        # with no `price_diverged` makes `figures` ask for neither figure, and this is what says so
        # rather than letting the README's second contribution go unchecked in silence.
        no_divergence = dict(sample)
        no_divergence.pop("price_diverged")
        case("negative control: a build that cannot produce the divergence figures is reported",
             len(published.missing_divergence(no_divergence)), 1)
        case("  ... and it asks for both figures", "both figures are unchecked"
             in published.missing_divergence(no_divergence)[0], True)
        case("  ... while a build that can produce them is not reported",
             published.missing_divergence(sample), [])

        # The allow-list, in the direction that was missing. An entry that neither admits a figure
        # nor is declared as documentation is a claim about the README that nothing tests, which is
        # how `74%` outlived its sentence and how `$1.8m` came to be explained as a different
        # figure from the one it let through.
        allow_readme = open(os.path.join(tmp, published.SURFACES["readme"])).read()
        case("every allowance either fires or is declared, so nothing is reported",
             published.unused_allowances(sample, allow_readme), [])

        real_allow = dict(published.README_ALLOWED)
        try:
            published.README_ALLOWED["percent"] = dict(real_allow["percent"])
            published.README_ALLOWED["percent"]["74%"] = "a rounded reading of something"
            off = published.unused_allowances(sample, allow_readme)
        finally:
            published.README_ALLOWED.clear()
            published.README_ALLOWED.update(real_allow)
        case("negative control: an entry the README does not carry is reported", len(off), 1)
        case("  ... and the complaint says the README does not carry it",
             "'74%', which does not fire: the README does not carry it" in (off[0] if off else ""),
             True)

        # The other failure mode, which is the worse one: an entry that fires but explains a figure
        # it is not about. It cannot be caught from the README alone, so what is checked instead is
        # that every entry is *reachable*, and the explanations are read by a human. This case pins
        # the reachability half: removing the entry that admits a figure has to produce a complaint
        # naming that figure, or the entry was never doing anything.
        try:
            published.README_ALLOWED["percent"] = dict(real_allow["percent"])
            del published.README_ALLOWED["percent"]["93.5%"]
            off = published.underivable(sample, allow_readme)
        finally:
            published.README_ALLOWED.clear()
            published.README_ALLOWED.update(real_allow)
        case("negative control: dropping the entry that admits a figure is reported", len(off), 1)
        case("  ... and it names the figure", "'93.5%'" in (off[0] if off else ""), True)

        # A declaration about an entry that does not exist is a comment about nothing, and it would
        # otherwise be a way to silence the check by typo.
        real_doc = dict(published.README_ALLOWED_DOC_ONLY)
        try:
            published.README_ALLOWED_DOC_ONLY[("percent", "99%")] = "nothing"
            off = published.unused_allowances(sample, allow_readme)
        finally:
            published.README_ALLOWED_DOC_ONLY.clear()
            published.README_ALLOWED_DOC_ONLY.update(real_doc)
        case("negative control: a declaration the allow-list does not carry is reported",
             len(off), 1)
        case("  ... and it says the declaration is about nothing",
             "which the allow-list does not carry, so the declaration is about nothing"
             in (off[0] if off else ""), True)

        # One figure moved by a tenth of a point, which is the size of move a second build on the
        # same day actually produces. If this passes, the check cannot catch its own drift.
        write(published.SURFACES["banner"],
              "\n".join(want["banner"]).replace("25.0% withheld", "24.9% withheld"))
        off = published.stale(sample, tmp)
        case("one figure moved by a tenth of a point is reported", len(off), 1)
        case("  ... and it names the diagram and the figure it wanted",
             off[0] if off else None,
             "docs/brand/readme-banner.svg should say '25.0% withheld'")

        # The other direction: the money is right and a count is wrong. Both diagrams carry the
        # mint count, so a count that is wrong has to be reported in both of them.
        write(published.SURFACES["banner"], "\n".join(want["banner"]))
        stale_counts = dict(sample)
        stale_counts["mints"] = {"total": 927, "live_differs_from_base": 999}
        off = published.stale(stale_counts, tmp, surfaces=("banner", "architecture"))
        case("a stale mint count is reported in both diagrams", len(off), 2)
        case("  ... and the architecture diagram is checked, not skipped",
             "docs/architecture.svg should say '999 of 927 mints differ here'" in off, True)

        # The README is a separate surface and has to be checked as one. Drop a single figure from
        # it and leave both diagrams correct: only the README may be reported, and it is reported
        # twice, because a figure the build no longer carries is both a figure that is missing and
        # a figure that is there and wrong. The second complaint is the absence rule.
        write(published.SURFACES["readme"],
              readme_fixture(("1.54% of the book", "1.53% of the book")))
        off = published.stale(sample, tmp, surfaces=("readme",))
        case("a README figure that drifted is reported on its own", off,
             ["README.md should say '1.54% of the book'",
              "README.md carries '1.53%', which data.json does not produce and the allow-list "
              "does not explain"])

        # The absence rule needs a control of its own, and this is the case that separates it from
        # every check above. Presence is blind to a second, contradicting copy: the review of
        # 17 September changed the second `1.035` in the README to `1.135` and the page built clean,
        # because the first copy was still there and every check asked whether a figure was present
        # rather than whether the README said one thing. The fixture carries `1.035` once, so the
        # control *adds* a contradicting copy rather than replacing one, and leaves the first in
        # place. If this passes, the rule is not testing what it claims to.
        write(published.SURFACES["readme"], readme_fixture() + "\n1.135\n")
        off = published.stale(sample, tmp, surfaces=("readme",))
        case("negative control: a contradicting second copy is reported where the first is fine",
             off,
             ["README.md carries '1.135', which data.json does not produce and the allow-list "
              "does not explain"])

        # The median activation age is the one figure on the README's first screen carrying a single
        # decimal, so none of the classes the absence rule scanned reached it. It has two checks
        # now, and both fire here: the requirement reports the figure it wanted, and the duration
        # class reports the figure it found, because a value the build does not derive is not one it
        # can account for. That pair is the intended behaviour, and asserting only the first would
        # have hidden the second.
        write(published.SURFACES["readme"],
              readme_fixture(("median age 10.0 days", "median age 29.9 days")))
        case("a moved median activation age is reported, by the requirement and by the rule",
             published.stale(sample, tmp, surfaces=("readme",)),
             ["README.md should say 'median age 10.0 days'",
              "README.md carries '29.9', which data.json does not produce and the allow-list does "
              "not explain"])

        # And the direction that would otherwise pass in silence. `figures` asks the README for the
        # figure only when the build can derive it, so a build that lost `built_utc` would drop the
        # requirement and every case in this file would still print `ok`. The README here is written
        # without the duration, so that the absence rule, which has no derivation to explain one
        # either, does not add a second complaint about the same absence: this case is about one
        # check, so it isolates one. The allow-listed prose stays, for the same reason, since a
        # README without it would add eight dead-allowance complaints and stop isolating anything.
        without_age = [w for w in want["readme"]
                       if not w.startswith("median age") and "tokens priced here" not in w]
        write(published.SURFACES["readme"],
              "\n".join(without_age + deploy[1:]) + "\n" + allowances)
        off = published.stale({k: v for k, v in sample.items() if k != "built_utc"}, tmp,
                              surfaces=("readme",))
        case("negative control: a build that cannot derive the age says so, rather than passing",
             off,
             ["README.md quotes a median activation age and this build cannot produce one, so the "
              "figure is unchecked; it needs `built_utc` and a `tokens` list carrying `effective_at`"])

        # The duration class, the absence rule's fifth and the one that had been missing. A second
        # copy is what it is for: the case above catches the figure moving, and this catches a
        # contradicting copy standing beside a correct one, which is the failure the review of
        # 17 September produced with `1.135` and which presence alone cannot see.
        write(published.SURFACES["readme"],
              readme_fixture(("median age 10.0 days",
                              "median age 10.0 days, and the oldest is 41.2 days")))
        off = published.stale(sample, tmp, surfaces=("readme",))
        case("negative control: a second, contradicting duration is reported",
             off,
             ["README.md carries '41.2', which data.json does not produce and the allow-list does "
              "not explain"])

        # The README's first bold line, which is the headline claim of the whole entry and which no
        # check reached until the review of 21 September falsified it. Each figure below is real and
        # sits one key away from a different real figure in the same object: 26.2% is withheld over
        # gross rather than the rate applied, 628 is the UTC-window count rather than the calendar
        # one, and 370 of the 400 pairs a mint count with a withholding count. So none of the three
        # mutations is a typo, and a build that shipped any of them would read as a correct sentence.
        # One case per figure rather than one for the sentence, because each is a separate string
        # and a single case would pass with two of the three pins missing.
        write(published.SURFACES["readme"],
              readme_fixture(("377 of the 400 tokenized stocks", "370 of the 400 tokenized stocks")))
        case("negative control: the headline's rated pair moved is reported",
             published.stale(sample, tmp, surfaces=("readme",)),
             ["README.md should say '377 of the 400 tokenized stocks'"])

        # The pair case above reports one complaint and not two, and that is the point of pinning it
        # rather than extending the absence rule. `derivable` builds its `N of M` set as the full
        # cross-product of every count in the build, so "370 of the 400" is admitted the moment 370
        # and 400 are both real counts, which they are: 370 is the mint count and 400 the rated
        # symbol count. The rule cannot tell the pair that means something from the pair that means
        # nothing, and the comment on that loop already says so. The rate case below is the opposite
        # and is worth reading beside this one: 26.2% is not a ratio any two counts in this fixture
        # produce, so the percent class fires and the pin is the second complaint rather than the
        # only one.

        write(published.SURFACES["readme"],
              readme_fixture(("lose 30% of every dividend", "lose 26.2% of every dividend")))
        case("negative control: the headline's rate moved is reported",
             published.stale(sample, tmp, surfaces=("readme",)),
             ["README.md should say 'lose 30% of every dividend'",
              "README.md carries '26.2%', which data.json does not produce and the allow-list "
              "does not explain"])

        write(published.SURFACES["readme"],
              readme_fixture(("631 fall outside the US regular session",
                              "628 fall outside the US regular session")))
        case("negative control: the calendar count moved is reported",
             published.stale(sample, tmp, surfaces=("readme",)),
             ["README.md should say '631 fall outside the US regular session'"])

        # The rounding rule on the pence share, asserted at a boundary where the paths actually
        # differ rather than at one where they happen to agree. Measured: 0.02675 * 100 prints as
        # 2.67 through a float and as 2.68 half up, so a fixture at that value separates the rule
        # from the accident. This is the shape the committed README carried: it said 1.53% where
        # 0.01535 is 1.535.
        contested = json.loads(json.dumps(sample))
        contested["currency"]["pence_share_of_book"] = 0.02675
        case("the pence share is rounded half up, not through a float",
             [f for f in published.figures(contested)["readme"] if f.endswith("of the book")],
             ["2.68% of the book"])
        case("  ... and a float would have asked the README for a different figure",
             "%.2f%% of the book" % (0.02675 * 100), "2.67% of the book")
        case("  ... so the figure the README is asked for is the half-up one, not the float one",
             "2.68% of the book" in published.figures(contested)["readme"], True)

        # The banner's worked example: a pair that is in the build passes, a pair that is not is
        # reported. Ten decimals, so a truncated or rounded pair is caught too.
        write(published.SURFACES["banner"], "1.0344000942 1.0268028385")
        case("a worked example drawn from this build is accepted",
             published.stale_banner_example(sample, tmp), [])
        write(published.SURFACES["banner"], "1.0344000942 1.0268028300")
        case("a worked example that is not in this build is reported",
             published.stale_banner_example(sample, tmp),
             ["docs/brand/readme-banner.svg shows 1.0268028300, which is not a multiplier in this build"])
        write(published.SURFACES["banner"], "no figures here at all")
        case("a banner with no worked example is reported rather than passed",
             published.stale_banner_example(sample, tmp),
             ["docs/brand/readme-banner.svg shows no ten-decimal value, "
              "so the receipt has lost its worked example"])

        # The page and the banner have to print the same figure. This is the check that was missing
        # rather than wrong: the first two ask whether each hand-written surface describes *this
        # build*, and neither asked whether two surfaces quoting one build agree with *each other*.
        # They did not. Each had its own idea of ten decimal places, one rounding and one cutting
        # short, so the worked example read 1.0344000942 / 1.0268028385 on the banner and
        # 1.0344000941 / 1.0268028384 in the page's receipt and ledger. The banner's two figures
        # appeared nowhere in index.html at all.
        write(published.SURFACES["banner"], "1.0344000942 1.0268028385")
        case("the page and the banner agree when they print the same digits",
             published.page_agrees_with_banner("receipt 1.0344000942 and 1.0268028385", tmp), [])
        case("negative control: a page printing the cut-short pair is reported",
             published.page_agrees_with_banner("receipt 1.0344000941 and 1.0268028384", tmp),
             ["docs/brand/readme-banner.svg prints 1.0268028385 and index.html does not, so the "
              "two surfaces disagree about the same figure",
              "docs/brand/readme-banner.svg prints 1.0344000942 and index.html does not, so the "
              "two surfaces disagree about the same figure"])
        case("negative control: a page carrying only one of the two is reported",
             len(published.page_agrees_with_banner("receipt 1.0344000942 only", tmp)), 1)

        # Rounding against cutting short, at values where the two actually differ rather than at
        # ones where they happen to agree. These three are the values this entry's own docstrings
        # named, so the rule is asserted against the examples that were already in the tree.
        boundary = ("1.0216625054701978", "1.0344000941634355", "1.0268028384810615")
        case("a multiplier is rounded, not cut short",
             [published.multiplier(v) for v in boundary],
             ["1.0216625055", "1.0344000942", "1.0268028385"])
        case("negative control: the old form gives a different answer on the same three",
             [to_places_old(v, 10) for v in boundary],
             ["1.0216625054", "1.0344000941", "1.0268028384"])
        case("  ... and the rounded form is the one the banner check accepts",
             published.stale_banner_example(sample, tmp), [])
        # The other direction, which is what makes the rounding decision load-bearing rather than
        # cosmetic: a banner written from the cut-short values is refused. If this passed, the two
        # forms would both be accepted and the page and the banner could drift apart again.
        write(published.SURFACES["banner"], " ".join(to_places_old(v, 10) for v in boundary))
        case("negative control: a banner carrying the cut-short pair is refused",
             published.stale_banner_example(sample, tmp),
             ["docs/brand/readme-banner.svg shows 1.0216625054, "
              "which is not a multiplier in this build",
              "docs/brand/readme-banner.svg shows 1.0268028384, "
              "which is not a multiplier in this build",
              "docs/brand/readme-banner.svg shows 1.0344000941, "
              "which is not a multiplier in this build"])
        case("trailing zeros are dropped, so a multiplier of one prints as one",
             [published.multiplier(v) for v in (1.0, "1.0000000000", 1)],
             ["1", "1", "1"])

        # ------------------------------------------------------------- the video script
        #
        # The recording is the one artefact in this entry that cannot be rebuilt from the tree. The
        # script's figures are typed, then read aloud, and nothing before this read the script at
        # all: a figure that moved in the fetch between the script being written and the camera
        # being turned on would be spoken on camera while the build contradicted it, and every other
        # check in this workspace would still pass.
        #
        # This control drives the real `published.spoken_figures` over a script this file writes, so
        # a figure can be moved here and seen to be reported. The beats are the real beats and the
        # spoken lines are the real lines, including the line breaks, because the check joins a
        # beat's blockquotes before matching and a fixture with no breaks would not exercise that.
        spoken_sample = {
            "withholding": {"top_rate_share": 0.9425, "rated_symbols": 400, "top_rate": "0.3",
                            "top_rate_symbols": 377},
            "money": {"annualised_withheld_usd": "11600000", "withheld_usd": "2620000",
                      "gross_usd": "10000000"},
            "actions": {"months": 6.6},
            "mints": {"total": 927, "live_differs_from_base": 370},
            "read_rule": {"agreed": 927, "disagreed": 0},
            "activation_timing": {"n": 640, "outside_calendar": 631,
                                  "on_a_non_trading_day": 57, "top_times": [["00:30", 407]]},
            "reconciliation": {"fresh_median": 0.0198, "stale_median": 0.0482,
                               "buckets": [{"label": "0-2 days", "n": 41, "median": 0.0198},
                                           {"label": "over 60 days", "n": 9, "median": 0.0482}]},
            # The pair the page's two-value card shows, which the mechanism beat's on-screen
            # direction quotes. Deliberately round, so the fixture is visibly a fixture: the real
            # pair is 1.0268028385 and 1.0344000942 and a failure could be mistaken for the build.
            "tokens": [{"symbol": "PEPx", "base_multiplier": 1.01, "live_multiplier": 1.02}],
        }

        def script_text(mint="three hundred and seventy", card="94.2%", wrap=True):
            """The script fixture: the real beats, with the figures this file moves made knobs.

            `wrap` puts the mechanism beat's pair on the line after its `**On screen:**` marker, which
            is how the real script has it. It is a knob so the continuation handling can be shown to
            matter rather than asserted to: see `screen_text_old` below.
            """
            pair = ("`1.01`, the right is `1.02`, with \"effective\" between them."
                    if wrap else "the two values.")
            return "\n".join([
                "## Part 1 — Prep, 45 minutes before recording",
                "",
                "Open the evidence page URL. Confirm the hero headline reads `Tokenized stocks pay",
                "dividends invisibly, 30% short, and at hours no market is open.`, the lede under it",
                "opens `370 of 927 mints`, the hero chip reads `withheld 26.2% of dividend income`,",
                "the first card reads `%s`, and the sentence in section 04 ends" % card,
                "`927 agreed, 0 disagreed`.",
                "",
                "## Part 2 — Exact screen layout",
                "",
                "Two windows, side by side.",
                "",
                "## Part 3 — Beat by beat",
                "",
                "### 0:00 The rate the issuer charges",
                "**On screen:** Window 1, the evidence page, top. Hero headline and the `%s` card."
                % card,
                "> Ninety-four percent of the four hundred stocks the issuer rates lose thirty",
                "> percent of every dividend before it is reinvested. Across six and a half months,",
                "> two point six million dollars withheld, about eleven point six million a year.",
                "",
                "### 1:00 The two candidates",
                "**On screen:** Window 1, scroll to section 02 and stop on the two-value card. The",
                "left value is %s" % pair,
                "> The dividend is not paid in cash. On %s of nine hundred and" % mint,
                "> twenty-seven mints, it is not the multiplier.",
                "",
                "### 1:20 The rule",
                "**On screen:** Window 1, the paragraph below the card, ending on `927 agreed,"
                " 0 disagreed`.",
                "> I compared the two on every mint rather than a sample. Nine hundred and",
                "> twenty-seven agreed, none disagreed.",
                "",
                "### 1:40 When they land",
                "**On screen:** Window 1, the timing block: the modal times and the two",
                "outside-the-session lines.",
                "> The modal move is half past midnight UTC. Of six hundred and forty",
                "> activations, six hundred and thirty-one land outside the exchange's own",
                "> session, and fifty-seven land on a day the market does not trade at all.",
                "",
                "### 2:00 The falsification test",
                "**On screen:** Window 1, section 05, the two age medians side by side.",
                "> It does: two percent in the first ten days, four point eight percent after sixty days.",
                "",
                "**(Total spoken: 100 words)**",
                "",
                "## Part 4 — After",
                "",
                "**Watch it back in full before uploading.** Check three things: the `%s` figure is"
                % card,
                "legible at 0:05, the word \"multiplier\" is on screen when it is spoken, and the",
                "desk's status chip reads `Live from devnet` on camera, not `Recorded`.",
                "",
                "**Title:** `Record Date: on 370 of 927 mints the obvious field is the previous"
                " one`",
                "",
                "**Description:**",
                "",
                "> 377 of the 400 tokenized stocks lose 30% of every dividend. About $11.6m a year.",
                "> On 370 of 927 mints the field named `multiplier` is not the multiplier.",
                "",
                "**Tags:** `solana`, `token-2022`",
                "",
                "**Last thing.** Re-run the fetch on submission day.",
            ])

        print()
        print("  the video script's spoken figures, against a build they carry")
        case("every spoken figure matches, so nothing is reported",
             published.spoken_figures(spoken_sample, script_text()), [])

        # The move that found the hole. This exact injection went into the real script and the build
        # did not refuse, because the check was asking whether the build's words were a substring of
        # the beat, and `three hundred and seventy` is a substring of `three hundred and seventy-one`.
        #
        # One complaint, and it is the only one. Until 21 September 2026 the money beat repeated the
        # mint count, so this injection produced one complaint only because the count was spoken
        # twice and both copies moved together. The rewrite folded the money into the opening beat
        # and left the count to the mechanism beat, so the count is now spoken once and this control
        # is a statement about a single surface rather than about two that happen to agree.
        off = published.spoken_figures(spoken_sample,
                                       script_text(mint="three hundred and seventy-one"))
        case("negative control: a count moved by one in the script is reported", len(off), 1)
        case("  ... and it names the beat and the figure the build holds", off[0] if off else None,
             "the video script's beat 1:00 should say 'three hundred and seventy' for the mints "
             "whose live value differs from the field named multiplier, and does not")

        # The two forms, side by side on the same input. This reaches into a private helper
        # deliberately: the comparison is between the form that is in the module and the form that
        # was, and only one of them is still in the module.
        case("negative control: the old substring form accepts the moved figure",
             spoken_present_old("three hundred and seventy",
                                "on three hundred and seventy-one of nine hundred"), True)
        case("  ... and the boundary form rejects it, which is why it replaced the substring",
             published._phrase_present("three hundred and seventy",
                                       "on three hundred and seventy-one of nine hundred"), False)

        # A beat the check cannot find is a figure nobody hears checked. The heading is the key, so
        # renaming it has to be reported rather than silently dropping the beat.
        off = published.spoken_figures(spoken_sample,
                                       script_text().replace("### 1:20 ", "### 1:21 "))
        case("negative control: a renamed beat leaves its figure unchecked, and says so",
             off, ["the video script has no beat starting at 1:20, so the read rule checked on "
                   "every mint is unchecked"])

        # The modal time is the one figure the module can hold no spoken form for, because it is the
        # only one whose vocabulary is finite. A silent None here would switch the check off exactly
        # when the modal time moved, which is when it is needed, so the None has to be a complaint.
        odd_hour = json.loads(json.dumps(spoken_sample))
        odd_hour["activation_timing"]["top_times"] = [["14:05", 407]]
        case("negative control: a modal time with no spoken form is reported, not skipped",
             published.spoken_figures(odd_hour, script_text()),
             ["the video script says nothing for when the activations land: this module has no "
              "rendering for the build's current value, so the check would pass without looking"])

        # ------------------------------------------------- what is on screen rather than said
        #
        # The first version of this check read only the blockquotes. The reviewer then named "the
        # 94.2% card" among the figures to verify, and a card is read by a viewer, so a stale card
        # fails exactly as a stale sentence does. It was uncovered, and the fix is not a second check
        # but the same check over the other lines: `published._figures` is shared by all three
        # surfaces so the derivation and the boundary rule cannot drift apart between them.
        print()
        print("  the video script's on-screen figures, against a build they carry")
        case("every on-screen figure matches, so nothing is reported",
             published.screen_figures(spoken_sample, script_text()), [])

        off = published.screen_figures(spoken_sample, script_text(card="94.0%"))
        case("negative control: a card moved in the script is reported", len(off), 1)
        case("  ... and it names the beat and the value the build holds", off[0] if off else None,
             "the video script's on-screen direction at 0:00 should carry '94.2%' for the card in "
             "the hero, which the opening beat holds the camera on, and does not")

        # The continuation bug, and the direction of it tested rather than assumed. The old parser
        # reads the marker's own line only, so it cannot see the pair on the next line, and a check
        # over it would refuse a correct script. A false refusal, not a silent pass: the mirror of the
        # substring case above, which is why both are kept rather than only the dangerous one.
        case("negative control: the old marker-only parser cannot see the pair, so a check over it "
             "would refuse a correct script",
             published._phrase_present("1.01", screen_text_old(script_text())["1:00"]), False)
        case("  ... and the block parser reads the continuation line, which is why it replaced it",
             "1.01" in published._screen_text(script_text())["1:00"], True)

        off = published.screen_figures(spoken_sample, script_text(wrap=False))
        case("negative control: a beat whose on-screen line lost its pair is reported", len(off), 2)

        # ------------------------------------------------- the prep section
        #
        # Not in the recording, and still worth checking: the prep section is a list of figures to
        # confirm on the page before the camera is turned on, and a step naming a figure the page no
        # longer shows costs the reader time on the morning they have least of it.
        print()
        print("  the video script's prep figures, against a build they carry")
        case("every prep figure matches, so nothing is reported",
             published.prep_figures(spoken_sample, script_text()), [])

        off = published.prep_figures(spoken_sample, script_text(card="94.0%"))
        case("negative control: a prep figure moved in the script is reported", len(off), 1)
        case("  ... and it names the figure and what it is for", off[0] if off else None,
             "the video script's prep section should tell the reader to confirm '94.2%' for the "
             "first card, and does not")

        # A script with no Part 1 is a check that has stopped looking, not a script with nothing to
        # check. The two would print the same if the section lookup returned an empty string instead
        # of None, which is why `published._part_text` returns None.
        no_prep = script_text().split("## Part 2")[1]
        case("negative control: a script with no prep section is reported, not skipped",
             published.prep_figures(spoken_sample, no_prep),
             ["the video script has no prep section, so the hero chip's withheld share is unchecked",
              "the video script has no prep section, so the first card is unchecked",
              "the video script has no prep section, so the hero lede's mint count is unchecked",
              "the video script has no prep section, so the read-rule sentence is unchecked"])

        # ------------------------------------------------- the upload notes
        #
        # The title and the description are the copy a judge reads before the video, and nothing read
        # them until 20 September 2026. Found by mutating one occurrence of a checked figure at a
        # time: with every occurrence moved at once the build refuses on the first problem it can
        # see, so a line no check reaches is never reported and reads as covered.
        print()
        print("  the video script's upload notes, against a build they carry")
        case("every upload-note figure matches, so nothing is reported",
             published.after_figures(spoken_sample, script_text()), [])

        off = published.after_figures(spoken_sample, script_text(card="94.0%"))
        case("negative control: the watch-back checklist's figure moved is reported", len(off), 1)
        case("  ... and it names the figure and what it is for", off[0] if off else None,
             "the video script's upload notes should carry '94.2%' for the watch-back checklist's "
             "first item, and do not")

        # The title and the description each carry the mint count, so one region over the whole
        # section would let either move unnoticed while the other satisfied the check. These two
        # controls are what makes the decomposition load-bearing rather than tidy: each moves one
        # copy and expects exactly one complaint, and a single-region version fails both.
        title_moved = script_text().replace("**Title:** `Record Date: on 370 of 927",
                                            "**Title:** `Record Date: on 371 of 927")
        off = published.after_figures(spoken_sample, title_moved)
        case("negative control: the title's mint count moved is reported", len(off), 1)
        case("  ... and the description's copy of it is not implicated", off[0] if off else None,
             "the video script's upload notes should carry '370 of 927' for the upload title's mint "
             "count, and do not")

        desc_moved = script_text().replace("> On 370 of 927 mints", "> On 371 of 927 mints")
        off = published.after_figures(spoken_sample, desc_moved)
        case("negative control: the description's mint count moved is reported", len(off), 1)
        case("  ... and it is the description that is named", off[0] if off else None,
             "the video script's upload notes should carry '370 of 927' for the description's mint "
             "count, which repeats the title's, and do not")

        off = published.after_figures(spoken_sample, script_text().replace("$11.6m", "$11.7m"))
        case("negative control: the description's annualised figure moved is reported", len(off), 1)

        # Upload notes whose label has gone are a check that has stopped looking, not a script with
        # nothing to check, for the reason the prep control above gives.
        case("negative control: upload notes with no title label are reported, not skipped",
             published.after_figures(spoken_sample,
                                     script_text().replace("**Title:**", "**Titel:**")),
             ["the video script's upload notes have no title, so the upload title's mint count is "
              "unchecked"])

    # ------------------------------------------------------------- the price record's key set
    #
    # `fetch_prices` normalises every mint into one dict and every caller reads it with `.get`, so a
    # key it forgets is `None` on every token rather than an error, and nothing fails. That is not
    # hypothetical. `liquidity` was absent from this dict for the whole 17 September build, so all
    # 379 tokens recorded `null` while the endpoint was answering with a value for 47 of them, and
    # the README quoted a pool depth off it. The field is carried now and this asserts the key set,
    # because a comment saying the dict is normalised in one place is not a check on that place.
    print()
    print("  the price record, whose key set every caller reads with .get")
    fetch = load_fetch()
    if fetch is None:
        case("fetch.py could not be loaded, so this control is skipped rather than passed",
             "skipped", "not skipped")
    else:
        payload = {"usdPrice": 12.5, "liquidity": 7164452.0, "stockData": {"price": 12.75}}
        rec = fetch.price_record(payload, 12.75, "stockData.price", 12.5, "USD")
        case("every key a caller reads is present",
             sorted(rec), sorted(["usd", "local", "source", "alt", "currency", "liquidity"]))
        case("  ... and liquidity survives the normalisation, which is what it did not do",
             rec.get("liquidity"), 7164452.0)
        case("  ... and the reference field is still read out of stockData",
             rec.get("local"), 12.75)
        case("negative control: a payload with no liquidity gives None rather than raising",
             fetch.price_record({"usdPrice": 1.0}, 1.0, "usdPrice", None, "USD").get("liquidity"),
             None)

    print()
    print("%d of %d as expected" % (passed, total))
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
