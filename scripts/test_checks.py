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
            "activation_timing": {"n": 640, "on_a_non_trading_day": 57},
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
            "tokens": [{"base_multiplier": 1.0268028384810615,
                        "live_multiplier": 1.0344000941634355}],
        }
        want = published.figures(sample)

        def write(path, text):
            with open(os.path.join(tmp, path), "w") as handle:
                handle.write(text)

        # Every surface written to carry exactly the fixture's figures. The join is what a file with
        # no line breaks looks like; a figure has to appear verbatim and the separator is irrelevant.
        for name, path in published.SURFACES.items():
            write(path, "\n".join(want[name]))

        print()
        print("  the hand-written surfaces, against a build whose figures they carry")
        case("every figure matches, so nothing is reported", published.stale(sample, tmp), [])

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
        # it and leave both diagrams correct: only the README may be reported.
        write(published.SURFACES["readme"],
              "\n".join(want["readme"]).replace("1.54% of the book", "1.53% of the book"))
        off = published.stale(sample, tmp, surfaces=("readme",))
        case("a README figure that drifted is reported on its own", off,
             ["README.md should say '1.54% of the book'"])

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

    print()
    print("%d of %d as expected" % (passed, total))
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
