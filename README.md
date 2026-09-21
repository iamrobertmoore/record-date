<div align="center">

<img src="docs/brand/readme-banner.svg" alt="Record Date. A tokenized stock pays its dividend by raising a multiplier inside its Token-2022 mint account, and the field named multiplier holds the previous value." width="820">

<br>

**Reads a tokenized stock's dividend out of the Token-2022 mint account, resolves the multiplier that is actually live, and records each activation on chain as a crank writes it, so a venue can tell when the dividend moves and refuses to settle inside the window the issuer only recommends pausing for.**

<a href="https://iamrobertmoore.github.io/record-date/desk.html"><b>Settlement desk</b></a> &nbsp;·&nbsp;
<a href="https://iamrobertmoore.github.io/record-date/"><b>Evidence page</b></a> &nbsp;·&nbsp;
<a href="https://explorer.solana.com/address/ycg2obpKmccwAz1zGf4QqgV2vnkWd6CQKLdJSGDxtmG?cluster=devnet"><b>Program on devnet</b></a> &nbsp;·&nbsp;
<a href="docs/architecture.svg"><b>Architecture</b></a> &nbsp;·&nbsp;
<a href="#run-it-yourself"><b>Reproduce the numbers</b></a>

</div>

---

## The problem, measured

**377 of the 400 tokenized stocks on Solana that publish a withholding rate lose 30% of every dividend before it is reinvested. Across 6.6 months of the issuer's own feed that is $6,373,807 withheld, about $11,588,741 a year, and no surface a holder looks at reports it.**

And the dividend itself is not paid in cash. It is reinvested by quietly raising a `multiplier` stored inside the token's Token-2022 mint account. **On 370 of 927 mints the field named `multiplier` is not the multiplier**: it holds the value from before the most recent corporate action, and the live value sits in a second field beside it. A reader that takes the obvious one is one corporate action behind.

That read rule is not inferred from the field names. It is what Token-2022's own `process_update_multiplier` does, and it was then checked against the issuer's own published current value on **every** mint rather than a sample: **927 agreed, 0 disagreed, 0 published nothing to compare against.** The comparison is one of the build's checks and it fails the page if it ever stops holding.

The multiplier reinvests the **net** dividend, and that is asserted rather than assumed, because it is the claim the whole entry rests on. If the chain reinvested the gross, the 30% would never reach the token and the withholding would be a number in a feed. It does not: divide the feed's per-unit cashflow by the multiplier step and the answer is the share price, and doing that with the net figure lands on the market at **1.033** where the gross figure reads **1.476**. The control is the 40 zero-rate events, where the feed's net and gross are the same number and the test correctly has no preference: both read **1.006**.

---

## What this entry adds

Three things, and none of them is the mechanism.

1. **The count, measured against the exchange's own published calendar rather than a window I chose.** 640 activations, of which **631 fall outside the US regular session as the exchange itself defines it**, and **57 of them take effect on a day the market does not trade at all**. The premise is the organiser's own number rather than mine: the Solana Foundation's 13 September newsletter reports that **63% of tokenized-equity volume settles outside US market hours**. The issuer's own documentation tells venues and protocols to pause for fifteen minutes around each activation and attaches no enforcement to it.

2. **A unit error in the field the prior art recommends.** 174 of the 927 mints track an equity that does not trade in dollars, and the 27 London listings are quoted in **pence**. Read at face value the priced book is **$13.81bn**; converted out of each underlying's own currency at the ECB rate it is **$6.46bn**. The yield denominator is built from those prices, so the error is small in the book and large in the total.

3. **The history the mint discards.** The mint keeps two multipliers and no more, the value from before the last change and the latest beside it with the timestamp it takes effect. Everything before that pair is gone: **370 of the 379 tokens priced here carry a past activation timestamp, median age 26.7 days**. The program records each activation as a crank writes it, ordered, permissionless and never edited.

The mechanism itself is not mine, and the next section says whose it is.

---

## What was already known before this entry

**The read rule is not my discovery and this entry does not claim it.** Solana's own Token-2022 documentation describes the stale field. [ExDate](https://github.com/AlperJ/exdate) published it with worked examples on 13 September 2026, alongside the 30% withholding rate and a market-wide dollar total, and its `PRIORART.md` names [SolanaRWA](https://solanarwa.app) as having turned these same multiplier changes into per-holder income since May 2026, and Lido's stETH reward history as the five-year-old precedent. All of that is real prior art on the *mechanic*, and a judge who finds it after an entry has claimed novelty will stop reading.

What ExDate and SolanaRWA are both built as is a **holder's statement**: what a wallet was paid, read back from the chain. ExDate ships no program of any kind, and says so.

What this entry is built as is a **venue's control**, and the timing ground is not empty. [Kamino](https://gov.kamino.finance/t/kamino-is-integrating-xstocks-powered-by-the-chainlink-data-standard-to-enable-tokenized-equities-lending/792), the first major lending protocol to take tokenized equities as collateral, already branches on the activation timestamp, and it has since November 2025. Its oracle, [Scope](https://github.com/Kamino-Finance/scope), declares `V10_TIME_PERIOD_BEFORE_ACTIVATION_TO_SUSPEND_S` as 24 hours in `programs/scope/src/oracles/chainlink.rs` and, once the clock reaches 24 hours before the `activation_date_time` in the Chainlink report, stops refreshing the price and returns `SuspendExistingPrice` until somebody resumes it by hand. That is a real control and it is worth reading as prior art on the *timing*. What it is not is a record, and the difference is the point: Scope reads an oracle report, which is an attestor's statement about the corporate action, and it reacts by suspending, so a venue asking when this last moved, or how often it moves, still has nowhere to look.

