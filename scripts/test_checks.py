#!/usr/bin/env python3
"""Negative controls for the two checks in fetch.py that were rewritten.

A check that cannot fail for the reason it claims is worse than no check, because it looks like
rigour. Both of these were in that state. This file holds the old form of each next to the new
form, on inputs built to separate them, so the difference is auditable rather than asserted.

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

from statistics import median


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
    print("%d of %d as expected" % (passed, total))
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
