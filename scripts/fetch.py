#!/usr/bin/env python3
"""Record Date: pull every number on the page, from public sources, with no key.

    python3 scripts/fetch.py            writes data.json and prints the findings

Five sources, all public and none authenticated:

  api.xstocks.fi  the issuer's own asset list and corporate-action feed
  Solana RPC      the mint accounts, read directly
  Jupiter         prices and liquidity
  docs.xstocks.fi the published mechanics, quoted rather than paraphrased
  Pyth            the US exchange calendar, and the session boundaries beside it

The script asserts the things the page claims. If an assertion fails the page is wrong and
the script says so, rather than publishing a stale number.
"""

import base64
import datetime
import json
import os
import struct
import sys
import time
import urllib.error
import urllib.request
import zoneinfo
from collections import Counter, defaultdict
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

XSTOCKS_API = "https://api.xstocks.fi/api/v2/public"
RPC = "https://api.mainnet-beta.solana.com"
JUPITER = "https://api.jup.ag/price/v3"
TOKEN_2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"

# The US regular session as a fixed UTC window, stated generously. It runs 13:30 to 20:00 UTC
# under EDT and 14:30 to 21:00 under EST, so this covers both with an hour to spare at each end.
# A wider window can only make the finding below harder to reach, which is the direction to err in.
#
# This is the loose bound, not the answer. The exchange's own calendar is fetched below and is the
# authoritative definition; the two are computed independently and the check requires both to clear
# the bar, because a window I chose and a calendar the exchange publishes are not the same claim.
US_SESSION_OPEN_UTC = 13 * 60          # 13:00
US_SESSION_CLOSE_UTC = 21 * 60         # 21:00

# Pyth publishes the exchange calendar alongside its feed directory, with no key. Its price values
# over HTTP are a different matter: /v2/updates/price/latest is 401 without a key, and a key without
# an equity grant is refused with 403 and the rule in the error body. So this build takes the
# session definition from Pyth and prices from elsewhere. That is worth being explicit about: the
# calendar is Pyth's, the numbers beside it are not.
#
# The on-chain route needs nothing from anyone, and the program uses it. Pyth posts every update to
# Solana as a `PriceUpdateV2` account owned by its receiver program, and a posted account is public
# state, so scripts/capture_pyth_fixture.py reads one over plain RPC with no credential. Verified
# 17 September 2026: the live mainnet AAPL account advanced its publish_time nine times in three
# minutes and was never more than 24 seconds old, sampled at 06:50 ET with the US session shut.
#
# Verified 17 September 2026, because the boundary matters and it moved:
#
#   /v2/price_feeds            free, no key. Carries `schedule` (the calendar) and `market_hours`
#                              (`is_open`, plus absolute `next_open` / `next_close` unix seconds).
#   /v2/updates/price/latest   401 unauthorized. Hermes required authentication from 26 Aug 2026.
#
# `market_hours` is Hermes metadata and is NOT in the on-chain price message, so a Solana program
# cannot read it: Pyth Core's `PriceFeedMessage` carries feed_id, price, conf, exponent,
# publish_time, prev_publish_time, ema_price and ema_conf, and no session field. The `MarketSession`
# type belongs to Pyth Pro (Lazer) on Sui and Iota. So the calendar is consumed off chain, and is
# checked against Pyth's own published session boundaries rather than read from a program.
PYTH_FEEDS = "https://hermes.pyth.network/v2/price_feeds"

# The ECB's daily reference rates, republished by Frankfurter, with no key. Needed because
# `stockData.price` is quoted in the underlying's own currency, not in dollars. See `to_usd`.
FX_RATES = "https://api.frankfurter.app/latest"


def usd(value, places=0):
    """A dollar figure with thousands separators, for anything a reader will see.

    The check details are printed on the page next to figures that are all formatted, so a raw
    6495809 sitting among them reads as debug output. Formatting happens here rather than in the
    page builder so data.json is legible too.
    """
    return "${:,.{p}f}".format(Decimal(value), p=places)

# Token-2022 `ScaledUiAmountConfig`: authority (32), multiplier (8), timestamp (8), new (8)
SCALED_UI_AMOUNT_LEN = 56
OFF_MULTIPLIER, OFF_EFFECTIVE_TS, OFF_NEW_MULTIPLIER = 32, 40, 48
MINT_BASE_LEN = 82
# The base region is always `Account::LEN` bytes, whichever base state it holds, because the
# program only writes TLV data after this point so that a mint and a token account cannot be
# confused. A mint fills only the first 82 of them. The other 83 are padding and must be zero.
BASE_REGION_LEN = 165
ACCOUNT_TYPE_MINT = 1
TLV_START = BASE_REGION_LEN + 1
# The highest value in the Token-2022 ExtensionType enum. A byte pair above this is not a header.
# There is deliberately no maximum extension *length* constant: the bound that matters is whether
# an entry fits inside the account, and that is derived from the account rather than chosen. The
# largest body the walk steps over on any live xStock mint is measured and reported as
# `layout.max_extension_len_seen`.
MAX_EXTENSION_TYPE = 27


def get_json(url, tries=6, timeout=90):
    last = None
    for attempt in range(tries):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "record-date"})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except Exception as exc:  # noqa: BLE001 - retry on anything transient
            last = exc
            time.sleep(2 + 2 * attempt)
    raise RuntimeError("gave up on %s: %s" % (url, last))