**The same is true of the ledger, and one entry is close enough to it that a judge may find it first.** [Multiplier](https://github.com/GODGRACE07/multiplier) is live at [multiplier-roan.vercel.app](https://multiplier-roan.vercel.app) and does the thing this entry is built around: a corporate-action ledger for xStocks, corrected balances, and a replay endpoint reaching back to August 2025. It is polled from `api.xstocks.fi` every five minutes, so its ledger is the issuer's own attestation stored faithfully. That is the same class of source as Scope, and it is not a record of the chain. Its values are right, and I checked the one activation where both sides are visible: for KOx on 15 September the feed and the mint agree exactly, on the timestamp and on the multiplier. What it cannot do is witness an activation rather than receive one, and its older entries cannot be checked against the mint at all, because the mint has already overwritten them. That is the gap this entry is built in.

**The on-chain half is crowded too, and an earlier version of this file got that wrong in two places.** [EquityGuard](https://github.com/Maheshsiddu29/EquityGuard) is an `assert_safe_execution` instruction deployed on devnet on 13 September that reads `new_multiplier_effective_timestamp` directly out of the mint, with no price feed, and fails the transaction inside a transition window. An earlier version of this file said that "what is not there is a timestamp a program can read without one". **That was false.** The timestamp is in the mint, any program can read it, and EquityGuard does. The same file called a deployed on-chain program near-unique differentiation, and that is false as well: of the 108 repositories this event had produced when I counted on 20 September, **27 ship an on-chain program.** [DividendX](https://github.com/notorious-d-e-v/dividendx-stocklana) runs an Anchor program with a dividend calendar, [KEEL](https://github.com/Marc-Dvci/KEEL) enforces risk mandates on chain against live Pyth accounts, [paritas](https://github.com/UEddy/investire) reads the same multiplier timestamp in Rust, and [stockcurve](https://github.com/ExpertVagabond/stockcurve) reads the same `PriceUpdateV2` account with no key that this entry reads.

What survives is not the mechanism. It is **the history the mint discards, and a count nobody has made**, and both are set out in the section above. The mint keeps only that last pair and discards everything before it, and EquityGuard's guard answers whether it is safe to trade *now* but cannot answer when this last moved or how often it moves.

---

## Who this is for

**Tomas holds about $9,000 of tokenized US equities in a Solana wallet.** He is not a trader. He bought them because they pay dividends and they settle in seconds.

In March, May and August the issuer reinvested his XOMx dividend. Each time it withheld 30% first. Tomas saw none of it. His token balance never moved, because Token-2022 leaves the raw amount alone and expects the reader to apply the multiplier. Nothing on chain told him a corporate action had happened, and nothing told him a third of his dividend had been withheld before the reinvestment.

**I have not put a named wallet in front of a mint whose two fields differ, so I am not going to claim one shows him the wrong number.** What I can show is that the field is easy to take. `stockcurve`, an entry in this same event, reads the `ScaledUiAmountConfig` extension and takes `readDoubleLE(32)`, which is the field named `multiplier`; its own comment lists `new_multiplier_effective_timestamp` beside it and nothing in the file consults it (`scripts/status.mjs:32`). KEEL and DividendX apply the timestamp. So the mistake is in reach and made by a program built by someone who was looking straight at the extension. How many wallets make it is not something this build measures, and the honest form of that claim is "on 370 of 927 mints the two fields differ", which is what the rest of this file counts.

The second reader is the protocol on the other side. A lending market that takes xStocks as collateral prices them by multiplying the raw amount by whatever the mint says. Get the field wrong and the position is mispriced at exactly the moment a dividend lands.

The third is a venue, and it is the one this entry is written for. The issuer's own docs ask trading venues to pause for fifteen minutes either side of an activation, and say nothing enforces it. EquityGuard enforces it now, one trade at a time, and Kamino's oracle blacks out for a day around it. What no surface gives a venue is the record behind the rule: a permissionless account of when this last moved, how often it moves, and how many of the moves land on days the market is shut, read out of the mint itself rather than out of an oracle report, with no attestor and nobody's permission.

---

## Try this, watch what happens

| Try this | Watch what happens |
|---|---|
| [Open the settlement desk](https://iamrobertmoore.github.io/record-date/desk.html) | Pick a registered mint and the deployed program answers, live, over plain RPC. The desk has no key and no backend: it assembles the transaction in the browser, asks the program what a holding is worth, and applies the fifteen minute rule to the program's own reading of how long ago the activation was. On almost every devnet mint the field named `multiplier` disagrees with the live value, and the desk shows both answers side by side and counts them as it reads |
| [Open the evidence page](https://iamrobertmoore.github.io/record-date/) | Every figure above, with the eight freshest reconciliations and the median gap against the market bucketed by age |
| [Fetch NVDAx's multiplier history](https://api.xstocks.fi/api/v2/public/assets/NVDAx/multiplier/history?page=0&pageSize=5&network=Solana) | The issuer's own record of each activation, with the previous and new multiplier side by side and no API key |
| [Open the program on devnet](https://explorer.solana.com/address/ycg2obpKmccwAz1zGf4QqgV2vnkWd6CQKLdJSGDxtmG?cluster=devnet) | The deployed program, its IDL, and the receipt accounts it writes |

---

## The one thing worth checking

The corporate-action feed and the mint account are two independent records of the same events. If they describe the same event, then

```
net dividend per unit  ÷  (new multiplier − previous multiplier)  =  the share price
```

on the activation date. The left-hand side comes from the chain and the issuer's feed alone, with no oracle. That claim makes a prediction. The implied price is fixed on the activation date and the market price is today's, so the gap between them has to be the stock's own movement, which means the gap must **grow** with the age of the activation. If the identity were an artefact of how the feed is written, the gap would be noise and the same size in every bucket.

It grows across the first four buckets. Across 443 reconciliations on 351 names:

| Age of the activation | Events | Median gap against today's price |
|---|---:|---:|
| 0 to 2 days | 41 | 1.7% |
| 3 to 10 days | 34 | 2.5% |
| 11 to 30 days | 160 | 4.9% |
| 31 to 60 days | 118 | 6.9% |
| over 60 days | 90 | 4.8% |

**The oldest bucket is the one where the gradient does not continue, and it is reported rather than smoothed.** From fresh to sixty days the gap goes 1.7% to 6.9%, which is what the identity predicts. Past sixty days it comes back to 4.8%. I do not have an explanation I can test for that, so the build asserts only what it can: a fresh activation reconciles tighter than an old one, at **2.1% within ten days against 4.8% beyond sixty**. A monotonic claim would be a stronger sentence and it would not be true of the data in front of me.

---

## When the multiplier moves

The issuer's own docs tell venues and protocols to pause for fifteen minutes around each activation, and attach no enforcement to it. Whether that matters depends entirely on when the activations land, which the issuer publishes and which, as far as I can find, had not been counted by the minute each one lands on. So I counted them. **640** activations, drawn from the 370 symbols that both pay a dividend and carry a price, by the minute of the day they take effect:

| Time (UTC) | Activations | New York clock |
|---|---:|---|
| 00:30 | 407 | 20:30 ET the previous day |
| 23:55 | 177 | 19:55 ET, after the close |
| 00:15 | 11 | 20:15 ET the previous day |
| 01:15 | 5 | 21:15 ET the previous day |

**631 of the 640, or 98.6%, fall outside the US regular session as the exchange itself defines it, and 93.8% of the sample lands on those four minutes.**

That is measured twice, because one definition of "the market is open" would be mine and the other is the exchange's. The first is a fixed UTC window drawn wide at 13:00 to 21:00, which covers the session under both daylight time and standard time and can only make the finding harder to reach: by that definition 628 of the 640, or 98.1%, are outside. The second is the calendar Pyth publishes in its feed directory, with no key: `America/New_York`, a 09:30 to 16:00 session, and twelve listed holiday and half-day overrides. Against that, 9 of the 640 fall inside the session, so 631 are outside. The two are computed independently and the build requires both to agree the market is shut. They disagree about three activations and agree about the other 637.

The sharper number is the one neither window is needed for. **57 activations, 8.9% of the sample, take effect on a day the market does not trade at all.** On those days the pause is not merely advisable. The underlying is shut for the whole session and the token is not, which is the premise this finding rests on rather than an assumption: the Solana Foundation's 13 September newsletter reports that **63% of tokenized-equity volume settles outside US market hours**, and the event brief names that as a theme.

This is the part of the story that is not a bug in anyone's code. Activation is scheduled for 00:30 UTC the day after the ex-date, four and a half hours after the US close and thirteen hours before the next open. The fifteen minute pause is what stands in for a market, and it is a recommendation in a document rather than a constraint in a program. **Nothing can enforce it from inside a mint account, which is why this entry ships a program instead of a warning.**

---

## Two price fields, and one of them is not a price

A tokenized stock on Jupiter carries two prices. `usdPrice` is the pool quote. `stockData.price` is a reference feed. They are not interchangeable, and on 17 September 2026 they differed by more than 20% on **15** mints.

The widest is a factor of **30**, on `Xsn3H7AC…`. Its pool quote is **$395.01** and its reference is **$13.355**. That mint has no activation in the feed at all, which matters below.

The reconciliation settles the rest, because it used no price at all. Across the **443** events it covers, `net ÷ step` lands on the market at **1.033** while the gross reads **1.476**. Two of the disagreements it is adjudicating: a reference of **$27.99** against a pool quote of **$106.34** on SCHFx, and a reference of **$146.80** against a pool quote of **$43.31** on MRKx.

Across the **443** reconciliations this build makes, the field it uses sits at a **median error of 4.5%**. The separation is far wider on the **ten** events where the two published fields disagree with each other, which is the subset that tests the choice rather than the method: there the build requires the field it uses to beat the field it rejected by four times, and to clear 25% on its own. It asserts both, and it asserts an absolute bound as well as a relative one, because beating the other field by four times is not evidence that a field is a price. The relative-only form is what lets a field that is 100% out pass against one that is 1,000% out.

**Where there is no dividend event the reconciliation cannot adjudicate, and that is stated rather than hidden.** `Xsn3H7AC…` has no activation in the feed, so its pool quote is rejected on the strength of the reference field alone, with no independent confirmation. That is why the build uses one field throughout and records the disagreement rather than switching field by field: a rule that picked whichever field looked better on the day would be unfalsifiable.

This is also the one place the entry is standing on somebody else's lesson. [ExDate](https://github.com/AlperJ/exdate) published its own verification ledger on 13 September 2026 and found that seven of its 65 payers were priced more than 30% off their underlying, one of them by 61.9x, carrying a quarter of its headline; it noted that Jupiter returns the reference price in the same payload and that the data to self-check "was already in hand and unused". This build uses that reference field as the price, and the checks above are the self-check it asked for.

---

## The reference price is not always in dollars

The field ExDate recommends is the right one, and taking it at face value is the next mistake along.

**174 of the 927 mints track an equity that does not trade in dollars.** The issuer's asset API publishes the underlying's currency and the listing exchange beside every deployment, and the reference feed quotes the price in that currency rather than in dollars. Of the 784 mints this build can price, 31 are non-dollar: 27 London listings, 2 in euros, 2 in Hong Kong dollars.

The London listings are the ones that bite, because they are quoted in **pence**. A FTSE share is published at roughly **75 times** the dollar price of the same share, because the exchange quotes in pence and the feed carries that figure through as though it were pounds. On this build every one of the 27 is published at **74.8 times** its dollar price, a ratio pinned to 100 divided by the GBP rate rather than varying with the share, which is why it is one number and not a range.

Read at face value, the market value of the 784 priced mints is **$13.81bn**. Converted out of each underlying's own currency at the ECB reference rate for 2026-09-17, it is **$6.46bn**. The London listings are **1.54% of the book by value and 100% of that difference**: reading a pence figure as dollars multiplies that slice by 75, so a slice that rounds to nothing is the whole of the error. The exact figures for a given build are in `data.json` and printed on the evidence page, because they move with the prices and with the currency mix. What does not move is the currency the reference price is quoted in.

Three checks carry this, because a wrong unit is not a wrong number and it does not look like one:

- **the reference price is converted out of the underlying's own currency**: 31 of 784 priced mints are non-dollar listings, every one is converted at the ECB rate for 2026-09-17, and **0 are converted by nothing**
- **the London listings are read as pence, not pounds**: 27 LSE mints priced, each published at **74.8 times** its dollar price, a ratio pinned to 100 divided by the GBP rate rather than varying with the share; drop the division and it reads 0.75
- **the currency mistake is small in the book and large in the total**: the London listings are 1.54% of the converted book and 100% of the gap between the converted and face-value totals

The reason this matters beyond a market value nobody trades on is that **the yield denominator is built from these prices.** A book that is 2.14 times too large reports a yield 2.14 times too small, and a plausible-looking yield is exactly the number that does not get questioned. This is also the one finding in the entry I have not seen anywhere else: ExDate's ledger recommends the reference field, prices seven assets off the pool quote, and does not mention currency, exchange or pence at all.

---

## What is on chain

The program is deployed to devnet at `ycg2obpKmccwAz1zGf4QqgV2vnkWd6CQKLdJSGDxtmG`.

| Instruction | What it does |
|---|---|
| `init_registry` | Once per deployment. Holds the authority and the counters. |
| `register_mint` | Reads an xStock mint and stores the live multiplier **alongside** the value in the field the mint calls `multiplier`, so the gap between them is auditable. |
| `record_activation` | Permissionless. If the multiplier has moved, writes a `Receipt`: previous, new, the issuer's own timestamp, the raw supply. Ordered by sequence and never edited. |
| `read_entitlement` | Raw units in the units a wallet should display. Return data is a little-endian `u128`. Integer arithmetic throughout. |
| `activation_pending` | One byte: has the multiplier moved since this program last looked? A cranker calls this first so it does not pay for a transaction that reverts. |
| `settlement_window` | Two little-endian `i64`s: seconds since the last activation, and seconds until one the issuer has staged. `-1` on either means there is nothing to report, because `0` is a real answer and means 1970. A protocol following the issuer's own advice to pause refuses to settle while either is inside 900. |
| `bind_pyth_feed` | Binds a registered mint to a Pyth feed id. The id is stored rather than the price account's address, because a `PriceUpdateV2` account is rewritten on every publish and its address is not stable. Re-binding overwrites, so a corrected id is fixable. |
| `verify_against_pyth` | Reads a Pyth `PriceUpdateV2` account and refuses it if the receiver program does not own it, if it is for a feed other than the bound one, if it is older than a limit the caller sets, or if it differs from the caller's price by more than a tolerance the caller sets. Return data is the Pyth price and the deviation it was judged on. |

The first six instructions are the read done properly. **`settlement_window` is the one that exists because of the timing finding, and it has already been overtaken once.** An earlier version of this file said the timestamp was not readable on chain and that holding it in an account was the difference between advice and a primitive. That was wrong. The mint's `ScaledUiAmountConfig` carries `new_multiplier_effective_timestamp`, and EquityGuard branches on it directly with no price feed and no account of ours. What the mint does **not** carry is history. It holds the most recent change and overwrites it, so a program arriving today can see the last activation and nothing before it. `record_activation` writes each one down as it happens, permissionless and ordered. `settlement_window` returns two numbers rather than one, and the second is the half the mint cannot express at all: the issuer's window runs fifteen minutes either side of an activation, and while a value is staged the mint's single timestamp describes the value that has **not** arrived, so the mint reports nothing about when the value in force took effect. The record holds that timestamp, and the window reads it there, which is the one place in this program where the record is used for something other than bookkeeping. A venue asking whether it is safe to trade right now can usually ask the mint alone. A venue asking how often this stock has moved this quarter, or whether the moves cluster on days the market is shut, has to ask a record, and this is the record.

**A receipt's `effective_at` is the issuer's own timestamp for the value it recorded, and it can be 0.** The issuer stages the next multiplier about four hours before it takes effect, and staging overwrites the timestamp of the value now in force, so for those four hours the mint holds a real multiplier whose date is gone. A crank landing in that window records the change it can see and dates it `0`, which means *recorded after the issuer staged the next value, so the mint no longer held the date*. The value is not in doubt, and `staged_at` still reports when the next one arrives, which is why `settlement_window` returns both numbers and uses `-1` rather than `0` for "nothing to report". Measured on SATAx: the issuer staged its next value at 20:20:17Z for `2026-09-17T00:30:00Z`, so the mint carried a timestamp that had not arrived for 4h 09m 43s. **That also says when a cranker should call.** The two modal activation times are 20:30 ET and 19:55 ET, or 00:30Z and 23:55Z, and the staging window is the four hours ahead of each, so the larger bucket's window runs from about 00:35Z to 20:00Z and the smaller one closes 35 minutes earlier.

**The last two exist because of a gap in everything above them.** Every other instruction reads the mint, and the mint is the issuer's own account, so a number taken from there is the issuer agreeing with itself. `verify_against_pyth` puts a third party's price for the same equity beside it. The account it reads is a Pyth `PriceUpdateV2` posted to Solana and owned by Pyth's receiver program, so the comparison is against state this program does not control, and the three ways it could be meaningless are each refused: the wrong owner, the wrong feed, and an age the caller will not accept. Two such accounts are committed as test fixtures, captured from mainnet and devnet by `scripts/capture_pyth_fixture.py`, and the devnet one is used to test the staleness refusal because it was genuinely 33.6 days old when it was captured. The layout in `src/pyth.rs` is derived from those bytes rather than from documentation, and a test asserts the discriminator against `sha256("account:PriceUpdateV2")[:8]` so the offsets below it can be trusted.

**The age limit is the caller's number, not the program's.** How fresh a price has to be is a question about the caller's settlement, and the answers differ: a venue settling inside the session might want a minute, while one settling a weekend corporate action has to accept Friday's close, because no equity prints on a Saturday and so the price cannot move. The program has no way to know which case it is looking at, so it enforces a one-day ceiling and lets the caller choose below it. The measured interval is what makes five minutes a reasonable default rather than a guess: sampling the live AAPL account ten times over three minutes on 17 September 2026, it advanced its `publish_time` nine times and was never more than 24 seconds old, at 06:50 ET with the session still shut. **Weekend behaviour is not measured directly, and the reason it matters is sharp.** 407 of the 640 activations land at 20:30 ET and 177 at 19:55 ET, both after the close and neither inside a session. The devnet AAPL account's last write was Friday 14 August at 16:00:19 ET, the exact minute of the close, and another entry in this event states that with the real Pyth receiver nothing is published for US equities between Friday 20:00 ET and Sunday 20:00 ET. If that holds, a five-minute rule refuses every Friday-night corporate action, thirty minutes after the gap opens. That is why the value is a recommendation and not a constant in the program.

The mint is parsed as raw bytes rather than through the `spl-token-2022` crate, because the program does not link against the program that owns the mint.

**The layout is not the one the Token-2022 docs describe.** A mint account is a 165-byte base region, then the account-type byte, then the `(type: u16, length: u16, body)` entries. The mint itself fills only the first 82 bytes of that region. The remaining 83 are padding. Walking the extensions from byte 83, as reading the documented layout as "82-byte base, then extensions" would have you do, therefore lands in padding and finds nothing.

The 165-byte base region is deliberate and it is in the Token-2022 source, with the reason written above the constant:

> Any account with extensions must be at least `Account::LEN`. Both mints and accounts can have extensions. A mint with extensions that takes it past 165 could be indiscernible from an Account with an extension, even if we add the account type… With this approach, we only start writing the TLV data after `Account::LEN`, which means we always know that the account type is going to be right after that.
>
> `interface/src/extension/mod.rs`, above `BASE_ACCOUNT_AND_TYPE_LENGTH`

The `spl-token` client agrees and slices the same offset itself: `getMint` requires `data[165] == AccountType.Mint` and then takes `data.slice(165 + 1)`. So the header is computed, not searched for: the base region is a fixed 165 bytes, the first extension header is at 166, and a non-zero byte in the padding is an error rather than something to step past. **Asserted on all 927 live mints: the padding is zero in 927 of 927, the account-type byte is 1 in 927 of 927, and the `ScaledUiAmountConfig` header is at byte 275 in all 927.**

The walk from one header to the next is exact, and that is a decision rather than an omission. **On all 927 mints the extension run is dense and ends exactly at the end of the account**, so there is nothing to skip over. A tolerant walk that advanced a byte whenever a header looked implausible would be a way to return a *different* plausible-looking body without failing, which is worse than failing. The density is measured on every run and the page is not built if it stops holding.

**The walk covers the whole TLV area and returns the offset it found, rather than returning the moment it finds it.** It used to return early, so every entry after the scaled-ui-amount entry went unread. On every mint read from mainnet that is four further entries, `Pausable`, `ConfidentialTransferMint`, `TransferHook` and `TokenMetadata`, and each of them is now bounds-checked on every call. A program a venue settles against should not report a number out of an account it has already established is not laid out the way it claims to be. The change is measured rather than asserted, and the measurement is committed rather than described. `tests/mints/` holds all 927 mint accounts as raw bytes, captured from mainnet by `scripts/capture_mint_census.py`, and `tests/walk_census.rs` walks every one of them on a plain `cargo test` with no network and no environment variable. **927 accounts, 927 walked, 0 refused, body offset 279 on every one.** It used to return early when `RECORD_DATE_MINT_DIR` was unset, and no directory was committed, so it passed having walked nothing; the directory is now the default and a missing or short one is a failing test, because a check that cannot fail is worse than no check. Point `RECORD_DATE_MINT_DIR` at a fresh capture to diff the two runs: identical output is the claim that the extra checks change no answer on any live mint.

Reading Token-2022's own walk to mirror its rules turned up a second defect in the opposite direction. **The extension-type ceiling was stale by one**: it read 27 while `PermissionedBurn` had already taken 28, so a legitimate mint carrying that extension would have been refused over a type this program never reads. The enum is written out beside the constant now, so the number can be checked rather than trusted, and a test pins both sides of it, which makes the next growth of that enum a failing test rather than a live mint failing in the field.

**Every entry the walk steps over is bounds-checked, including the entry it is looking for, and that ordering was wrong until 17 September.** The loop condition is only `off + 4 <= data.len()`, so a header sitting in the last four bytes of an account passed it, matched the type, matched the length, and returned a body offset that `read_mint` then sliced past the end of the buffer. Slicing out of bounds panics rather than returning an error. The check sat below the branch that returns, so the one entry the walk was asked to find was the one entry it never checked. A unit test now builds exactly that account and asserts the refusal; with the old ordering it does not fail politely, it panics with `range end index 226 out of range for slice of length 178`, which is how the defect was confirmed rather than argued about.

Multipliers are stored as `f64` because that is how the mint stores them, and converted once to a `u128` fixed-point value at 1e18. There is no float arithmetic after that, and **two multipliers are never compared as floats**: `TokenRecord` keeps the raw bits of both the trap value and the live one (`base_multiplier_bits`, `live_multiplier_bits`) so the wrong answer stays auditable, and every decision compares the fixed-point form, because `0.0 == -0.0` is true and `NaN != NaN` is true, which makes an `f64` equality test an unsound way to ask whether a multiplier moved. Every comparison in the program is between integers or bytes. The three float comparisons in `to_fixed_point` are the validity, overflow and truncation bounds on the conversion itself, and even the Pyth deviation is compared in basis points as a `u128` rather than as a price difference. The conversion fails closed on anything the token program could not have written: `try_validate_multiplier` in Token-2022's scaled-ui-amount processor requires `is_sign_positive() && is_normal()` before it will set either field, so a zero, a subnormal, a negative, an infinity and a NaN are each refused rather than rounded. It also refuses a multiplier below 1e-18, which would otherwise convert to a fixed point of zero and be read downstream as a position worth nothing.

---

## Run it yourself

```bash
# every number on the page, from public endpoints, with no key
python3 scripts/fetch.py          # writes data.json, asserts 32 checks
python3 scripts/build_page.py     # writes index.html, after checking the hand-written figures

# the checks themselves
python3 scripts/test_checks.py    # 99 negative controls, no network needed
```

`fetch.py` takes about twelve minutes, most of it the 927-mint read-rule pass against mainnet. **The second command will often refuse, and that is the point rather than a fault.** It checks the figures written by hand in this README against the build the fetch just made, so if a price moved while the fetch was running it names the figure it wanted and writes no page. Running `fetch.py` again clears it. A build that quietly published a README disagreeing with its own data would be the exact failure this entry is about, so the gate is deliberately on the reproduce path rather than beside it.

If any check fails the script prints why and **refuses to build the page**. It is not possible to publish a stale number through this pipeline by accident.

**The video script is checked too, and it is the one surface that has to be.** Every other artefact here is rebuilt from `data.json`, so a figure that moves regenerates. The script is typed and then read aloud, so a number that moves between it being written and the camera being turned on would be spoken on camera while the build contradicted it, and the recording is the one thing in this entry that cannot be regenerated from the tree. The figures the script speaks, the figures it puts on screen, the figures its prep section tells the reader to confirm on the page, and the figures in the title and description it is uploaded under are all derived from `data.json` and compared with the script before the page is written, and a moved figure refuses the build the same way a stale README figure does. The fourth of those was added on 20 September, after a probe that moved one occurrence of a figure at a time showed the upload notes were read by nothing while the README's copy of the same figure was gated. What the check deliberately does not cover is named in the module rather than left as a silence, because "not checked" and "checked and fine" print the same otherwise.

**This file is one of the things that is checked.** The page is generated from `data.json` and cannot disagree with it, but the README, the banner at the top of it and the architecture diagram below are written by hand, and a hand-copied figure that moves is the exact defect this entry is about. All three are derived from `data.json` and compared with it before the page is written. That check has a control of its own, and it earned its place immediately: it found two figures on this screen that the previous commit had left contradicting the build they shipped with. The checks include the ledger agreeing with the headline to the cent, the withheld total covering every rate rather than only the dominant one, the mint layout holding on every account read, the read rule matching the issuer on every mint, the multiplier reinvesting the net dividend rather than the gross, the reference price being converted out of the underlying's own currency, and the age gradient above.

**The net-versus-gross check is the one that carries the entry, and it has a control.** Divide the feed's per-unit cashflow by the multiplier step and the answer is a share price. Done with the net figure it lands on the market at 1.033; done with the gross it reads 1.476. The 40 zero-rate events are the control: there the feed's net and gross are the same number, so the test must have no preference, and it does not, reading 1.006 either way. Without that control the check would be measuring the arithmetic rather than the unit.

**Two of those checks could not fail for the reason they claimed, and both were rewritten.** One selected its rows on the error it then reported. The other compared a deduplicated list against the set of its own keys, which is true whatever survived. `scripts/test_checks.py` holds the old form of each next to the new one, on inputs built to separate them: it shows the old price selection collapsing to nothing when the choice is swapped, and the old dedupe check passing on a list where the wrong row survived. A check that cannot fail is worse than no check, because it looks like rigour.

```bash
# the program
anchor build --arch v1                                       # --arch v1 is required, see below
cargo test --manifest-path programs/record_date/Cargo.toml   # 56 tests: 18 unit, 37 integration, 1 census

# a warning is a build failure here, so the list cannot grow unread
cargo clippy --manifest-path programs/record_date/Cargo.toml --all-targets -- -D warnings

scripts/deploy.sh devnet                                     # refuses to deploy a mismatched id
```

**The integration tests read a real mint, not a fixture written to match the parser.** `fixtures/nvdax_mint.bin` is the actual 679-byte account of the NVDAx mint (`Xsc9qvGR1efVDFGLrVsmkzv3qi45LTBjeUKSPmx9qEh`) read from Solana mainnet, and `the_fixture_is_the_real_account_it_claims_to_be` asserts the layout on that account rather than on a synthetic one. **And the Rust parser is no longer proven on one account.** `tests/mints/` holds all 927 mint accounts as raw bytes, captured from mainnet by `scripts/capture_mint_census.py`, and `tests/walk_census.rs` walks every one of them on a plain `cargo test`: 927 accounts, 927 walked, 0 refused, body offset 279 on every one. The 927-mint assertions in `scripts/fetch.py` remain the independent second implementation, so the layout is still cross-checked between two of them rather than one agreeing with itself.

```bash
# the whole loop on devnet, against the deployed program, with no mocks
npm install
npm run demo        # creates a mint, schedules a dividend, waits for it, cranks a receipt
npm run demo:status # read what is already on chain, change nothing
```

**`--arch v1` is not optional.** `anchor build` defaults to `--arch v3`, and LiteSVM 0.10.0 cannot load a v3 ELF: it reports `InvalidAccountData`, which reads like a broken program rather than a build flag. There is a second trap underneath it. `cargo-build-sbf` caches per arch in `target/sbpf<arch>-solana-solana`, but `anchor build` only copies to `target/deploy/` when it actually compiles, so switching arch with nothing to rebuild leaves the **previous** arch's binary in place to be deployed. `scripts/deploy.sh` therefore removes `target/deploy/record_date.so` first and then verifies the `e_flags` of what the build produced, rather than trusting the flag. The integration tests check the same thing, so a v3 build fails with an instruction instead of a mystery.

The binary deployed to devnet is the binary the tests ran against. The comparison is not `==`, and that is the point: `solana program dump` returns the whole programdata account, which is padded past the end of the ELF, so exact equality reports a difference that is not there. The claim is that the deployed prefix equals the built file and everything after it is zero.

```
built            326696 bytes, sha256 fa4a700e3c304880
deployed         332952 bytes, 6256 of trailing padding
deployed ELF     identical to the tested binary
```

`scripts/deploy.sh` makes that comparison, refuses the deploy if it fails, and writes the three lines above to `docs/deploy.txt` as it goes. The block in this file is checked against that file by `scripts/build_page.py`, so it cannot be left behind by a later deploy. The check has been run against a deliberately mutated binary to confirm it fails for the reason it claims.

---

## What this does not claim

- **The 30% may not be a permanent loss.** It is withheld at source and a holder may be able to credit it at home, depending on where they live. What is not in doubt is the rate applied on 377 of 400 symbols, the exactness of its application, and that the holder is never shown it.
- **Why 30% rather than a treaty rate is not asserted.** The feed shows the rates and the arithmetic, not the reason. 20 symbols are at 0% and 3 at 5%, which is evidence a lower rate is achievable and nothing more than that.
- **The dividend is not lost, it is reinvested.** The holder's position grows by the net amount, and the multiplier step is what proves the net is the figure that reaches the token. The claim is that it grows by less than the company paid, and that no surface reports the difference.
- **No arbitrage is claimed, and no depth figure is quoted.** The pools behind these tokens are thin, and the reason this file puts no number on how thin is that depth is a live reading: it moves every block, so a depth written into a snapshot is stale before a reader reaches it, and every figure in this file is one the committed build reproduces. The two-field section above makes the point without needing depth. On **15** mints the pool quote and the reference feed disagree by more than 20%, and the widest is a factor of **30**. Where the pool quote is not a price at all, a spread against the underlying is measuring the pool's thinness rather than an opportunity.
- **The age gradient is not monotonic and is not claimed to be.** It holds from fresh to sixty days and comes back down in the oldest bucket. The build asserts the weaker, testable version.
- **The window is the feed's, not a clean year.** 6 March 2026 to 24 September 2026, scaled to a year rather than twelve measured months. Supply is today's rather than time weighted, so a token that minted heavily after its dividend is over-counted. The feed is an upcoming feed, so the window runs seven days past the build date: **$23,795,729 of the $24,365,251 gross is dated on or before today and $569,522 is still forward.** Both are reported and a check proves they add up. Read the annual figure as an order of magnitude.
- **The read rule, the withholding rate and the dollar total are not this entry's findings.** They are ExDate's, published on 13 September 2026, and Solana's own Token-2022 documentation describes the read rule. **Nor is the timing area unoccupied, and the on-chain half of it least of all.** Kamino's Scope oracle suspends a price 24 hours before the activation timestamp its Chainlink report carries, and has since November 2025; EquityGuard reads the mint's own activation timestamp and refuses execution inside a transition window; and 27 of the 108 repositories this event had produced when I counted on 20 September ship an on-chain program. An earlier version of this file claimed the timestamp as its own, called an on-chain program near-unique differentiation, and described Kamino's control as a price band, which it is not. **All three were wrong.** What this entry adds is the count and the history: 640 activations timed against the exchange's own calendar, and an ordered record of every activation rather than only the latest.
- **The field is not empty and this entry is not alone in it.** 131 projects had been submitted by 20 September and none are published, so this is what competitors claim rather than what I could verify they do. The four closest are `EquityGuard` (an on-chain guard reading the mint's activation timestamp, devnet, 13 September), `dividendx-stocklana` (an Anchor program splitting a position into principal and dividend-right tokens, with a real devnet Raydium round trip), `KEEL` (on-chain risk mandates priced against live Pyth accounts), and `stockcurve` (which reads the same keyless `PriceUpdateV2` account this entry does). Also on the ground: `multiplier` is a corporate-actions oracle, `openbell-solana` gates execution on off-hours premiums and raw-versus-scaled amount mistakes, `basis-terminal` productises the price gap, and `corporate-action-guard` (3 September 2026, before this event opened) issues fail-closed preflight receipts against stale corporate-action state on a different chain. I read their READMEs and, for the four closest, their program source; not their code in full.
- **The Pyth check is a comparison, not a correction, and nothing enforces it.** It refuses a price that is stale, for the wrong feed, or too far from Pyth's own. It does not make Pyth right, nothing forces a venue to call it, and a venue that ignores it is unaffected. That is the same shape as `settlement_window` and the same reason both exist: they are values another program can branch on rather than gates anything must pass. **The weekend and holiday behaviour of the equity feed is not measured directly here, and the evidence that it matters is sharp.** No equity prints when the market is shut, so the price cannot move, and whether the pusher still writes a fresh `publish_time` then is untested. Two things point the same way: the devnet AAPL account's last write was Friday 14 August at 16:00:19 ET, the exact minute of the close, and another entry in this event states that with the real Pyth receiver nothing is published for US equities between Friday 20:00 ET and Sunday 20:00 ET. Since 407 of the 640 activations land at 20:30 ET and 177 at 19:55 ET, both after the close, a five-minute rule would refuse every Friday-night corporate action. The cadence measured above is a weekday pre-market one, and that is why the age limit is an argument with a ceiling rather than a constant in the program.
- **This is one issuer.** 927 mints from Backed's own asset API, which is exhaustive over what Backed publishes and is not a census of every equity token on Solana.
- **The deployed program has only ever read mints this entry created.** Every `TokenRecord` and every `Receipt` on devnet belongs to a `DEMOx` mint the demo made, so the record on chain holds the entry's own demo runs rather than a real corporate action. The 927 real mints above were read on mainnet by `scripts/fetch.py` and by the committed census in `tests/mints/`, not by the deployed program. The read rule is therefore proven on real accounts and the history is not yet fed by them, and the gap matters because the whole claim is about what the record holds over time. It closes by deploying to mainnet and registering real mints, which is a decision with a cost and has not been taken.
- **The record holds what a crank wrote, and nothing cranks it today.** `record_activation` is permissionless, ordered and never edited, but it writes only when it is called, and it writes the multiplier the mint holds at the moment of the call. Two activations with no crank between them therefore leave one receipt rather than two: the later call finds the multiplier already stored, records nothing, and the step in between is lost, because the mint overwrote it. Every receipt on devnet is the demo's own call. The program is the primitive; the caller is the half that is not shipped, and paying someone to crank it on mainnet is a decision with a cost that has not been taken.

---

## The Pyth track

This entry is built for **Best use of Pyth market data**, and it is worth naming rather than leaving a judge to infer it. The track's own description is unusually close to what this file already does: it offers three Apple feeds and says builders can *"work with both the underlying market and the on-chain asset representing exposure to it"*, with *"use one feed, compare both"*. That is the comparison this entry is built on, and both halves are wired.

**The feed is bound on chain.** `bind_pyth_feed` stores the id `Equity.US.AAPL/USD`, the first of the three the track names, against a registered mint, and `verify_against_pyth` reads a `PriceUpdateV2` account owned by Pyth's receiver program and refuses it if the owner is wrong, the feed is not the bound one, the price is older than the caller will accept, or it differs from the caller's own price by more than the caller's tolerance. Pyth is consumed inside a Solana program rather than beside one.

**The other side of the comparison is the mint.** The price Pyth is checked against is the mint's own: the multiplier out of `ScaledUiAmountConfig`, resolved against the activation timestamp and applied to the raw balance. That is what makes the check worth making rather than a formality, because on 370 of the 927 mints the field named `multiplier` and the live value disagree.

**Pyth's calendar is what makes the timing finding authoritative.** The session definition in the section above is not mine: it is the `schedule` field in the same keyless feed directory, `America/New_York`, a 09:30 to 16:00 session and twelve holiday and half-day overrides, and 631 of the 640 activations fall outside it.

**What this does not claim about the track.** Nothing here is claimed as prize money, and the track's stated prize is three months of Pyth Pro access. The calendar half is read over HTTP from a public directory and not on chain, and saying otherwise would be the wrong claim to make to the sponsor whose reviewer can check it in one request. The entry is also not built to the track's other suggested shapes: it is a settlement primitive that consumes a Pyth price, not a price surface, a basket or a charting app of its own.

---

## Sources

All public, none authenticated.

| | |
|---|---|
| mint list | `https://api.xstocks.fi/api/v2/public/assets` |
| corporate actions | `https://api.xstocks.fi/api/v2/public/corporate-actions/upcoming` |
| multiplier, current | `https://api.xstocks.fi/api/v2/public/assets/{SYMBOL}/multiplier` |
| multiplier history | `https://api.xstocks.fi/api/v2/public/assets/{SYMBOL}/multiplier/history` |
| mint accounts | `https://api.mainnet-beta.solana.com` |
| prices | `https://api.jup.ag/price/v3` |
| exchange rates, for the non-dollar listings | `https://api.frankfurter.app/latest` (ECB reference rates) |
| the exchange calendar, for the timing claim | `https://hermes.pyth.network/v2/price_feeds` |
| a Pyth price account, for the on-chain check | `https://api.mainnet-beta.solana.com`, reading the `PriceUpdateV2` account itself |
| the underlying's currency and venue | `https://api.xstocks.fi/api/v2/public/assets` |
| the pause recommendation, and the polling pattern this program implements | [docs.xstocks.fi/developers/multipliers](https://docs.xstocks.fi/developers/multipliers) |
| 63% of tokenized-equity volume outside market hours | [Solana Foundation newsletter, 13 September 2026](https://solanacompass.com/news/solana-tokenized-stocks-beat-nyse-and-nasdaq-combined-in-volume-with-63-of-trades-after-market-hours) |
| the venue that already branches on the activation timestamp | [Kamino governance forum, 14 July 2025](https://gov.kamino.finance/t/kamino-is-integrating-xstocks-powered-by-the-chainlink-data-standard-to-enable-tokenized-equities-lending/792), and [Scope's `chainlink.rs`](https://github.com/Kamino-Finance/scope/blob/master/programs/scope/src/oracles/chainlink.rs), where the 24 hour suspension constant lives |
| dividend mechanics | [docs.xstocks.fi/docs/dividends-and-stock-splits](https://docs.xstocks.fi/docs/dividends-and-stock-splits) |
| the multiplier contract | [docs.xstocks.fi/developers/multipliers](https://docs.xstocks.fi/developers/multipliers) |
| the read rule, in code | [`process_update_multiplier`](https://github.com/solana-program/token-2022/blob/main/program/src/extension/scaled_ui_amount/processor.rs) |
| the account layout, in code | [`interface/src/extension/mod.rs`](https://github.com/solana-program/token-2022/blob/main/interface/src/extension/mod.rs) |

Pyth appears twice and neither use needs a key. Its **feed directory** is public, and the session definition in section 02 is taken from it. Its **price values** are a different surface, and the distinction is worth stating because it is easy to get wrong: the Hermes HTTP endpoint serves equity price updates only to a key carrying a grant, while Pyth also posts every update to Solana as a `PriceUpdateV2` account owned by its receiver program, and a posted account is public state. `scripts/capture_pyth_fixture.py` reads one with a plain RPC call and no credential at all, and the two fixtures under `programs/record_date/tests/fixtures/` are the bytes it returned. The `verify_against_pyth` instruction reads such an account at settlement time. No HTTP price API is called anywhere in this repository. **The keyless read itself is not this entry's finding**, and it should not be read as one: `stockcurve` states the same thing in its own README and `Erodoro` commits `PriceUpdateV2` layout snapshots as fixtures. What is less common is consuming it inside the program rather than off chain, which is what `verify_against_pyth` does.

**An earlier version of this file said Pyth's price values "need a key and are not used here". Both halves were wrong.** The values reach a Solana program without a key because the account is on chain, and they are used now. The correction is recorded rather than quietly applied, because the claim sat in the sources section of the published page where a judge would read it.

**Prior art this entry is built on top of:** [ExDate](https://github.com/AlperJ/exdate) (the read rule, the withholding rate, the dollar total, and the split-versus-dividend distinction), [SolanaRWA](https://solanarwa.app) (per-holder dividend records on this chain since May 2026), [Kamino](https://gov.kamino.finance/t/kamino-is-integrating-xstocks-powered-by-the-chainlink-data-standard-to-enable-tokenized-equities-lending/792) (its Scope oracle suspends a price for the 24 hours before the activation timestamp its Chainlink report carries, shipped in release 0.30.0 in November 2025), and [Lido's stETH reward history](https://stake.lido.fi/rewards) (the same mechanic on Ethereum since 2021).

**And the same event, on the same ground, on chain.** A sweep of the 108 repositories this hackathon had produced when I counted on 20 September found **27 shipping an on-chain program**. The four closest to this ground are named here rather than left for a judge to find: [EquityGuard](https://github.com/Maheshsiddu29/EquityGuard) (an `assert_safe_execution` guard reading the mint's activation timestamp, deployed on devnet 13 September, which is the entry this one was closest to duplicating), [DividendX](https://github.com/notorious-d-e-v/dividendx-stocklana) (an Anchor program splitting a position into principal and dividend-right tokens, with a real devnet Raydium round trip), [KEEL](https://github.com/Marc-Dvci/KEEL) (on-chain risk mandates priced against live Pyth accounts, with session awareness), and [stockcurve](https://github.com/ExpertVagabond/stockcurve) (which reads the same keyless `PriceUpdateV2` account this entry does). Two of those, EquityGuard and stockcurve, reached conclusions an earlier version of this file claimed as its own. The count of 27 is a lower bound, and the rule is stated rather than implied: a repository counts if it carries an `Anchor.toml` or a top-level `programs/` directory, which is what catches the three whose program sits under `onchain/`, `program/` or `smart-contract/`, and the three whose program has no `Anchor.toml` at all. Both markers agree on exactly the same repositories, and that set is also the repositories containing a Rust source file, so the figure survives being measured three ways. Three of the 108 are empty repositories with no commits, and they are counted in the denominator rather than quietly dropped. It is a lower bound because it counts repositories matching keyword searches, not the submission list, which stays hidden until entries close.

Built for **Stocklana**, by Robert Moore. MIT licensed.

