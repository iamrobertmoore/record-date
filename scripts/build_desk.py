#!/usr/bin/env python3
"""Build `desk.html` from `desk-data.json`.

The desk is a static page with no backend, so everything it can say before it talks to the
chain has to be in the file. This bakes the captured devnet state into the markup, which is
what a reader sees with JavaScript switched off and what a judge sees if the RPC is
unreachable. The page's own script then re-reads the same accounts and replaces these values.

Like `build_page.py`, this asserts the claims it is about to publish and refuses to write the
page if one fails. A page that cannot be built is better than a page that is wrong.
"""

import datetime
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
DATA = REPO / "desk-data.json"
TEMPLATE = HERE / "desk_template.html"
OUT = REPO / "desk.html"
WINDOW = HERE / "desk-window.json"

# The program's own error variants, in declaration order, so the code is 6000 + index.
ERROR_NAMES = [
    "NotToken2022", "MintTooShort", "MintPaddingNotZero", "NotAMint", "NoScaledUiAmount",
    "BadExtensionLength", "UnknownExtension", "ExtensionOverrunsAccount", "BadMultiplier",
    "SymbolTooLong", "NotRegistered", "NothingToRecord", "NotPythReceiver", "NotAPriceUpdate",
    "PriceUpdateTooShort", "FeedIdMismatch", "StalePythPrice", "PriceDeviation", "PriceOutOfRange",
    "NegativePythPrice", "FeedSymbolTooLong", "PriceAgeCeilingExceeded",
    # Added 17 Sep 2026, with the whole-TLV-area walk. Anchor numbers these positionally from
    # 6000, so a new variant goes on the end and nothing above it moves. This list is a manual
    # mirror of the enum in `programs/record_date/src/error.rs`, and it silently went one short
    # the moment the variant was added, which is the argument for asserting the order rather
    # than trusting it.
    "TruncatedExtensionHeader",
]
# Anchor's framework errors, which is what a missing signer produces.
ANCHOR_ERRORS = {
    3010: "AccountNotSigner", 3011: "AccountNotSystemOwned", 3012: "AccountNotInitialized",
    3007: "AccountOwnedByWrongProgram", 3006: "AccountNotMutable",
}


def error_name(code, offset=6000):
    if code is None:
        return None
    if code in ANCHOR_ERRORS:
        return ANCHOR_ERRORS[code]
    i = code - offset
    return ERROR_NAMES[i] if 0 <= i < len(ERROR_NAMES) else f"error {code}"


