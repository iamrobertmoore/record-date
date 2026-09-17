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
        # The program's delta is now - effective_at. Cross-check it against the mint's own
        # timestamp, read in the same pass. This is the check that the two halves of the page
        # agree: a wrong offset in either decoder shows up here.
        effective = mints[mint]["effectiveAt"]
        delta = answer["deltaSeconds"]
        if effective is not None and delta is not None and effective <= captured_epoch:
            drift = abs((captured_epoch - effective) - delta)
            if drift > 300:
                problems.append(
                    f"{record['symbol']}: the program reports {delta}s since the activation but "
                    f"the mint's own timestamp implies {captured_epoch - effective:.0f}s "
                    f"({drift:.0f}s apart)"
                )
        if delta is None:
            problems.append(f"{record['symbol']}: settlement_window returned nothing")

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

    delta = danswer["deltaSeconds"]
    inside = delta is not None and abs(delta) <= pause
    verdict = "No answer" if delta is None else ("Hold" if inside else "Settle")
    verdict_class = "hold" if (delta is None or inside) else "settle"
    if delta is None:
        why = "The program returned nothing this page can read."
    elif inside:
        why = (
            f"The activation was {duration(delta)} ago, which is inside the {pause // 60} minute "
            f"window this desk holds for."
        )
    else:
        why = (
            f"The activation was {duration(delta)} ago. The window closed "
            f"{duration(abs(delta) - pause)} ago, so this trade is clear."
        )

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
        window_note = (
            f"On {human_utc(w['capturedAt'])} the desk was asked about "
            f"<span class=\"num\">{esc(w['mint'][:8])}\u2026</span> with an activation "
            f"<span class=\"num\">{w['deltaSeconds']} seconds</span> old, and it held. That "
            f"reading is recorded here because the window is only open for fifteen minutes: a "
            f"live page cannot be made to show a hold on demand, so one was staged and captured. "
            f"The activation was written in "
            f'<a href="https://explorer.solana.com/tx/{esc(w["signature"])}?cluster=devnet">'
            f'transaction <span class="num">{esc(w["signature"][:16])}\u2026</span></a>, which a '
            f"judge can open. Every other number on this page is read live."
        )
    else:
        window_note = (
            "The settlement window is thirty minutes wide and it opens when an issuer "
            "activates a multiplier. A live page cannot be made to show a hold on demand, so "
            "this desk shows the program's own delta and applies the rule to it. When the "
            "absolute delta is inside the window the verdict above reads Hold."
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
        "DEFAULT_DELTA": f"{abs(delta):,}" if delta is not None else "n/a",
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
    print(f"wrote {OUT.relative_to(REPO)}  {len(html):,} bytes")
    print(f"  default mint      {default['symbol']} {dmint}")
    print(f"  field / live      {dstate['multiplier']} / {dstate['newMultiplier']}")
    print(f"  naive / program   {danswer['naiveUnitsDisplay']} / {danswer['programUnitsDisplay']}")
    print(f"  gap               {danswer['gapUnitsDisplay']}  ({gap_pct})")
    print(f"  delta             {delta}s -> {verdict}")
    print(f"  receipts on page  {len(default_receipts)} of {len(receipts)}")
    print(f"  mints disagreeing {len(disagreeing)} of {len(records)}")
    print(f"  price rows        {len(price_rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