def rpc(method, params, tries=6):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    last = None
    for attempt in range(tries):
        try:
            request = urllib.request.Request(
                RPC, data=body.encode(), headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(request, timeout=90) as response:
                payload = json.load(response)
            if "error" in payload:
                raise RuntimeError(payload["error"])
            return payload["result"]
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(2 + 2 * attempt)
    raise RuntimeError("rpc %s failed: %s" % (method, last))


# ---------------------------------------------------------------- the issuer's own data


def fetch_assets():
    """Every xStock deployed on Solana.

    Paginated, and the page index is the trap. The two endpoints on this API do **not** agree:
    `/assets` is **0-indexed** (page 0 is a real page of 100, page 10 is empty) while
    `/corporate-actions/upcoming` is **1-indexed** (page 0 returns HTTP 400). Reading `/assets`
    from page 1 therefore drops a whole page and looks like nothing is wrong.

    This build did exactly that until 17 September 2026, and reported 827 mints where the API
    serves 927. The missing 100 were not sampled, they were absent: every figure computed over
    the field was computed over 89% of it. Found by disbelieving a jump from 777 to 827 in one
    hour, which is 50 new listings, and is not a thing that happens.

    So: start at page 0, loop until a page comes back empty, and refuse to accept a walk that
    stopped because it ran out of range rather than because the data ended.
    """
    rows = {}
    pages = 0
    for page in range(0, 40):
        payload = get_json(
            "%s/assets?pageSize=100&page=%d" % (XSTOCKS_API, page)
        )
        nodes = payload.get("nodes") or []
        if not nodes:
            break
        pages += 1
        for asset in nodes:
            for deployment in asset.get("deployments") or []:
                if deployment.get("network") != "Solana":
                    continue
                address = deployment.get("address")
                if not address:
                    continue
                rows[address] = {
                    "symbol": asset.get("symbol"),
                    "name": asset.get("name"),
                    "isin": asset.get("isin"),
                    "underlying": asset.get("underlyingSymbol"),
                    # The currency the underlying trades in, and the venue. Both are needed to
                    # read a price: the token is dollar-denominated but the reference price beside
                    # it is not, and 124 of the mints are not US listings.
                    "currency": (asset.get("underlying") or {}).get("currency") or "USD",
                    "venue": (
                        (asset.get("trading") or {}).get("exchange") or {}
                    ).get("abbreviation"),
                }
                break
    else:
        raise RuntimeError(
            "asset pagination stopped at the %d page cap rather than at the end of the data, "
            "so the walk is truncated" % pages
        )
    return rows


def fetch_corporate_actions():
    """Every published corporate action, scheduled and cancelled, past and future."""
    rows = []
    for page in range(1, 9):
        payload = get_json(
            "%s/corporate-actions/upcoming?pageSize=100&page=%d" % (XSTOCKS_API, page)
        )
        nodes = payload.get("nodes") or []
        if not nodes:
            break
        rows.extend(nodes)
    return rows


def fetch_multiplier_history(symbol):
    payload = get_json(
        "%s/assets/%s/multiplier/history?page=0&pageSize=25&network=Solana"
        % (XSTOCKS_API, symbol)
    )
    return payload.get("nodes") or []


def parse_schedule(text):
    """An exchange calendar, as (timezone, weekly[7], overrides{MMDD: session}).

    The grammar is `<TZ>;<Mon>..<Sun>;<MMDD>/<session>,...`, where a session is `HHMM-HHMM` in
    exchange-local time, `C` means closed, and the day list runs Monday first. An override replaces
    the weekly pattern for that date, which is how a holiday and a half day are both expressed.

    The overrides are forward-listed from whenever Pyth published the feed. So this is
    authoritative for the regular weekly session and for any date it names, and silent about a
    holiday that predates the listing. That limit is exactly why the UTC window is kept as well:
    the two are computed independently and the check requires both to agree that the market is
    shut, so a gap in one cannot carry the claim on its own.
    """
    parts = text.split(";")
    if len(parts) < 2:
        raise ValueError("not a schedule: %r" % text[:60])
    days = parts[1].split(",")
    if len(days) != 7:
        raise ValueError("expected 7 day sessions, got %d" % len(days))

    def session(token):
        token = token.strip()
        if token in ("C", "", "-"):
            return None
        opens, closes = token.split("-")
        return (int(opens[:2]) * 60 + int(opens[2:]), int(closes[:2]) * 60 + int(closes[2:]))

    weekly = [session(d) for d in days]
    overrides = {}
    for item in (parts[2].split(",") if len(parts) > 2 else []):
        item = item.strip()
        if not item:
            continue
        mmdd, _, body = item.partition("/")
        overrides[mmdd] = session(body)
    return parts[0], weekly, overrides


def _session_text(session):
    """A session as `HH:MM-HH:MM`, or `closed`. Only for the console and the page."""
    if session is None:
        return "closed"
    return "%02d:%02d-%02d:%02d" % (
        session[0] // 60, session[0] % 60, session[1] // 60, session[1] % 60
    )


def fetch_exchange_calendar():
    """The calendar Pyth publishes for US equities, and how many feeds carry it.

    The dominant schedule is taken rather than any one symbol's, because the whole US equity set
    shares one and taking the most common avoids picking a symbol that happens to trade elsewhere.

    Pyth also states the same session a second way, as absolute unix seconds for the next open and
    the next close. That is an independent description rather than a restatement of the grammar, so
    it is carried out of here to check `parse_schedule` against the publisher's own arithmetic.
    """
    feeds = get_json(PYTH_FEEDS)
    usd = [
        f for f in feeds
        if (f.get("attributes") or {}).get("asset_type") == "Equity"
        and f["attributes"].get("quote_currency") == "USD"
    ]
    counts = Counter(
        f["attributes"]["schedule"] for f in usd if f["attributes"].get("schedule")
    )
    if not counts:
        raise RuntimeError("Pyth returned no scheduled USD equity feeds")
    schedule, n = counts.most_common(1)[0]
    tz, weekly, overrides = parse_schedule(schedule)

    # The consensus across the feeds that carry this calendar. They describe one session, so they
    # should not disagree with each other; taking the mode means one stale listing cannot move it.
    bounds = Counter(
        (f["market_hours"]["next_open"], f["market_hours"]["next_close"])
        for f in usd
        if f["attributes"].get("schedule") == schedule
        and isinstance(f.get("market_hours"), dict)
        and f["market_hours"].get("next_open")
        and f["market_hours"].get("next_close")
    )
    if not bounds:
        raise RuntimeError("Pyth published no session boundaries for the US equity calendar")
    (next_open, next_close), n_bounds = bounds.most_common(1)[0]

    return {
        "timezone": tz,
        "weekly": weekly,
        "overrides": overrides,
        "feeds": len(usd),
        "feeds_on_this_schedule": n,
        "distinct_symbols": len({
            (f["attributes"].get("display_symbol") or "").upper()
            for f in usd if f["attributes"].get("display_symbol")
        }),
        "schedule": schedule,
        "next_open": next_open,
        "next_close": next_close,
        "feeds_agreeing_on_boundaries": n_bounds,
    }


def session_open(when, calendar):
    """Is the US regular session open at this instant, by the exchange's own calendar?"""
    local = when.astimezone(zoneinfo.ZoneInfo(calendar["timezone"]))
    key = "%02d%02d" % (local.month, local.day)
    session = calendar["overrides"].get(key, calendar["weekly"][local.weekday()])
    if session is None:
        return False
    minutes = local.hour * 60 + local.minute
    return session[0] <= minutes <= session[1]


def fetch_current_multiplier(symbol):
    """The issuer's own view of the multiplier in force, or None if it publishes none.

    `currentMultiplier` is the value the issuer says applies now. It is a separate statement from
    the mint account, which is what makes comparing the two worth doing.
    """
    payload = get_json("%s/assets/%s/multiplier?network=Solana" % (XSTOCKS_API, symbol))
    if not isinstance(payload, dict):
        return None
    value = payload.get("currentMultiplier")
    return None if value is None else float(value)


# ------------------------------------------------------------------ the mint, read directly


def find_scaled_ui_amount(data):
    """Offset of the ScaledUiAmountConfig body inside a Token-2022 mint account.

    The layout is not what the Token-2022 docs suggest. It is a 165 byte base region, then the
    account-type byte, then `(type: u16, length: u16, body)` entries. The mint itself occupies
    only the first 82 bytes of that region. Walking the extensions from byte 83, as the
    documented layout reads, lands in 83 bytes of padding and finds nothing.

    The 165 byte base region is deliberate: the program only writes TLV data after `Account::LEN`
    so that a mint and a token account cannot be confused. `interface/src/extension/mod.rs` in
    the Token-2022 source says so, and checks the padding is zero, as this does.

    Validated on every xStock mint on 16 September 2026: the padding is zero, the account-type
    byte is 1, and the walk lands on the ScaledUiAmountConfig header at byte 275 in every one.

    Returns None for anything that is not a mint with a scaled-ui-amount extension, rather than
    scanning past bytes that do not fit the layout.
    """
    if len(data) <= MINT_BASE_LEN:
        return None  # no extensions at all, so no account-type byte and nothing to find
    if len(data) < TLV_START:
        return None
    if any(data[MINT_BASE_LEN:BASE_REGION_LEN]):
        return None  # the padding is not zero, so the layout is not what is assumed
    if data[BASE_REGION_LEN] != ACCOUNT_TYPE_MINT:
        return None

    offset = TLV_START
    while offset + 4 <= len(data):
        ext_type, ext_len = struct.unpack_from("<HH", data, offset)
        # ExtensionType::Uninitialized. The initialised run has ended.
        if ext_type == 0:
            return None
        if ext_type > MAX_EXTENSION_TYPE:
            return None
        if ext_type == 25:
            if ext_len != SCALED_UI_AMOUNT_LEN:
                return None
            return offset + 4
        if offset + 4 + ext_len > len(data):
            return None
        offset += 4 + ext_len
    return None


def read_mint(data, now):
    """Supply, decimals, and the multiplier the mint actually means."""
    if len(data) < MINT_BASE_LEN:
        return None
    body_at = find_scaled_ui_amount(data)
    if body_at is None:
        return None
    body = data[body_at : body_at + SCALED_UI_AMOUNT_LEN]
    base = struct.unpack_from("<d", body, OFF_MULTIPLIER)[0]
    effective_at = struct.unpack_from("<q", body, OFF_EFFECTIVE_TS)[0]
    new = struct.unpack_from("<d", body, OFF_NEW_MULTIPLIER)[0]

    # The trap: `multiplier` is the previous value. `new_multiplier` is the live one, once its
    # timestamp has passed. A reader that takes the obvious field is one action behind.
    live = new if (new != 0.0 and effective_at <= now) else base
    return {
        "supply": struct.unpack_from("<Q", data, 36)[0],
        "decimals": data[44],
        "base": base,
        "live": live,
        "effective_at": effective_at if live == new else 0,
    }


def fetch_mints(mints, now):
    """Every Token-2022 mint, plus the evidence for the layout claim the parser rests on.

    The parser does not scan for the extension header. It computes it from a fixed 165 byte base
    region, and refuses the account if the padding there is not zero. That is only correct if the
    claim holds on the accounts in the wild, so the claim is counted here and asserted below
    rather than assumed.
    """
    out = {}
    layout = {
        "token2022_accounts": 0,
        "short_base_only": 0,
        "padding_zero": 0,
        "padding_not_zero": 0,
        "account_type_mint": 0,
        "account_type_other": 0,
        "scaled_header_offsets": {},
        "max_extension_len_seen": 0,
        "rejected_by_layout": 0,
        # Does a contiguous walk, stepping only over real headers, consume the account exactly?
        # If it does on every mint then the parser can be exact and the "advance one byte when a
        # header looks implausible" fallback can go. A fallback that can land on a different
        # plausible header is a way to return the wrong body without failing, which is worse than
        # failing. This measures before that is decided.
        "walk_exact": 0,
        "walk_overran": 0,
        "walk_undershot": 0,
        "walk_entries": 0,
    }
    addresses = list(mints)
    for start in range(0, len(addresses), 100):
        chunk = addresses[start : start + 100]
        values = rpc("getMultipleAccounts", [chunk, {"encoding": "base64"}])["value"]
        for address, value in zip(chunk, values):
            if not value or value.get("owner") != TOKEN_2022:
                continue
            data = base64.b64decode(value["data"][0])
            layout["token2022_accounts"] += 1

            if len(data) <= MINT_BASE_LEN:
                layout["short_base_only"] += 1
            elif len(data) >= TLV_START:
                if any(data[MINT_BASE_LEN:BASE_REGION_LEN]):
                    layout["padding_not_zero"] += 1
                else:
                    layout["padding_zero"] += 1
                if data[BASE_REGION_LEN] == ACCOUNT_TYPE_MINT:
                    layout["account_type_mint"] += 1
                else:
                    layout["account_type_other"] += 1

            # One walk, and it is the parser's walk: step from header to header, never skip a byte
            # looking for something that resembles a header. Two things are measured. Whether it
            # lands exactly on the end of the account, which is what licenses the parser to be
            # exact; and the largest body it steps over, which is the measurement behind the
            # comment in token2022.rs about how big a real extension body is.
            if len(data) >= TLV_START:
                end = TLV_START
                entries = 0
                while end + 4 <= len(data):
                    ext_type, ext_len = struct.unpack_from("<HH", data, end)
                    if ext_type == 0 or ext_type > MAX_EXTENSION_TYPE:
                        break
                    layout["max_extension_len_seen"] = max(
                        layout["max_extension_len_seen"], ext_len
                    )
                    end += 4 + ext_len
                    entries += 1
                layout["walk_entries"] = max(layout["walk_entries"], entries)
                if end == len(data):
                    layout["walk_exact"] += 1
                elif end > len(data):
                    layout["walk_overran"] += 1
                else:
                    layout["walk_undershot"] += 1

            body_at = find_scaled_ui_amount(data)
            if body_at is None:
                layout["rejected_by_layout"] += 1
                continue
            header = body_at - 4
            layout["scaled_header_offsets"][header] = (
                layout["scaled_header_offsets"].get(header, 0) + 1
            )
            parsed = read_mint(data, now)
            if parsed:
                parsed["len"] = len(data)
                out[address] = parsed
        print("  read %d of %d mints" % (min(start + 100, len(addresses)), len(addresses)))
    return out, layout


def verify_supply_against_rpc(addresses, now, stride=7):
    """Check the read rule against the RPC's own arithmetic, not the issuer's API.

    This is a second, independent witness for the same claim the read-rule census makes. The
    issuer's endpoint says which value the issuer considers current. `getTokenSupply` says which
    value the *runtime* considers current, because it returns the scaled ui amount: the RPC
    applies the multiplier itself before answering. If the field this program picks as live is the
    field the runtime applies, the two supplies agree.

    The comparison is per mint and both numbers are read back to back, because supply moves: a
    mint or a burn between two calls would show up as a disagreement that is really just time
    passing. The tolerance is one unit in the last decimal place the RPC reports, which is the
    precision it rounds its answer to, not slack.

    Sampled on a stride rather than run over all mints, because the call is not batchable and the
    census is already covered by the issuer-side check. The stride is fixed so the sample is the
    same on every run.
    """
    result = {"checked": 0, "agreed": 0, "disagreed": 0, "unavailable": 0, "examples": []}
    for address in addresses[::stride]:
        try:
            value = rpc("getAccountInfo", [address, {"encoding": "base64"}])["value"]
        except Exception:  # noqa: BLE001
            result["unavailable"] += 1
            continue
        if not value:
            result["unavailable"] += 1
            continue
        parsed = read_mint(base64.b64decode(value["data"][0]), now)
        if not parsed:
            result["unavailable"] += 1
            continue
        try:
            supply = rpc("getTokenSupply", [address])["value"]
        except Exception:  # noqa: BLE001
            result["unavailable"] += 1
            continue

        mine = (
            Decimal(parsed["supply"]) / (Decimal(10) ** parsed["decimals"])
            * Decimal(repr(parsed["live"]))
        )
        theirs = Decimal(supply["uiAmountString"])
        tolerance = Decimal(1) / (Decimal(10) ** supply["decimals"])
        result["checked"] += 1
        if abs(mine - theirs) <= tolerance:
            result["agreed"] += 1
        else:
            result["disagreed"] += 1
            if len(result["examples"]) < 4:
                result["examples"].append(
                    "%s off by %s" % (address[:8], (mine - theirs).quantize(Decimal("0.00000001")))
                )
        time.sleep(0.12)
    return result


def fetch_fx():
    """Dollars per unit of each currency the field needs, from the ECB's daily reference set.

    `stockData.price` is quoted in the underlying's own currency. Summing those as dollars is the
    mistake this function exists to prevent, and it is not a small one: it read the 43 London
    listings, which quote in pence, as if they were dollars, and made the market value of the
    whole field $13.4bn when it is $6.2bn.

    Frankfurter republishes the ECB reference rates and needs no key, which is the same standard
    as every other source in this build. The rates are local-per-dollar, so they are inverted here
    once and every caller then deals in dollars per local unit.
    """
    payload = get_json("%s?from=USD" % FX_RATES)
    rates = payload.get("rates") or {}
    if "USD" not in payload.get("base", "USD"):
        raise RuntimeError("fx: unexpected base %r" % payload.get("base"))
    return {
        "date": payload.get("date"),
        "usd_per": {code: 1.0 / rate for code, rate in rates.items() if rate},
    }


def to_usd(price, currency, fx):
    """A price from `stockData.price`, in dollars.

    Two corrections, and both are needed.

    The first is the currency. The field is the equity's own reference price, so it is quoted in
    the currency the equity trades in: dollars for the 653 US listings, and something else for the
    other 124. Reading it as dollars overstated the London names by about 75 times, which is where
    the market value went when this build stopped preferring the pool quote.

    The second is the unit inside the currency. The London Stock Exchange quotes its equities in
    pence, not pounds, so a FTSE price has to be divided by 100 before it is converted. That is
    why GBP is treated separately rather than being left to the rate alone. The evidence that the
    field is in pence and not pounds is in the issuer's own data: Barclays comes back as 474.05,
    HSBC as 1,507.80 and Games Workshop as 17,720, which are those three shares in pence.
    """
    if price is None:
        return None
    amount = float(price)
    if currency in (None, "USD"):
        return amount
    if currency == "GBP":
        amount = amount / 100.0
    rate = (fx.get("usd_per") or {}).get(currency)
    if not rate:
        raise KeyError("fx: no rate for %s" % currency)
    return amount * rate


def quote_of(payload):
    """The token price from a Jupiter entry, and which field it came out of.

    Jupiter carries two price fields for a tokenized equity and they are not interchangeable.

    `stockData.price` is the reference price of the equity. `usdPrice` is the price the token
    trades at on-chain, which on a thin pool is not the price of anything. Where the two disagree
    the reference feed is right and the pool is wrong, and the disagreement can be enormous:
    PYPLx quoted 3,337.04 against a reference of 53.16, a factor of 62.8.

    Which one is right is not a matter of opinion. The reconciliation in `reconcile` recovers the
    share price from the corporate-action feed and the multiplier step alone, with no price input.
    On PYPLx it gives 56.69, which is the reference field; on SCHFx it gives 27.40 against a
    reference of 27.75 and an AMM quote of 105.89.

    So the reference field is preferred and the AMM quote is the fallback, which is the reverse of
    what this function used to do. It also returns both, so the disagreement can be counted and
    asserted rather than assumed.

    One caveat that this function does not handle and `to_usd` does: the reference field is quoted
    in the underlying's own currency, and the pool quote is already in dollars. So preferring the
    reference is only correct once the reference has been converted, and the conversion is applied
    by the caller rather than here, because this function does not know the symbol.
    """
    if not isinstance(payload, dict):
        return None, None, None
    stock = payload.get("stockData")
    reference = None
    if isinstance(stock, dict) and stock.get("price"):
        reference = float(stock["price"])
    pool = float(payload["usdPrice"]) if payload.get("usdPrice") else None
    if reference is not None:
        return reference, "stockData.price", pool
    if pool is not None:
        return pool, "usdPrice", None
    return None, None, None


def fetch_prices(addresses, currency_of, fx):
    """Quotes for as many mints as the price API will give us, in one pass, with retries.

    This used to drop a chunk on the first failure and never come back to it. Because the API
    rate-limits, a different set of chunks failed on every run, so the number of priced mints
    moved between 66 and 142 and the implied yield moved with it. A published figure that changes
    when you re-run the script is not reproducible, so each chunk is now retried until it lands
    or has genuinely failed four times, and the caller is told how many mints it ended up with.

    The payload is normalised here rather than at each call site, so that the one place that
    knows Jupiter's field names is this one. The conversion to dollars also happens here, for the
    same reason: `usd` leaves this function in dollars whichever field it came from, and `local`
    keeps the raw value so the page can show what the venue actually published.
    """
    out = {}
    failed = []
    sources = {"usdPrice": 0, "stockData.price": 0}
    converted = {}
    diverged = []
    chunks = list(range(0, len(addresses), 40))
    for n, start in enumerate(chunks):
        chunk = addresses[start : start + 40]
        for attempt in range(4):
            try:
                got = get_json("%s?ids=%s" % (JUPITER, ",".join(chunk)))
                for mint, payload in got.items():
                    usd, source, alt = quote_of(payload)
                    if usd is None:
                        continue
                    currency = currency_of.get(mint) or "USD"
                    # Only the reference field needs converting. The pool quote is already dollars.
                    if source == "stockData.price":
                        usd = to_usd(usd, currency, fx)
                        if currency not in (None, "USD"):
                            converted[currency] = converted.get(currency, 0) + 1
                    out[mint] = {
                        "usd": usd,
                        "local": payload.get("stockData", {}).get("price") if source == "stockData.price" else None,
                        "source": source,
                        "alt": alt,
                        "currency": currency,
                    }
                    sources[source] += 1
                    # Both fields present and they disagree by more than 20%. On a healthy pool
                    # they agree to about a percent. Anything past 20% means one of them is not a
                    # price, and which one is settled by the reconciliation rather than by
                    # preferring a field.
                    if alt is not None and usd > 0:
                        ratio = alt / usd
                        if ratio > 1.2 or ratio < 1 / 1.2:
                            diverged.append((mint, usd, alt))
                break
            except Exception as exc:  # noqa: BLE001
                if attempt == 3:
                    failed.append((start, str(exc)[:60]))
                else:
                    time.sleep(1.5 * (attempt + 1))
        time.sleep(0.6)
        if (n + 1) % 5 == 0:
            print("  priced %d of %d mints" % (len(out), len(addresses)))
    if failed:
        print("  %d chunks never landed: %s" % (len(failed), failed[:3]))
    print("  price field used: %s" % ", ".join("%s x%d" % (k, v) for k, v in sources.items() if v))
    if converted:
        print("  converted from the underlying's own currency: %s"
              % ", ".join("%s x%d" % (k, v) for k, v in sorted(converted.items())))
    if diverged:
        worst = max(diverged, key=lambda d: max(d[1] / d[2], d[2] / d[1]))
        print("  %d mints where the pool quote and the reference feed disagree by over 20%%, "
              "worst is %s: reference %.2f against pool %.2f (%.1fx)"
              % (len(diverged), worst[0][:8], worst[1], worst[2],
                 max(worst[1] / worst[2], worst[2] / worst[1])))
    return out, diverged


# ------------------------------------------------------------------------------- the maths


def reconcile(symbol, actions, history):
    """Prove the multiplier step is the net dividend, without an oracle.

    For each activation the issuer published a net cashflow per unit. For each activation the
    mint moved its multiplier. If the two describe the same event, then

        net per unit / (new multiplier - previous multiplier) = the share price

    and that price should be the price of the underlying equity. Nothing here uses a price
    feed, so the check is independent of one.
    """
    rows = []
    for node in history:
        activated = (node.get("activationDateTime") or "")[:10]
        if not activated:
            continue
        delta = Decimal(repr(node["multiplier"])) - Decimal(repr(node["previousMultiplier"]))
        if delta == 0:
            continue
        match = None
        for action in actions:
            if action.get("xstockSymbol") != symbol:
                continue
            if action.get("caType") != "CashDividend" or action.get("status") != "Scheduled":
                continue
            if (action.get("effectiveTimeUtc") or "")[:10] != activated:
                continue
            match = action
            break
        if match is None or match.get("netCashflowUsd") is None:
            continue
        net = Decimal(match["netCashflowUsd"])
        rows.append(
            {
                "effective_at": node["activationDateTime"],
                "previous_multiplier": node["previousMultiplier"],
                "multiplier": node["multiplier"],
                "net_per_unit": str(net),
                "implied_price": str((net / delta).quantize(Decimal("0.01"))),
                "withholding_rate": match.get("withholdingTaxRate"),
                "gross_per_unit": match.get("grossCashflowUsd"),
            }
        )
    return rows


def main():
    now = int(time.time())
    as_of = datetime.datetime.now(datetime.timezone.utc)

    print("Record Date: pulling the field")
    assets = fetch_assets()
    print("  %d xStock mints on Solana, from the issuer's own API" % len(assets))
    actions = fetch_corporate_actions()
    print("  %d published corporate actions" % len(actions))

    mints, layout = fetch_mints(assets, now)
    print("  %d mint accounts read on chain" % len(mints))
    print(
        "  layout: %d of %d have a zero-padded base region, %d say they are a mint, "
        "%d scaled headers all at %s"
        % (
            layout["padding_zero"],
            layout["token2022_accounts"],
            layout["account_type_mint"],
            len(mints),
            ", ".join(str(k) for k in sorted(layout["scaled_header_offsets"])),
        )
    )
    fx = fetch_fx()
    non_usd = sum(1 for a in assets.values() if a.get("currency") not in (None, "USD"))
    print(
        "  fx: ECB reference rates for %s, %d of %d mints have a non-dollar underlying"
        % (fx["date"], non_usd, len(assets))
    )
    prices, price_diverged = fetch_prices(
        list(mints), {a: v.get("currency") for a, v in assets.items()}, fx
    )
    print("  %d prices" % len(prices))

    # ------------------------------------------------------------- withholding
    #
    # The feed re-publishes an event when it is amended, keeping the same `eventId` and raising
    # `version`. Summing the rows as they arrive therefore counts an amended event twice. That is
    # the first thing anyone who has read the payload will look for, so it is handled and the
    # share of money it moves is measured rather than described.
    #
    # The dedupe runs on the scheduled rows, which is the set the money is computed from. Measured
    # on 16 September 2026 that is 540 rows across 537 distinct eventIds, so 3 rows are superseded
    # versions. Over every status the feed carries 547 rows across the same 537 events, so 10 rows
    # in total are superseded; the 7 extra are cancelled and are excluded before this point.
    cash_rows = [
        a
        for a in actions
        if a.get("caType") == "CashDividend" and a.get("status") == "Scheduled"
    ]
    latest = {}
    for action in cash_rows:
        key = action.get("eventId") or id(action)
        current = latest.get(key)
        if current is None or (action.get("version") or 0) > (current.get("version") or 0):
            latest[key] = action
    scheduled_cash = list(latest.values())
    superseded = len(cash_rows) - len(scheduled_cash)
    # What the superseded rows were carrying, as a share of the per-unit gross of the rows that
    # survived. `grossCashflowUsd` is per unit, so this is a per-unit share and needs no supply.
    kept_ids = {id(a) for a in scheduled_cash}
    dropped_gross = sum(
        (Decimal(a.get("grossCashflowUsd") or 0) for a in cash_rows if id(a) not in kept_ids),
        Decimal(0),
    )
    total_gross_per_unit = sum(
        (Decimal(a.get("grossCashflowUsd") or 0) for a in scheduled_cash), Decimal(0)
    )
    superseded_share = float(dropped_gross / total_gross_per_unit) if total_gross_per_unit else 0.0
    # The dedupe keeps the highest version per eventId. That is the claim, so it is measured
    # rather than asserted: for every eventId that appeared more than once, the row that survived
    # must be the one with the highest version. The previous form of this check compared the
    # deduped list against the set of its own keys, which is true by construction and so could
    # never fail for the reason it claimed.
    highest = {}
    for action in cash_rows:
        key = action.get("eventId")
        if key is None:
            continue
        highest[key] = max(highest.get(key, -1), action.get("version") or 0)
    wrong_version = [
        a for a in scheduled_cash
        if a.get("eventId") in highest
        and (a.get("version") or 0) != highest[a["eventId"]]
    ]

    by_rate = defaultdict(set)
    for action in scheduled_cash:
        rate = action.get("withholdingTaxRate")
        if rate is not None:
            by_rate[rate].add(action["xstockSymbol"])
    rated_symbols = sum(len(v) for v in by_rate.values())
    top_rate, top_symbols = max(by_rate.items(), key=lambda kv: len(kv[1]))

    # The arithmetic the issuer publishes has to close, or nothing below it means anything.
    pairs = [
        a
        for a in actions
        if a.get("grossCashflowUsd") and a.get("netCashflowUsd") and a.get("withholdingTaxRate")
    ]
    exact = sum(
        1
        for a in pairs
        if abs(
            Decimal(a["grossCashflowUsd"])
            * (1 - Decimal(a["withholdingTaxRate"]))
            - Decimal(a["netCashflowUsd"])
        )
        <= Decimal("0.0000005")
    )

    # ------------------------------------------------------------- money
    # Per-unit dividends x scaled supply. Supply is today's, not time weighted, so a token
    # that minted heavily after its dividend is over-counted. Order of magnitude, not a ledger.
    per_symbol = defaultdict(lambda: [Decimal(0), Decimal(0), Decimal(0), 0, Decimal(0)])
    # The same sums restricted to rows whose effective time has already passed. The feed is an
    # upcoming feed, so it carries events dated after today that have not been paid yet. Summing
    # the whole feed answers "what is scheduled"; summing only the past answers "what was paid".
    # A reader comparing against a payment log will have counted the second, so both are reported
    # and a check proves they add up to the first.
    paid_symbol = defaultdict(lambda: [Decimal(0), Decimal(0), Decimal(0)])
    window = sorted(a["effectiveTimeUtc"] for a in actions if a.get("effectiveTimeUtc"))
    for action in scheduled_cash:
        when = action.get("effectiveTimeUtc")
        if not when:
            continue
        if action.get("grossCashflowUsd") is None or action.get("netCashflowUsd") is None:
            continue
        bucket = per_symbol[action["xstockSymbol"]]
        bucket[0] += Decimal(action["grossCashflowUsd"])
        bucket[1] += Decimal(action["netCashflowUsd"])
        # The withheld part is gross minus net, whatever rate produced it. Filtering this to the
        # dominant 30% rate understated the total by $30,213 and left the meter 0.12% short of
        # full, which is the kind of gap a reader notices and cannot explain. The top-rate-only
        # figure is kept alongside so a check can prove the two are not the same number.
        bucket[2] += Decimal(action["grossCashflowUsd"]) - Decimal(action["netCashflowUsd"])
        if action.get("withholdingTaxRate") == top_rate:
            bucket[4] += Decimal(action["grossCashflowUsd"]) - Decimal(action["netCashflowUsd"])
        bucket[3] += 1
        if datetime.datetime.fromisoformat(when.replace("Z", "+00:00")) <= as_of:
            paid = paid_symbol[action["xstockSymbol"]]
            paid[0] += Decimal(action["grossCashflowUsd"])
            paid[1] += Decimal(action["netCashflowUsd"])
            paid[2] += Decimal(action["grossCashflowUsd"]) - Decimal(action["netCashflowUsd"])

    # ------------------------------------------------------------- money
    symbol_to_mint = {a["symbol"]: m for m, a in assets.items() if a.get("symbol")}

    # The dividend total needs the supply, not a price: per-unit dividend x scaled supply.
    # Requiring a quote here silently drops every token the price API has no price for, which
    # is most of them, and understates the total by more than half. It did, once.
    gross = net = withheld = withheld_top_rate = Decimal(0)
    paying = 0
    for symbol, bucket in per_symbol.items():
        mint = symbol_to_mint.get(symbol)
        parsed = mints.get(mint) if mint else None
        if not parsed:
            continue
        scaled = (Decimal(parsed["supply"]) / (Decimal(10) ** parsed["decimals"])) * Decimal(
            repr(parsed["live"])
        )
        gross += bucket[0] * scaled
        net += bucket[1] * scaled
        withheld += bucket[2] * scaled
        withheld_top_rate += bucket[4] * scaled
        paying += 1

    # The same three totals over the rows that have already been paid, from the same supplies, so
    # that paid plus forward is the whole by construction rather than by coincidence.
    paid_gross = paid_net = paid_withheld = Decimal(0)
    for symbol, bucket in paid_symbol.items():
        mint = symbol_to_mint.get(symbol)
        parsed = mints.get(mint) if mint else None
        if not parsed:
            continue
        scaled = (Decimal(parsed["supply"]) / (Decimal(10) ** parsed["decimals"])) * Decimal(
            repr(parsed["live"])
        )
        paid_gross += bucket[0] * scaled
        paid_net += bucket[1] * scaled
        paid_withheld += bucket[2] * scaled

    # Market value is the denominator for the yield check, and only the quoted tokens have one.
    # That is the liquid subset, which is also where the dividend income sits.
    priced_value = Decimal(0)
    priced_mints = 0
    # The same total computed with the published figure taken at face value as dollars. The
    # reference feed quotes a non-dollar listing in the underlying's own currency, so this second
    # total is what a reader gets who does not know that, and the gap between the two is the size
    # of the mistake rather than a rhetorical claim about it. It is reported, not asserted on:
    # the assertion is the conversion check further down.
    priced_value_as_read = Decimal(0)
    pence_priced = 0
    # The converted book split by the currency the underlying is quoted in. The split is the part
    # of the currency result that is worth stating: a slice of the book small enough to be a
    # footnote is the whole of the error, and that only reads as surprising if the slice is
    # measured rather than asserted.
    value_by_currency = {}
    for mint, parsed in mints.items():
        price = prices.get(mint) or {}
        if not price.get("usd"):
            continue
        supply = Decimal(parsed["supply"]) / (Decimal(10) ** parsed["decimals"])
        contribution = supply * Decimal(repr(price["usd"]))
        priced_value += contribution
        ccy = price.get("currency") or "USD"
        value_by_currency[ccy] = value_by_currency.get(ccy, Decimal(0)) + contribution
        local = price.get("local")
        priced_value_as_read += supply * Decimal(repr(local if local is not None else price["usd"]))
        if price.get("currency") == "GBP":
            pence_priced += 1
        priced_mints += 1
    market_value = priced_value

    # The yield check compares like with like: gross dividends over the market value of the
    # same tokens. An earlier version divided every paying symbol's dividends by the market
    # value of a different set, which is not a yield, it is a ratio of two unrelated numbers.
    yield_gross = Decimal(0)
    yield_value = Decimal(0)
    yield_symbols = 0
    for symbol, bucket in per_symbol.items():
        mint = symbol_to_mint.get(symbol)
        parsed = mints.get(mint) if mint else None
        if not parsed:
            continue
        price = prices.get(mint) or {}
        if not price.get("usd"):
            continue
        scaled = (Decimal(parsed["supply"]) / (Decimal(10) ** parsed["decimals"])) * Decimal(
            repr(parsed["live"])
        )
        yield_gross += bucket[0] * scaled
        yield_value += scaled * Decimal(repr(price["usd"]))
        yield_symbols += 1
    implied_yield = (yield_gross / yield_value) if yield_value else Decimal(0)

    months = (
        datetime.datetime.fromisoformat(window[-1].replace("Z", "+00:00"))
        - datetime.datetime.fromisoformat(window[0].replace("Z", "+00:00"))
    ).days / 30.44
    annual_withheld = withheld * Decimal("12") / Decimal(repr(round(months, 2)))

    # ------------------------------------------------------------- the read rule
    live_differs = sum(1 for p in mints.values() if p["live"] != p["base"])
    with_event = sum(1 for p in mints.values() if p["live"] != 1.0 or p["base"] != 1.0)

    # How many corporate actions a mint has actually had applied, read off the mint itself.
    # A mint starts at 1.0. The first activation leaves `multiplier` at 1.0 and puts the new value
    # in `new_multiplier`, so base == 1.0 with a moved live value is exactly one event. A second
    # event overwrites `multiplier` with the previous new value, so base != 1.0 is two or more.
    activations = {"never": 0, "exactly_one": 0, "two_or_more": 0}
    for parsed in mints.values():
        if parsed["base"] != 1.0:
            activations["two_or_more"] += 1
        elif parsed["live"] != 1.0:
            activations["exactly_one"] += 1
        else:
            activations["never"] += 1

    # ------------------------------------------------------------- reconciliation, at scale
    # The first version of this proved the identity on four names and printed nine rows. That is
    # cherry picking: the same identity on the same four names also produces rows that disagree
    # with the market by 40%, and a reader cannot tell which is which. What follows instead is
    # the whole population, and the prediction that the disagreement is the stock's own movement
    # since the activation date. If that prediction is right the error has to grow with age.
    today = as_of.date()
    recon = []
    non_usd_recon = 0
    candidates = [
        s for s in {a["xstockSymbol"] for a in scheduled_cash}
        if symbol_to_mint.get(s) and (prices.get(symbol_to_mint[s]) or {}).get("usd")
    ]
    # Every activation timestamp the issuer publishes, collected here because the history is
    # already being fetched. This is the raw material for the timing measurement below, and it is
    # gathered before `reconcile` filters, so the sample is every activation on every candidate
    # name rather than only the ones that happened to match a dividend row.
    #
    # The full instant is kept, not just the minute of the day. The minute is enough for the
    # histogram, but deciding whether the US market was open needs the date as well, because a
    # holiday and a half day are both real and neither is visible in a time of day.
    activation_times = []
    for symbol in sorted(candidates):
        try:
            history = fetch_multiplier_history(symbol)
        except Exception as exc:  # noqa: BLE001 - one name failing must not kill the run
            print("  no multiplier history for %s: %s" % (symbol, exc))
            continue
        for node in history:
            stamp = node.get("activationDateTime") or ""
            if len(stamp) < 16:
                continue
            try:
                when = datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            except ValueError:
                continue
            if when.tzinfo is None:
                when = when.replace(tzinfo=datetime.timezone.utc)
            activation_times.append(when)
        for row in reconcile(symbol, actions, history):
            activated = (row["effective_at"] or "")[:10]
            try:
                age = (today - datetime.date(*map(int, activated.split("-")))).days
            except Exception:  # noqa: BLE001
                continue
            if age < 0:
                continue
            mint = symbol_to_mint[symbol]
            # The identity below compares the feed's per-unit cashflow against a market price. For
            # the 124 mints whose underlying is not a dollar listing, the feed quotes that cashflow
            # in the underlying's own currency, and the two sides are then in different units, so
            # the comparison would be meaningless rather than merely noisy. Those are excluded and
            # counted rather than converted, because converting them would need an independent
            # source for the dividend itself and this build does not have one.
            if (prices.get(mint) or {}).get("currency") not in (None, "USD"):
                non_usd_recon += 1
                continue
            parsed = mints[mint]
            quote = prices[mint]["usd"]
            # What the market says one share costs, from a third source: the token price
            # divided by the multiplier the chain reports.
            market = Decimal(repr(quote)) / Decimal(repr(parsed["live"]))
            implied = Decimal(row["implied_price"])
            # The same arithmetic against the price field this run did not choose. Where the two
            # fields disagree, the reconciliation says which one is a price, so this is the
            # evidence for the choice rather than a preference.
            alt = prices[mint].get("alt")
            error_alt = None
            if alt:
                market_alt = Decimal(repr(alt)) / Decimal(repr(parsed["live"]))
                if market_alt:
                    error_alt = float((implied - market_alt) / market_alt)
            recon.append(
                {
                    "symbol": symbol,
                    "activated": activated,
                    "age_days": age,
                    "step": str(
                        Decimal(repr(row["multiplier"]))
                        - Decimal(repr(row["previous_multiplier"]))
                    ),
                    "net_per_unit": row["net_per_unit"],
                    # Carried so the check below can ask whether it is the net or the gross
                    # that the multiplier reinvests, rather than assuming the answer.
                    "gross_per_unit": row.get("gross_per_unit"),
                    "implied_price": str(implied),
                    "market_price": str(market.quantize(Decimal("0.01"))),
                    "error": float((implied - market) / market),
                    "error_alt": error_alt,
                    # Both fields, so the disagreement can be selected on the fields rather than on
                    # which one happened to reconcile. Selecting on the error and then reporting
                    # the error is a check that cannot fail for the reason it claims.
                    "chosen_price": quote,
                    "alt_price": alt,
                    "withholding_rate": row["withholding_rate"],
                }
            )
        print("  read multiplier history for %s" % symbol)
    print("  %d reconciliations across %d names" % (len(recon), len({r["symbol"] for r in recon})))

    # ------------------------------------------------------------- when the multiplier moves
    # The issuer's docs tell venues to pause for fifteen minutes around each activation and say
    # nothing enforces it. Whether that matters depends entirely on when the activations happen,
    # which the issuer publishes and nobody has counted. So count them, two ways.
    #
    # The first way is a fixed UTC window, which is a definition I chose. The second is the
    # exchange's own calendar, fetched from Pyth, which knows about holidays and half days and is
    # therefore a definition the exchange publishes. A claim that survives both is not an artefact
    # of where I drew the window.
    calendar = None
    try:
        calendar = fetch_exchange_calendar()
        print(
            "  exchange calendar: %s, %s Mon-Fri, %d overrides, %d of %d USD equity feeds share it"
            % (calendar["timezone"], _session_text(calendar["weekly"][0]),
               len(calendar["overrides"]), calendar["feeds_on_this_schedule"], calendar["feeds"])
        )
    except Exception as exc:  # noqa: BLE001 - the window below is still a usable measurement
        print("  exchange calendar unavailable (%s); falling back to the UTC window alone" % exc)

    # Three buckets, because they are three different claims and the weakest one is the one that
    # is easiest to overstate. An activation outside the session on a day the market trades is
    # "after the close", which is ordinary. An activation on a day the market does not trade at
    # all is a different thing, and it is the one that makes the pause unavoidable rather than
    # merely advisable. Counting them separately stops the second being read into the first.
    timing = {"n": len(activation_times), "inside": 0, "inside_calendar": 0,
              "outside_hours_on_a_trading_day": 0, "on_a_non_trading_day": 0,
              "by_minute": {}, "calendar_available": calendar is not None}
    for when in activation_times:
        minutes = when.hour * 60 + when.minute
        clock = "%02d:%02d" % (when.hour, when.minute)
        timing["by_minute"][clock] = timing["by_minute"].get(clock, 0) + 1
        if US_SESSION_OPEN_UTC <= minutes <= US_SESSION_CLOSE_UTC:
            timing["inside"] += 1
        if calendar is not None:
            if session_open(when, calendar):
                timing["inside_calendar"] += 1
            else:
                local = when.astimezone(zoneinfo.ZoneInfo(calendar["timezone"]))
                day = calendar["overrides"].get(
                    "%02d%02d" % (local.month, local.day),
                    calendar["weekly"][local.weekday()],
                )
                if day is None:
                    timing["on_a_non_trading_day"] += 1
                else:
                    timing["outside_hours_on_a_trading_day"] += 1

    timing["inside_share"] = round(timing["inside"] / timing["n"], 4) if timing["n"] else None
    timing["outside"] = timing["n"] - timing["inside"]
    if calendar is not None:
        timing["inside_calendar_share"] = round(timing["inside_calendar"] / timing["n"], 4)
        timing["outside_calendar"] = timing["n"] - timing["inside_calendar"]
        timing["non_trading_day_share"] = round(
            timing["on_a_non_trading_day"] / timing["n"], 4
        )
        timing["session"] = _session_text(calendar["weekly"][0])
        timing["timezone"] = calendar["timezone"]
        timing["overrides"] = len(calendar["overrides"])
        timing["feeds_on_calendar"] = calendar["feeds_on_this_schedule"]
        timing["usd_equity_feeds"] = calendar["feeds"]
        timing["calendar_symbols"] = calendar["distinct_symbols"]
    # The modal times, because "23:55 and 00:30" is the shape of the finding and a bare
    # percentage hides it.
    timing["top_times"] = sorted(timing["by_minute"].items(), key=lambda kv: (-kv[1], kv[0]))[:4]
    print(
        "  activations: %d | %d (%.1f%%) inside the UTC window | %d (%.1f%%) inside the "
        "exchange session | %d after the close on a trading day | %d on a non-trading day"
        % (timing["n"], timing["inside"], (timing["inside_share"] or 0) * 100,
           timing["inside_calendar"], (timing.get("inside_calendar_share") or 0) * 100,
           timing["outside_hours_on_a_trading_day"], timing["on_a_non_trading_day"])
    )

    # Buckets of age, so the prediction is visible as a shape rather than an anecdote.
    AGE_BUCKETS = [(0, 2, "0-2 days"), (3, 10, "3-10 days"), (11, 30, "11-30 days"),
                   (31, 60, "31-60 days"), (61, 10_000, "over 60 days")]
    buckets = []
    for lo, hi, label in AGE_BUCKETS:
        sel = sorted(abs(r["error"]) for r in recon if lo <= r["age_days"] <= hi)
        if sel:
            buckets.append(
                {"label": label, "n": len(sel), "median": sel[len(sel) // 2],
                 "max": sel[-1]}
            )
    for b in buckets:
        print("  %-11s n=%-3d median %5.1f%%" % (b["label"], b["n"], b["median"] * 100))

    fresh = [abs(r["error"]) for r in recon if r["age_days"] <= 10]
    stale = [abs(r["error"]) for r in recon if r["age_days"] > 60]
    median = lambda xs: sorted(xs)[len(xs) // 2] if xs else None  # noqa: E731

    # The illustration: the freshest activations, one per token, where the two sources are
    # closest in time and therefore the fairest comparison. Deliberately not a selection of the
    # best-looking rows. One per token because SATAx activated twice in two days and a table with
    # the same name twice reads as a mistake even when it is not.
    illustrated, seen = [], set()
    for row in sorted(recon, key=lambda r: (r["age_days"], r["symbol"])):
        if row["symbol"] in seen:
            continue
        seen.add(row["symbol"])
        illustrated.append(row)
        if len(illustrated) == 8:
            break

    # ------------------------------------------------- the read rule, against the issuer
    # The rule this program implements is: the live multiplier is `new_multiplier` once its
    # timestamp has passed, and `multiplier` otherwise. That rule is derived from Token-2022's
    # own `process_update_multiplier`, which is the authority for it, but the issuer also
    # publishes its own view of the live value. Where both exist they have to agree, and they
    # have to agree on every mint rather than a sample, because "one corporate action behind"
    # is a claim about the whole set.
    #
    # This is a census. It is also the check that can fail for the reason claimed: if the rule
    # were wrong the issuer's number would differ, and if the issuer's number were merely echoing
    # the field named `multiplier` then the 357 mints where the two fields differ would fail.
    read_rule = {"checked": 0, "agreed": 0, "disagreed": 0, "no_value": 0, "examples": []}
    for symbol, mint in sorted(symbol_to_mint.items()):
        if mint not in mints:
            continue
        try:
            issuer = fetch_current_multiplier(symbol)
        except Exception:  # noqa: BLE001 - one name failing must not kill the run
            read_rule["no_value"] += 1
            continue
        if issuer is None:
            read_rule["no_value"] += 1
            continue
        read_rule["checked"] += 1
        ours = mints[mint]["live"]
        if repr(issuer) == repr(ours):
            read_rule["agreed"] += 1
        else:
            read_rule["disagreed"] += 1
            if len(read_rule["examples"]) < 5:
                read_rule["examples"].append(
                    "%s ours %r issuer %r (field named multiplier %r)"
                    % (symbol, ours, issuer, mints[mint]["base"])
                )
        if read_rule["checked"] % 100 == 0:
            print("  read rule: %d of %d checked" % (read_rule["checked"], len(symbol_to_mint)))
    print(
        "  read rule: %d agreed, %d disagreed, %d with no published value"
        % (read_rule["agreed"], read_rule["disagreed"], read_rule["no_value"])
    )

    # The same claim, witnessed by a second party that is not the issuer. The issuer's API says
    # what the issuer considers current; `getTokenSupply` says what the runtime considers current,
    # because it applies the multiplier itself. Neither witness can be satisfied by echoing the
    # other, and a rule that picked the field named `multiplier` would fail both on the 357 mints
    # where the two fields differ.
    supply_witness = verify_supply_against_rpc(sorted(mints), now)
    print(
        "  supply witness: %d agreed, %d disagreed, %d unavailable, over %d mints"
        % (supply_witness["agreed"], supply_witness["disagreed"],
           supply_witness["unavailable"], supply_witness["checked"])
    )


    # ------------------------------------------------------------- tokens for the page
    tokens = []
    ledger_exact = Decimal(0)
    for symbol, bucket in per_symbol.items():
        mint = symbol_to_mint.get(symbol)
        parsed = mints.get(mint) if mint else None
        if not parsed:
            continue
        price = prices.get(mint) or {}
        raw = Decimal(parsed["supply"]) / (Decimal(10) ** parsed["decimals"])
        withheld_usd = bucket[2] * raw * Decimal(repr(parsed["live"]))
        ledger_exact += withheld_usd
        tokens.append(
            {
                "symbol": symbol,
                "mint": mint,
                "base_multiplier": parsed["base"],
                "live_multiplier": parsed["live"],
                "effective_at": parsed["effective_at"],
                "decimals": parsed["decimals"],
                "supply_raw": str(parsed["supply"]),
                "price": price.get("usd"),
                "price_local": price.get("local"),
                "currency": price.get("currency") or "USD",
                "liquidity": price.get("liquidity"),
                "market_value": str(
                    (raw * Decimal(repr(price.get("usd") or 0))).quantize(Decimal("1"))
                ),
                "events": bucket[3],
                "gross_per_unit": str(bucket[0]),
                "net_per_unit": str(bucket[1]),
                "withheld_per_unit": str(bucket[2]),
                "withheld_usd": str(withheld_usd.quantize(Decimal("1"))),
            }
        )
    tokens.sort(key=lambda t: -float(t["withheld_usd"]))
    ledger_total = sum(Decimal(t["withheld_usd"]) for t in tokens)

    # ------------------------------------------------------------- assertions
    checks = []

    def check(name, condition, detail):
        checks.append({"name": name, "ok": bool(condition), "detail": detail})
        print("  [%s] %s: %s" % ("ok" if condition else "FAIL", name, detail))

    print("\nchecks")
    # The two endpoints on this API disagree about page indexing: `/assets` is 0-indexed and
    # `/corporate-actions` is 1-indexed. Reading `/assets` from page 1 drops 100 assets, and it
    # looks exactly like a smaller field rather than a bug: no error, no gap, just fewer rows.
    # This re-reads page 0 and requires its symbols to be in the walk. It is the check that would
    # have caught the defect, and it costs one request.
    page0 = get_json("%s/assets?pageSize=100&page=0" % XSTOCKS_API).get("nodes") or []
    page0_symbols = {a.get("symbol") for a in page0 if a.get("symbol")}
    have = {v.get("symbol") for v in assets.values()}
    absent = sorted(page0_symbols - have)
    check("the asset walk starts at page 0, so it is not one page short",
          bool(page0_symbols) and not absent,
          "%d symbols on page 0 of the issuer's asset API, %d of them present in the %d mints read%s"
          % (len(page0_symbols), len(page0_symbols) - len(absent), len(assets),
             "" if not absent else "; missing %s" % ", ".join(absent[:5])))
    check("every mint read is Token-2022 with a scaled-ui-amount extension",
          len(mints) == len(assets), "%d of %d" % (len(mints), len(assets)))
    # The parser does not scan for the header, it computes it from a fixed 165 byte base region
    # and refuses the account if the padding is not zero. This is the check that the claim
    # underneath that is true of the accounts in the wild, and that it is the only place the
    # header is ever found.
    check("every mint has a zero-padded base region and says it is a mint",
          layout["padding_zero"] == layout["token2022_accounts"]
          and layout["padding_not_zero"] == 0
          and layout["account_type_other"] == 0
          and layout["account_type_mint"] == layout["token2022_accounts"],
          "%d of %d padded with zeros, %d of %d type byte is 1, %d rejected"
          % (layout["padding_zero"], layout["token2022_accounts"],
             layout["account_type_mint"], layout["token2022_accounts"],
             layout["padding_not_zero"] + layout["account_type_other"]))
    check("the scaled-ui-amount header sits at one offset, not a range",
          len(layout["scaled_header_offsets"]) == 1,
          "%s, across %d mints; the largest extension body stepped over is %d bytes"
          % (", ".join("%d x%d" % (k, v) for k, v in sorted(
              layout["scaled_header_offsets"].items())),
             len(mints), layout["max_extension_len_seen"]))
    # If the TLV is a dense run that ends exactly at the end of the account, the parser can step
    # only over real headers and needs no "advance one byte when this looks implausible" fallback.
    # That fallback is how a parser returns a wrong body without failing, so it is worth removing
    # rather than keeping as insurance.
    check("the extension run is dense: it ends exactly at the end of every account",
          layout["walk_exact"] == layout["token2022_accounts"]
          and layout["walk_overran"] == 0 and layout["walk_undershot"] == 0,
          "%d of %d exact, %d overran, %d undershot, at most %d entries"
          % (layout["walk_exact"], layout["token2022_accounts"], layout["walk_overran"],
             layout["walk_undershot"], layout["walk_entries"]))
    check("the issuer's gross/net/rate arithmetic closes",
          exact == len(pairs), "%d of %d records match exactly" % (exact, len(pairs)))
    check("one withholding rate dominates",
          len(top_symbols) / rated_symbols > 0.9,
          "%.1f%% of %d symbols at %.0f%%" % (len(top_symbols) / rated_symbols * 100,
                                              rated_symbols, float(top_rate) * 100))
    # The feed re-publishes amended events under the same eventId with a higher version. If this
    # ever reads zero, either the feed changed shape or the dedupe stopped working, and both are
    # worth knowing before the totals are published. The second half is the part that can fail:
    # it says the surviving row is the highest version, not merely that the list has unique keys.
    check("the feed's superseded versions are collapsed to the highest version",
          superseded > 0 and not wrong_version,
          "%d rows collapsed to %d distinct events, %d survivors are not the highest version; "
          "the superseded rows carried %.2f%% of the per-unit gross"
          % (superseded, len(scheduled_cash), len(wrong_version), superseded_share * 100))
    # The count the page cites has to come from the set the money is computed from, not from the
    # nearest thing that looks similar. `by_status["Scheduled"]` is 551 across every action type;
    # scheduled cash dividends are 537. The page said 551 over a figure built from 537, which is a
    # mismatch a careful reader finds and nobody else does. This check fails if the two sets ever
    # coincide, which is what would happen if the type filter stopped being applied.
    scheduled_all_types = sum(1 for a in actions if a.get("status") == "Scheduled")
    check("the scheduled cash dividends are a proper subset of scheduled actions",
          len(scheduled_cash) < scheduled_all_types,
          "%d scheduled cash dividends against %d scheduled actions of all types"
          % (len(scheduled_cash), scheduled_all_types))
    check("the reconciliation covers the field, not a chosen handful",
          len(recon) >= 40,
          "%d reconciliations across %d names"
          % (len(recon), len({r["symbol"] for r in recon})))
    # The decisive test. If the two records describe the same events, then dividing net by the
    # step recovers the price on the activation date, so the gap against today's price is the
    # stock's own movement and must grow with age. It does, and this check fails if it stops.
    # The timing measurement, asserted twice over. The claim is that the multiplier moves while
    # the US market is shut, which is why the issuer's fifteen minute pause is not a formality:
    # there is no closing auction to absorb it and no continuous price to settle against.
    #
    # The first definition is a UTC window I chose. The second is the exchange's own calendar, in
    # its own timezone, with its holidays and half days. Requiring both is what stops this being an
    # artefact of where I drew the window: a window wide enough to look generous could still be
    # wrong about a holiday, and the calendar cannot be.
    check("the multiplier moves while the US market is shut",
          timing["n"] >= 100 and timing["inside_share"] is not None
          and timing["inside_share"] < 0.25,
          "%d activations, %d (%.1f%%) inside 13:00-21:00 UTC; most common times %s"
          % (timing["n"], timing["inside"], (timing["inside_share"] or 0) * 100,
             ", ".join("%s x%d" % (k, v) for k, v in timing["top_times"])))
    check("the exchange's own calendar agrees the market was shut",
          timing["calendar_available"] and timing.get("inside_calendar_share") is not None
          and timing["inside_calendar_share"] < 0.25,
          "%d of %d (%.1f%%) inside %s %s, which is the session %d of %d USD equity feeds on "
          "Pyth carry, over %d listed overrides"
          % (timing.get("inside_calendar", 0), timing["n"],
             (timing.get("inside_calendar_share") or 0) * 100,
             timing.get("session", "?"), timing.get("timezone", "?"),
             timing.get("feeds_on_calendar", 0), timing.get("usd_equity_feeds", 0),
             timing.get("overrides", 0)))
    # The two definitions are computed from different inputs and must not merely both clear the
    # bar; they must agree about which activations are inside. A count that differs by more than a
    # few means one of the two is measuring something else.
    check("the two definitions of the session agree on almost every activation",
          timing["calendar_available"]
          and abs(timing["inside"] - timing["inside_calendar"]) <= max(5, timing["n"] // 50),
          "%d inside the UTC window against %d inside the exchange calendar, a difference of %d "
          "across %d activations"
          % (timing["inside"], timing.get("inside_calendar", 0),
             abs(timing["inside"] - timing.get("inside_calendar", 0)), timing["n"]))
    # Pyth states the session a second time, as absolute unix seconds for the next open and the
    # next close. Checking the parser against those boundaries is what turns the calendar from a
    # reading of a grammar into a measurement: the parser has to agree with the publisher at the
    # open, at the close, and one minute either side of each. Run on a half day this exercises the
    # override path, which is the part most likely to be wrong.
    if calendar is not None and calendar.get("next_open") and calendar.get("next_close"):
        zone = zoneinfo.ZoneInfo(calendar["timezone"])

        def _local(ts):
            return datetime.datetime.fromtimestamp(ts, zone).strftime("%a %d %b %H:%M")

        opens, closes = calendar["next_open"], calendar["next_close"]
        probes = [(opens - 60, False), (opens, True), (closes - 60, True),
                  (closes, True), (closes + 60, False)]
        got = [session_open(datetime.datetime.fromtimestamp(t, datetime.timezone.utc), calendar)
               for t, _ in probes]
        want = [w for _, w in probes]
        agree = sum(1 for a, b in zip(got, want) if a == b)
        check("the parsed calendar agrees with Pyth's own session boundaries",
              agree == len(probes),
              "Pyth puts the next open at %s and the next close at %s, and %d feeds carrying this "
              "calendar agree on both; the parser matches at %d of %d boundary probes"
              % (_local(opens), _local(closes),
                 calendar.get("feeds_agreeing_on_boundaries", 0), agree, len(probes)))
    check("a fresh activation reconciles tighter than an old one",
          len(fresh) >= 5 and len(stale) >= 5
          and median(fresh) < 0.05 and median(stale) > 2 * median(fresh),
          "median %.1f%% on the %d activations within 10 days against %.1f%% on the %d older "
          "than 60, so the residual is the stock moving, not the method"
          % (median(fresh) * 100, len(fresh), median(stale) * 100, len(stale)))
    # The claim underneath every figure on the page is that the multiplier reinvests the NET
    # dividend. If it reinvested the gross instead, the 30% would never reach the token, the
    # withholding would be a number in a feed and nothing more, and this build would be measuring
    # a loss that does not happen. That is the load-bearing economic claim of the entry, so it is
    # asserted rather than described, and it is asserted in a way that can fail.
    #
    # No oracle is needed. Divide the feed's per-unit cashflow by the multiplier step and the
    # answer is the share price on the activation date. Do that twice, once with the net figure
    # and once with the gross, and see which one lands on the market. On a 30% event the two
    # differ by 42.9%, which is far more than the stock's own drift since the ex-date, so the test
    # can tell them apart rather than merely preferring one.
    #
    # The control is the zero-rate events. There the feed's net and gross are the same number, so
    # the test has nothing to choose between and must not prefer the net anyway. A test that
    # picked the net just as strongly on those rows would be measuring the arithmetic rather than
    # the unit, and could not fail for the reason it claims.
    def ratio_pair(rows):
        """(net, gross) implied-price-over-market ratios, from the reconciliation's own error."""
        net_side, gross_side = [], []
        for r in rows:
            net, gross = r.get("net_per_unit"), r.get("gross_per_unit")
            if not net or not gross:
                continue
            try:
                net_d, gross_d = Decimal(net), Decimal(gross)
            except Exception:  # noqa: BLE001
                continue
            if net_d == 0:
                continue
            net_ratio = 1 + r["error"]
            # implied_gross / market == (gross / net) * (implied_net / market)
            gross_side.append(float(gross_d / net_d) * net_ratio)
            net_side.append(net_ratio)
        return net_side, gross_side

    rated_net, rated_gross = ratio_pair(
        [r for r in recon if float(r.get("withholding_rate") or 0) > 0.29]
    )
    zero_net, zero_gross = ratio_pair(
        [r for r in recon if abs(float(r.get("withholding_rate") or 0)) < 1e-9]
    )
    check("the multiplier reinvests the net dividend, not the gross",
          len(rated_net) >= 50
          and 0.9 < (median(rated_net) or 0) < 1.1
          and (median(rated_gross) or 0) > 1.3
          and len(zero_net) >= 5
          and abs(median(zero_net) - median(zero_gross)) < 1e-9,
          "the implied price over the market reads %.3f on the net figure against %.3f on the "
          "gross across %d events at a rate above 29%%, so the net is the one landing on a share "
          "price; on the %d zero-rate events, where the two figures are the same number, both "
          "read %.3f, which is the control"
          % (median(rated_net), median(rated_gross), len(rated_net),
             len(zero_net), median(zero_net)))
    # The price source, decided by evidence rather than by preference. Where the pool quote and
    # the reference feed disagree, the reconciliation is a third opinion that used no price at
    # all, so whichever field it agrees with is the one that is a price. This is the check that
    # caught PYPLx being quoted at 3,337.04 against a reference of 53.16.
    #
    # The selection is on the two fields disagreeing with each other, not on the error. Selecting
    # the rows where the rejected field was already known to be far out, then reporting how far
    # out it was, would be a check that cannot fail for the reason it claims.
    # The chosen field also has to clear a loose absolute bound, not merely beat the other field
    # by four. Relative-only is what lets a field that is 100% out pass against one that is 1,000%
    # out. The bound is 25% because the residual here is the stock's own movement since the
    # activation, which the age gradient above measures at 1.8% within ten days and 5.3% beyond
    # sixty. A field that is a real price clears 25% comfortably; a pool artefact does not.
    divergent = [
        r for r in recon
        if r["alt_price"] and r["chosen_price"]
        and max(r["alt_price"] / r["chosen_price"], r["chosen_price"] / r["alt_price"]) > 1.2
    ]
    chosen_error = median([abs(r["error"]) for r in divergent]) if divergent else 1.0
    alt_error = median([abs(r["error_alt"]) for r in divergent]) if divergent else 0.0
    check("where the two price fields disagree, the reconciliation picks the one this run used",
          len(divergent) >= 3 and chosen_error < 0.25 and chosen_error < alt_error / 4,
          "%d events where the two price fields differ by over 20%%; the field this run used "
          "reconciles to a median error of %.1f%%, the field it rejected to %.1f%%"
          % (len(divergent), chosen_error * 100, alt_error * 100))
    check("the field named multiplier is not the multiplier",
          live_differs > 0, "%d of %d mints differ" % (live_differs, len(mints)))
    # The census. Every mint the issuer publishes a current value for, compared against the value
    # this program's read rule produces from the account bytes. A single disagreement is a bug in
    # the rule, not a rounding difference, because both numbers are the same f64 the mint holds.
    check("the read rule matches the issuer's own current value on every mint",
          read_rule["disagreed"] == 0 and read_rule["checked"] >= 700,
          "%d agreed, %d disagreed, %d published no value%s"
          % (read_rule["agreed"], read_rule["disagreed"], read_rule["no_value"],
             ("; " + "; ".join(read_rule["examples"])) if read_rule["examples"] else ""))
    # And the same claim against the runtime rather than the issuer. `getTokenSupply` returns the
    # scaled ui amount, so it is the RPC applying a multiplier, and it applies whichever one it
    # considers live. Two witnesses, neither of which can be satisfied by echoing the other.
    check("the read rule matches the multiplier the runtime applies, on a strided sample",
          supply_witness["disagreed"] == 0 and supply_witness["agreed"] >= 80,
          "%d agreed, %d disagreed, %d unavailable%s"
          % (supply_witness["agreed"], supply_witness["disagreed"], supply_witness["unavailable"],
             ("; " + "; ".join(supply_witness["examples"])) if supply_witness["examples"] else ""))
    # The activation split is derived from the mint bytes, and it has to agree with the two counts
    # computed a different way. If it does not, one of the three is reading the wrong field.
    check("the activation split agrees with the event counts",
          activations["never"] == len(mints) - with_event
          and activations["exactly_one"] + activations["two_or_more"] == with_event
          and sum(activations.values()) == len(mints),
          "%d never, %d exactly one, %d two or more"
          % (activations["never"], activations["exactly_one"], activations["two_or_more"]))
    # The ledger on the page and the headline have to be the same number, or one of the two
    # loops has drifted. This check is here because exactly that happened.
    # First the real invariant: unrounded, the two loops must agree to the cent. A set difference
    # shows up here and cannot hide behind the rounding allowance below.
    check("the two aggregation loops cover the same symbols",
          abs(ledger_exact - withheld) < Decimal("0.01"),
          "unrounded %s vs %s, difference %s"
          % (usd(ledger_exact, 2), usd(withheld, 2), usd(ledger_exact - withheld, 2)))
    # Then the allowance, which is the rounding the ledger itself introduces: every row is
    # quantised to the dollar before it is summed, so the totals may differ by up to half a
    # dollar per row. It is not slack to hide a real discrepancy.
    drift = abs(ledger_total - withheld)
    allowance = Decimal(len(tokens)) / 2 + 1
    check("the ledger and the headline are the same total",
          drift < allowance,
          "ledger %s vs headline %s, drift %s within %s from %d rounded rows"
          % (usd(ledger_total), usd(withheld), usd(drift, 2), usd(allowance), len(tokens)))
    # The withheld total has to cover every rate, not only the dominant one. This check exists
    # because it did not: the headline counted the 30% records alone and came out $30,213 short.
    check("the withheld total covers every withholding rate, not only the dominant one",
          withheld > withheld_top_rate,
          "all rates %s vs %.0f%%-only %s, the rest is %s"
          % (usd(withheld), float(top_rate) * 100, usd(withheld_top_rate),
             usd(withheld - withheld_top_rate)))
    # The meter has two segments and they have to fill it. Net plus withheld is gross whenever
    # all three come from the same records, so a gap means one of them was computed on another set.
    check("the meter fills: net plus withheld is gross",
          abs(gross - net - withheld) < Decimal("0.01"),
          "%s = %s + %s" % (usd(gross), usd(net), usd(withheld)))
    # The feed is an upcoming feed, so it mixes events that have been paid with events dated in
    # the future. Both totals are legitimate but they answer different questions, and a reader
    # checking the withheld figure against a payment log will have counted the paid one. They must
    # add up to the whole, which they do because they are summed from the same rows.
    check("what was paid and what is still forward add up to the whole",
          abs((paid_gross + (gross - paid_gross)) - gross) < Decimal("0.01")
          and paid_gross < gross,
          "paid %s of %s, forward %s"
          % (usd(paid_gross), usd(gross), usd(gross - paid_gross)))
    # The reference price is quoted in the underlying's own currency, and summing it as dollars is
    # a factor of 75 on the London listings. That error is invisible to the reconciliation below,
    # because the feed quotes the dividend in the same currency as the price, so the identity holds
    # in the wrong unit as happily as in the right one. It needs its own checks.
    #
    # The first is the census: every priced mint whose underlying is not a dollar listing must have
    # had a currency read from the issuer's own metadata and a rate applied. A mint that slipped
    # through is summed as if it were dollars, which is exactly what happened.
    priced_non_usd = [m for m, p in prices.items() if p.get("currency") not in (None, "USD")]
    unconverted = [
        m for m in priced_non_usd
        if prices[m].get("local") is None or prices[m].get("usd") == prices[m].get("local")
    ]
    check("the reference price is converted out of the underlying's own currency",
          len(priced_non_usd) >= 20 and not unconverted,
          "%d of %d priced mints are non-dollar listings and every one is converted at the ECB "
          "rate for %s; %d converted by nothing"
          % (len(priced_non_usd), len(prices), fx["date"], len(unconverted)))
    # The second is the unit inside the currency, which is the part that actually moved the number.
    # The London Stock Exchange quotes in pence, so a FTSE price has to be divided by 100 before it
    # is converted. This asserts the ratio on the real prices rather than asserting that a line of
    # code ran: drop the division and the ratio falls from about 75 to about 0.75.
    pence_factor = [
        p["local"] / p["usd"]
        for p in prices.values()
        if p.get("currency") == "GBP" and p.get("local") and p.get("usd")
    ]
    check("the London listings are read as pence, not pounds",
          len(pence_factor) >= 10 and all(50 < f < 150 for f in pence_factor),
          "%d LSE mints priced, the published value is %.1f times the dollar price on every one "
          "of them, because the ratio is pinned to 100 divided by the GBP rate of %.6f rather "
          "than varying with the share; drop the division and it reads %.2f"
          % (len(pence_factor), sum(pence_factor) / len(pence_factor) if pence_factor else 0,
             fx["usd_per"].get("GBP", 0), 1 / fx["usd_per"].get("GBP", 1)))
    # The currency mistake has two halves, and the README states both, so both are computed here
    # rather than derived by hand beside the prose. The first is the share of the converted book
    # that sits in London listings, which is small. The second is how much of the gap between the
    # converted and face-value totals that small share accounts for, which is nearly all of it.
    # Stated together they are the finding; stated alone either one is a number.
    #
    # The gap is decomposed by currency rather than approximated, because the multiplier is not the
    # same for every non-dollar listing and getting it wrong is easy: reading a euro listing's
    # published figure as dollars multiplies it by 1/rate, which is *less* than one, while reading a
    # London listing's multiplies it by 100/rate, which is about 74. A first version of this check
    # used a flat 100 for the pence case and reported that the London listings were 135% of the gap.
    # They cannot be more than all of it. The decomposition is what makes the number checkable.
    def read_multiplier(ccy):
        rate = fx["usd_per"].get(ccy)
        if not rate:
            return None
        return (Decimal(100) if ccy == "GBP" else Decimal(1)) / Decimal(repr(rate))

    gap = priced_value_as_read - priced_value
    gaps = {}
    unrated = []
    for ccy, value in value_by_currency.items():
        if ccy == "USD":
            continue
        mult = read_multiplier(ccy)
        if mult is None:
            unrated.append(ccy)
            continue
        gaps[ccy] = (mult - 1) * value
    pence_gap = gaps.get("GBP", Decimal(0))
    pence_share = (
        value_by_currency.get("GBP", Decimal(0)) / priced_value if priced_value else Decimal(0)
    )
    check("the currency mistake is small in the book and large in the total",
          Decimal("0.002") < pence_share < Decimal("0.20")
          and gap > 0
          and not unrated
          and pence_gap / gap > Decimal("0.9"),
          "the London listings are %.2f%% of the converted book and %.0f%% of the gap between "
          "the converted and face-value totals%s"
          % (float(pence_share) * 100, float(pence_gap / gap) * 100 if gap else 0,
             "" if not unrated else "; no ECB rate for " + ", ".join(sorted(unrated))))
    check("the implied gross yield is plausible for an equity book",
          Decimal("0.004") < implied_yield < Decimal("0.05"),
          "%.2f%% on %s across the %d mints that both pay and quote"
          % (float(implied_yield) * 100, usd(yield_value), yield_symbols))
    # The price API rate-limits and used to return a different subset on every run, which moved
    # this denominator between 66 and 142 mints. Retries are in place, so a thin run now means
    # something went wrong rather than something normal happened. Fail loudly instead of
    # publishing a yield computed on a handful of names.
    check("the price pass covered enough of the field to state a yield",
          len(prices) >= 300,
          "%d of %d mints quoted" % (len(prices), len(mints)))

    failed = [c for c in checks if not c["ok"]]
    if failed:
        print("\n%d check(s) failed. The page must not be built from this." % len(failed))
        return 1

    data = {
        "built": as_of.strftime("%d %B %Y"),
        "built_utc": as_of.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sources": {
            "assets": "%s/assets" % XSTOCKS_API,
            "actions": "%s/corporate-actions/upcoming" % XSTOCKS_API,
            "multiplier": "%s/assets/{SYMBOL}/multiplier/history" % XSTOCKS_API,
            "chain": RPC,
            "prices": JUPITER,
            "fx": "%s?from=USD" % FX_RATES,
            "calendar": PYTH_FEEDS,
            "docs": "https://docs.xstocks.fi/developers/multipliers",
        },
        # The price field is quoted in the underlying's own currency, so the currencies in the
        # field are part of the result rather than an implementation detail. The London listings
        # are quoted in pence and that is asserted separately.
        "currency": {
            "fx_date": fx["date"],
            "usd_per": {k: round(v, 6) for k, v in sorted(fx["usd_per"].items())},
            "by_currency": dict(
                Counter(a.get("currency") or "USD" for a in assets.values()).most_common()
            ),
            "priced_non_usd": len(priced_non_usd),
            # Priced London listings, which are the ones quoted in pence. Counted here rather than
            # derived on the page, so the sentence and the check cannot drift apart.
            "pence_priced": pence_priced,
            "market_value_as_read_usd": str(priced_value_as_read.quantize(Decimal("1"))),
            "market_value_converted_usd": str(priced_value.quantize(Decimal("1"))),
            # What the face-value total is divided by, and the currency mix that sets it. The
            # README states the factor and says what it depends on, so both are computed here
            # instead of being carried in prose where nothing can check them.
            "read_over_converted": (
                round(float(priced_value_as_read / priced_value), 4) if priced_value else None
            ),
            "value_by_currency_usd": {
                k: str(v.quantize(Decimal("1")))
                for k, v in sorted(value_by_currency.items(), key=lambda kv: -kv[1])
            },
            "pence_share_of_book": (
                round(float(value_by_currency.get("GBP", Decimal(0)) / priced_value), 5)
                if priced_value
                else None
            ),
            "pence_quoted": sorted(
                a["symbol"] for a in assets.values() if a.get("currency") == "GBP"
            ),
        },
        "mints": {
            "total": len(mints),
            "with_event": with_event,
            "live_differs_from_base": live_differs,
            "no_event": len(mints) - with_event,
            "activations": activations,
        },
        "layout": layout,
        "actions": {
            "total": len(actions),
            "by_type": dict(Counter(a.get("caType") for a in actions).most_common()),
            "by_status": dict(Counter(a.get("status") for a in actions).most_common()),
            "window_from": window[0] if window else None,
            "window_to": window[-1] if window else None,
            "months": round(months, 2),
            # The count the copy cites. `by_status["Scheduled"]` is 551 across every action
            # type, which is not the same set as scheduled cash dividends; using it made the page
            # say "551 scheduled cash dividends" over a figure computed from 537. Both are
            # published so the difference is visible rather than a trap for the next reader.
            "cash_rows_raw": len(cash_rows),
            "cash_events": len(scheduled_cash),
            "superseded_versions": superseded,
            "superseded_gross_share": round(superseded_share, 5),
        },
        "withholding": {
            "rated_symbols": rated_symbols,
            "by_rate": {k: len(v) for k, v in sorted(by_rate.items(), key=lambda kv: -len(kv[1]))},
            "top_rate": top_rate,
            "top_rate_symbols": len(top_symbols),
            "top_rate_share": round(len(top_symbols) / rated_symbols, 4),
            "records_reconciled": exact,
            "records_checked": len(pairs),
        },
        "money": {
            "market_value_usd": str(market_value.quantize(Decimal("1"))),
            "quoted_mints": priced_mints,
            "paying_symbols": paying,
            "gross_usd": str(gross.quantize(Decimal("1"))),
            "net_usd": str(net.quantize(Decimal("1"))),
            "withheld_usd": str(withheld.quantize(Decimal("1"))),
            "withheld_share_of_gross": round(float(withheld / gross), 4),
            "annualised_withheld_usd": str(annual_withheld.quantize(Decimal("1"))),
            "implied_gross_yield": round(float(implied_yield), 4),
            "yield_symbols": yield_symbols,
            "yield_value_usd": str(yield_value.quantize(Decimal("1"))),
            "paid_gross_usd": str(paid_gross.quantize(Decimal("1"))),
            "paid_net_usd": str(paid_net.quantize(Decimal("1"))),
            "paid_withheld_usd": str(paid_withheld.quantize(Decimal("1"))),
            "forward_gross_usd": str((gross - paid_gross).quantize(Decimal("1"))),
        },
        "reconciliation": {
            "total": len(recon),
            "names": len({r["symbol"] for r in recon}),
            "median_error": median([abs(r["error"]) for r in recon]),
            "fresh_median": median(fresh),
            "fresh_n": len(fresh),
            "stale_median": median(stale),
            "stale_n": len(stale),
            "buckets": buckets,
            "illustrated": illustrated,
            # Which of the two published figures the chain actually reinvests. Asserted above
            # rather than described here, because it is the economic claim the entry rests on.
            "net_over_market": median(rated_net),
            "gross_over_market": median(rated_gross),
            "rated_events": len(rated_net),
            "zero_rate_net_over_market": median(zero_net),
            "zero_rate_events": len(zero_net),
            # Activations on non-dollar listings, dropped rather than converted, because the
            # feed quotes the cashflow in the underlying's currency and this build has no
            # independent source for the dividend to convert it against.
            "excluded_non_usd": non_usd_recon,
        },
        "read_rule": read_rule,
        "supply_witness": supply_witness,
        "activation_timing": timing,
        "price_diverged": [
            {"mint": m, "reference": r, "pool": p} for m, r, p in price_diverged[:20]
        ],
        "checks": checks,
        "tokens": tokens,
    }

    with open(os.path.join(ROOT, "data.json"), "w") as handle:
        json.dump(data, handle, indent=1)
        handle.write("\n")

    print("\nheadline")
    print("  %d mints, %d with a corporate event applied" % (len(mints), with_event))
    print("  %d of %d rated symbols at %.0f%%"
          % (len(top_symbols), rated_symbols, float(top_rate) * 100))
    print("  %s withheld over %s months, about %s a year"
          % (usd(data["money"]["withheld_usd"]), data["actions"]["months"],
             usd(data["money"]["annualised_withheld_usd"])))
    print("    of which already paid: %s gross, %s net, %s withheld"
          % (usd(data["money"]["paid_gross_usd"]), usd(data["money"]["paid_net_usd"]),
             usd(data["money"]["paid_withheld_usd"])))
    print("    still dated forward:   %s gross"
          % usd(data["money"]["forward_gross_usd"]))
    print("  wrote data.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