def esc(value):
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def iso(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def human_utc(iso_string):
    return iso_string.replace("T", " ").replace(".000Z", "").replace("Z", " UTC")


def duration(secs):
    a = abs(secs)
    if a < 60:
        return f"{a:.0f} seconds"
    if a < 3600:
        return f"{a / 60:.1f} minutes"
    if a < 86400:
        return f"{a / 3600:.1f} hours"
    return f"{a / 86400:.1f} days"


def main():
    data = json.loads(DATA.read_text())
    template = TEMPLATE.read_text()

    captured_at = data["capturedAt"]
    captured_epoch = datetime.datetime.fromisoformat(
        captured_at.replace("Z", "+00:00")
    ).timestamp()
    records = data["tokenRecords"]
    mints = data["mints"]
    answers = data["answers"]
    receipts = data["receipts"]
    pause = data["pauseSecs"]

    problems = []

    # ---------------------------------------------------------------- assertions
    # Each of these can fail for the reason it claims, and each guards a number that goes on
    # the page. A build that cannot check its own claims should not publish them.

    if len(records) != data["registry"]["mints"]:
        problems.append(
            f"the registry counts {data['registry']['mints']} mints but {len(records)} token "
            f"records were found"
        )

    if len(receipts) != data["registry"]["activations"]:
        problems.append(
            f"the registry counts {data['registry']['activations']} activations but "
            f"{len(receipts)} receipts were found"
        )

    for record in records:
        mint = record["mint"]
        if mint not in mints:
            problems.append(f"token record for {mint} has no mint account")
            continue
        if mint not in answers:
            problems.append(f"no program answer recorded for {mint}")
            continue
        answer = answers[mint]
        # Two numbers, and each is cross-checked against a different account read in the same
        # pass. Token-2022 carries exactly one timestamp, `new_multiplier_effective_timestamp`,
        # and the program splits it by whether it has passed: the seconds since the value in force
        # took effect, or the seconds until the staged one does. So the mint's own field decides
        # which branch is correct, and a wrong offset in either decoder, or the wrong branch,
        # shows up here rather than on the page.
        since = answer.get("sinceSeconds")
        until = answer.get("untilSeconds")
        if since is None or until is None:
            problems.append(
                f"{record['symbol']}: settlement_window returned nothing this build can read"
            )
            continue
        ts = mints[mint]["effectiveAt"]
        if ts is None:
            problems.append(f"{record['symbol']}: the mint has no ScaledUiAmountConfig to read")
            continue
        if ts <= captured_epoch:
            # The value in force is the mint's own, so the mint can date it and `since` is a
            # duration. Nothing is staged, and `until` must say so rather than say zero.
            implied = captured_epoch - ts
            if until != -1:
                problems.append(
                    f"{record['symbol']}: the mint's timestamp has passed, so nothing is staged, "
                    f"but the program reports {until}s until the next activation"
                )
            if abs(since - implied) > 300:
                problems.append(
                    f"{record['symbol']}: the program reports {since}s since the activation but "
                    f"the mint's own timestamp implies {implied:.0f}s "
                    f"({abs(since - implied):.0f}s apart)"
                )
        else:
            # Staged. The mint has overwritten the timestamp of the value in force with the one
            # that has not arrived, so `since` can only come from the program's own record, and
            # the check is that it agrees with the newest receipt for this mint rather than with
            # the mint. This is the branch that a single-number return could not express at all.
            implied = ts - captured_epoch
            if abs(until - implied) > 300:
                problems.append(
                    f"{record['symbol']}: the program reports {until}s until the staged activation "
                    f"but the mint's own timestamp implies {implied:.0f}s "
                    f"({abs(until - implied):.0f}s apart)"
                )
            trail = [r for r in receipts if r["mint"] == mint]
            kept = max((r["effectiveAt"] for r in trail), default=0)
            if kept <= 0:
                if since != -1:
                    problems.append(
                        f"{record['symbol']}: a value is staged and no receipt dates the value in "
                        f"force, so the program should report -1 and it reports {since}"
                    )
            elif abs(since - (captured_epoch - kept)) > 300:
                problems.append(
                    f"{record['symbol']}: a value is staged, so the program should report the "
                    f"newest receipt's {captured_epoch - kept:.0f}s since the activation, and it "
                    f"reports {since}s"
                )

    for binding in data["pyth"]["bindings"]:
        if binding["mint"] not in mints:
            problems.append(f"a Pyth binding names {binding['mint']}, which is not registered")

    if problems:
        print("refusing to write the desk:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    # ---------------------------------------------------------------- the default mint

    # Chosen deliberately rather than taken from whatever order the RPC returned. The page a
    # judge lands on should be the strongest one: a mint where the obvious field is wrong, so
    # the money gap is visible, and which carries a Pyth binding, so the reference-price row is
    # not empty. Ties broken by receipt count, then by address, so the build is deterministic.
    disagreeing = [r for r in records if mints[r["mint"]]["fieldDisagrees"]]
    if not disagreeing:
        print("refusing to write the desk: no mint is in the state the entry is about, so the")
        print("page would have nothing to demonstrate")
        return 1
    bound = {b["mint"] for b in data["pyth"]["bindings"]}
    disagreeing.sort(
        key=lambda r: (
            0 if r["mint"] in bound else 1,
            -len([x for x in receipts if x["mint"] == r["mint"]]),
            r["mint"],
        )
    )
    default = disagreeing[0]
    dmint = default["mint"]
    dstate = mints[dmint]
    danswer = answers[dmint]
    decimals = default["decimals"]

    gap_pct = "n/a"
    if danswer["programUnits"] and danswer["naiveUnits"]:
        g = (int(danswer["programUnits"]) - int(danswer["naiveUnits"])) / int(
            danswer["programUnits"]
        ) * 100
        gap_pct = f"{g:.4f}".rstrip("0").rstrip(".") + "%"

    # Two numbers, because the issuer's pause window is two-sided. This mirrors the page's own
    # script word for word, so the value baked into the markup and the value the live read
    # replaces it with cannot say different things. `-1` is "nothing to report"; `0` is a real
    # answer, which is why the tests below are `>= 0` rather than truthiness.
    since = danswer.get("sinceSeconds")
    until = danswer.get("untilSeconds")
    after = since is not None and 0 <= since <= pause
    before = until is not None and 0 <= until <= pause
    inside = after or before
    answered = since is not None or until is not None

    delta = "n/a"
    if answered:
        delta = (
            "no activation yet" if since is None or since < 0 else f"{since:,}s ago"
        ) + " \u00b7 " + (
            "nothing staged" if until is None or until < 0 else f"in {until:,}s"
        )

    verdict = "No answer" if not answered else ("Hold" if inside else "Settle")
    verdict_class = "hold" if (not answered or inside) else "settle"
    if not answered:
        why = "The program returned nothing this page can read."
    elif inside:
        why = (
            f"The issuer has staged an activation that takes effect in {duration(until)}, which "
            f"is inside the {pause // 60} minute window this desk holds for."
            if before
            else f"The activation was {duration(since)} ago, which is inside the "
            f"{pause // 60} minute window this desk holds for."
        )
    elif after or before:
        why = "The nearest activation is inside the window."
    elif since >= 0:
        # When the activation is long past, the age and the time since the window closed round to
        # the same string and the sentence read "1.1 days ago. The window closed 1.1 days ago",
        # which looks like a bug even though both halves are true. Compare the rendered strings
        # rather than pick a threshold, and keep this identical to the page's own script.
        age_text = duration(since)
        closed_text = duration(since - pause)
        why = (
            f"The activation was {age_text} ago, which is outside the {pause // 60} minute window "
            f"this desk holds for, so this trade is clear."
            if closed_text == age_text
            else f"The activation was {age_text} ago. The window closed {closed_text} ago, so "
            f"this trade is clear."
        )
    else:
        why = "No activation is near, so this trade is clear."

    # ---------------------------------------------------------------- the mint picker

    buttons = []
    for record in records:
        mint = record["mint"]
        state = mints[mint]
        flag = '<span class="flag"></span>' if state["fieldDisagrees"] else ""
        pressed = "true" if mint == dmint else "false"
        label = record["symbol"] or mint[:6]
        buttons.append(
            f'        <button type="button" data-mint="{esc(mint)}" aria-pressed="{pressed}"'
            f' title="{esc(mint)}">{flag}{esc(label)} \u00b7 {esc(mint[:4])}\u2026</button>'
        )

    # ---------------------------------------------------------------- the receipt trail

    def receipt_rows(mint):
        rows = sorted(
            [r for r in receipts if r["mint"] == mint],
            key=lambda r: (r["effectiveAt"], r["sequence"]),
        )
        if not rows:
            return (
                '<tr><td colspan="8" class="small">No receipts for this mint yet. The program '
                "writes one per activation, so an empty trail means it has not recorded an "
                "activation for this token.</td></tr>"
            )
        out = []
        for r in rows:
            tag = (
                '<span class="pill flat">no change</span>'
                if r["flat"]
                else '<span class="pill ok">moved</span>'
            )
            out.append(
                "            <tr>"
                f'<td class="mono">{r["sequence"]}</td>'
                f"<td>{tag}</td>"
                f'<td class="mono">{esc(r["previousMultiplier"])}</td>'
                f'<td class="mono">{esc(r["newMultiplier"])}</td>'
                f'<td class="mono">+{esc(r["deltaFp"])}</td>'
                f'<td>{iso(r["effectiveAt"])}</td>'
                f'<td>{iso(r["recordedAt"])}</td>'
                f'<td class="mono">{r["rawSupply"]:,}</td>'
                "</tr>"
            )
        return "\n".join(out)

    default_receipts = [r for r in receipts if r["mint"] == dmint]
    flats = len([r for r in default_receipts if r["flat"]])
    trail_note = (
        f"{len(default_receipts)} receipt"
        + ("" if len(default_receipts) == 1 else "s")
        + " for this mint, written in order and never changed"
    )
    if flats:
        trail_note += (
            f". {flats} of them recorded no change in the multiplier, which the program keeps "
            f"rather than hides."
        )
    else:
        trail_note += "."
    trail_note += " Read from the program's own accounts, not from an index."

    # ---------------------------------------------------------------- the price table

    price_rows = []
    for binding in data["pyth"]["bindings"]:
        mint = binding["mint"]
        symbol = next((r["symbol"] for r in records if r["mint"] == mint), mint[:6])
        feed = data["pyth"]["feeds"].get(binding["feedId"]) or {}
        newest = feed.get("newest")
        verify = feed.get("verify") or {}
        if not newest:
            price_rows.append(
                f"            <tr><td>{esc(symbol)}</td><td>{esc(binding['feedSymbol'])}</td>"
                f'<td colspan="3" class="small">no price account on this cluster</td>'
                f"<td><span class=\"pill flat\">no data</span></td></tr>"
            )
            continue
        age = captured_epoch - newest["publishTime"]
        if verify.get("err"):
            name = error_name(verify.get("errorCode"), data["errorCodeOffset"])
            tag = f'<span class="pill no">{esc(name)}</span>'
        else:
            tag = '<span class="pill ok">accepted</span>'
        price_rows.append(
            f"            <tr><td>{esc(symbol)}</td><td>{esc(binding['feedSymbol'])}</td>"
            f'<td class="mono">{esc(newest["address"])}</td>'
            f'<td class="mono">{iso(newest["publishTime"])}</td>'
            f'<td class="mono">{duration(age)}</td>'
            f"<td>{tag}</td></tr>"
        )

    # ---------------------------------------------------------------- the window note

    if WINDOW.exists():
        w = json.loads(WINDOW.read_text())
        # The capture was taken under the single-number return and is kept as it was recorded.
        # `sinceSeconds` is that same measured value under the two-number return; the arithmetic
        # is unchanged, so the number is quoted once and the note says which shape it came from
        # rather than restating it in the newer vocabulary and implying a measurement that was
        # never made.
        captured_since = w.get("sinceSeconds", w.get("deltaSeconds"))
        window_note = (
            f"On {human_utc(w['capturedAt'])} the desk was asked about "
            f"<span class=\"num\">{esc(w['mint'][:8])}\u2026</span> with an activation "
            f"<span class=\"num\">{captured_since} seconds</span> old and nothing staged, and it "
            f"held. That reading is recorded here because the window is only open for fifteen "
            f"minutes: a live page cannot be made to show a hold on demand, so one was staged and "
            f"captured. It predates the widening of the return from one number to two, and the "
            f"seconds-since arithmetic it exercised is the same arithmetic. The activation was "
            f"written in "
            f'<a href="https://explorer.solana.com/tx/{esc(w["signature"])}?cluster=devnet">'
            f'transaction <span class="num">{esc(w["signature"][:16])}\u2026</span></a>, which a '
            f"judge can open. Every other number on this page is read live."
        )
    else:
        window_note = (
            "The window is fifteen minutes wide and it opens when an issuer activates a "
            "multiplier. A live page cannot be made to show a hold on demand, so this desk shows "
            "the program's own two numbers and applies the rule to them. When either is inside "
            "the window the verdict above reads Hold."
        )

    # ---------------------------------------------------------------- the state blob

    state = dict(data)
    state["defaultMint"] = dmint
    state_json = json.dumps(state, separators=(",", ":"))
    # A `</script>` anywhere in the data would end the block early. Nothing in this data
    # contains one, and escaping the angle bracket makes that a property of the file rather
    # than of the data.
    state_json = state_json.replace("<", "\\u003c")

    # ---------------------------------------------------------------- fill

    values = {
        "PROGRAM_ID": data["programId"],
        "PROGRAM_ID_SHORT": data["programId"][:8] + "\u2026",
        "RPC": data["rpc"],
        "RPC_HOST": data["rpc"].replace("https://", ""),
        "DESK_PAYER": data["deskPayer"],
        "CAPTURED": human_utc(captured_at),
        "MINT_COUNT": len(records),
        "DISAGREE_COUNT": len(disagreeing),
        "STATUS_CLASS": "",
        "STATUS_LABEL": "Recorded",
        "STATUS_WHEN": f"capture from {human_utc(captured_at)}; this page re-reads devnet when "
                       f"it loads",
        "MINT_BUTTONS": "\n".join(buttons),
        "DEFAULT_FIELD": dstate["multiplier"],
        "DEFAULT_LIVE": dstate["newMultiplier"],
        "DEFAULT_EFFECTIVE": iso(dstate["effectiveAt"]) if dstate["effectiveAt"] else "no dated activation",
        "DEFAULT_AGREE": (
            "These two disagree, which is the trap."
            if dstate["fieldDisagrees"]
            else "These two agree, so the obvious read happens to be correct here."
        ),
        "DEFAULT_MINT": dmint,
        "DEFAULT_RECORD": default["address"],
        "DEFAULT_ACTIVATIONS": default["activations"],
        "DEFAULT_NAIVE": danswer["naiveUnitsDisplay"] or "n/a",
        "DEFAULT_PROGRAM": danswer["programUnitsDisplay"] or "n/a",
        "DEFAULT_GAP": danswer["gapUnitsDisplay"] or "n/a",
        "DEFAULT_GAP_NOTE": (
            "The obvious read and the program agree on this mint."
            if danswer["gapUnits"] in (None, "0")
            else f"raw \u00d7 {dstate['multiplier']} against the program's own answer."
        ),
        "DEFAULT_GAP_PCT": gap_pct,
        "DEFAULT_RAW": danswer["oneShareRaw"],
        "DEFAULT_DECIMALS": decimals,
        "DEFAULT_PROGRAM_HEX": (
            f"0x{int(danswer['programUnits']):032x}"
            if danswer["programUnits"]
            else "no return data"
        ),
        "DEFAULT_VERDICT": verdict,
        "DEFAULT_VERDICT_CLASS": verdict_class,
        "DEFAULT_VERDICT_WHY": why,
        "DEFAULT_DELTA": delta,
        "DEFAULT_DELTA_HEX": danswer.get("deltaHex") or "no return data",
        "PAUSE_SECS": pause,
        "PAUSE_MINUTES": pause // 60,
        "RECEIPT_ROWS": receipt_rows(dmint),
        "TRAIL_NOTE": trail_note,
        "PYTH_ROWS": "\n".join(price_rows),
        "WINDOW_NOTE": window_note,
        "STATE_JSON": state_json,
    }

    html = template
    for key, value in values.items():
        html = html.replace("{{" + key + "}}", str(value))

    leftover = [k for k in values if "{{" + k + "}}" in html]
    if leftover:
        print(f"refusing to write the desk: placeholders left unfilled: {leftover}")
        return 1
    if "{{" in html:
        # Only report the unfilled ones, and only as a warning, since a literal {{ in prose is
        # allowed. There is none here, so treat it as a failure.
        start = html.index("{{")
        print(f"refusing to write the desk: an unfilled placeholder near: {html[start:start + 40]!r}")
        return 1

    OUT.write_text(html)
    # The byte count, not the character count. `len(html)` counts characters, so on a page carrying
    # 22 non-ASCII characters it printed 75,625 for a 75,656-byte file. A figure labelled with a unit
    # it does not carry is the defect this entry is built on, and it was sitting in this build's own
    # output. `build_page.py` was already right.
    print(f"wrote {OUT.relative_to(REPO)}  {OUT.stat().st_size:,} bytes")
    print(f"  default mint      {default['symbol']} {dmint}")
    print(f"  field / live      {dstate['multiplier']} / {dstate['newMultiplier']}")
    print(f"  naive / program   {danswer['naiveUnitsDisplay']} / {danswer['programUnitsDisplay']}")
    print(f"  gap               {danswer['gapUnitsDisplay']}  ({gap_pct})")
    print(f"  window            {delta} -> {verdict}")
    print(f"  receipts on page  {len(default_receipts)} of {len(receipts)}")
    print(f"  mints disagreeing {len(disagreeing)} of {len(records)}")
    print(f"  price rows        {len(price_rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
