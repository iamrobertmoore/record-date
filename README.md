<div align="center">

<img src="docs/brand/readme-banner.svg" alt="Record Date. A tokenized stock pays its dividend by raising a multiplier inside its Token-2022 mint account, and the field named multiplier holds the previous value." width="820">

<br>

**Reads a tokenized stock's dividend out of the Token-2022 mint account, resolves the multiplier that is actually live, and records every activation on chain, so a venue can tell when the dividend moves and refuses to settle inside the window the issuer only recommends pausing for.**

<a href="https://iamrobertmoore.github.io/record-date/"><b>Evidence page</b></a> &nbsp;·&nbsp;
<a href="https://explorer.solana.com/address/ycg2obpKmccwAz1zGf4QqgV2vnkWd6CQKLdJSGDxtmG?cluster=devnet"><b>Program on devnet</b></a> &nbsp;·&nbsp;
<a href="docs/architecture.svg"><b>Architecture</b></a> &nbsp;·&nbsp;
<a href="#run-it-yourself"><b>Reproduce the numbers</b></a>

</div>

---

## The problem, measured

**377 of the 400 tokenized stocks on Solana that publish a withholding rate lose 30% of every dividend before it is reinvested. Across 6.6 months of the issuer's own feed that is $6,338,030 withheld, about $11,523,691 a year, and no surface a holder looks at reports it.**

And the dividend itself is not paid in cash. It is reinvested by quietly raising a `multiplier` stored inside the token's Token-2022 mint account. **On 361 of 777 mints the field named `multiplier` is not the multiplier**: it holds the value from before the most recent corporate action, and the live value sits in a second field beside it. A reader that takes the obvious one is one corporate action behind.

That read rule is not inferred from the field names. It is what Token-2022's own `process_update_multiplier` does, and it was then checked against the issuer's own published current value on **every** mint rather than a sample: **777 agreed, 0 disagreed, 0 published nothing to compare against.** The comparison is one of the build's checks and it fails the page if it ever stops holding.

The multiplier reinvests the **net** dividend, and that is asserted rather than assumed, because it is the claim the whole entry rests on. If the chain reinvested the gross, the 30% would never reach the token and the withholding would be a number in a feed. It does not: divide the feed's per-unit cashflow by the multiplier step and the answer is the share price, and doing that with the net figure lands on the market at **1.035** where the gross figure reads **1.479**. The control is the 40 zero-rate events, where the feed's net and gross are the same number and the test correctly has no preference: both read **1.007**.

---

## What was already known before this entry

**The read rule is not my discovery and this entry does not claim it.** Solana's own Token-2022 documentation describes the stale field. [ExDate](https://github.com/AlperJ/exdate) published it with worked examples on 13 September 2026, alongside the 30% withholding rate and a market-wide dollar total, and its `PRIORART.md` names [SolanaRWA](https://solanarwa.app) as having turned these same multiplier changes into per-holder income since May 2026, and Lido's stETH reward history as the five-year-old precedent. All of that is real prior art on the *mechanic*, and a judge who finds it after an entry has claimed novelty will stop reading.

What ExDate and SolanaRWA are both built as is a **holder's statement**: what a wallet was paid, read back from the chain. ExDate ships no program of any kind, and says so.

What this entry is built as is a **venue's control**, and the timing ground is not empty. [Kamino](https://gov.kamino.finance/t/kamino-is-integrating-xstocks-powered-by-the-chainlink-data-standard-to-enable-tokenized-equities-lending/792), the first major lending protocol to take tokenized equities as collateral, already holds corporate-action timestamps and mitigates outside trading hours with a **price band on the price feed it accepts**. A band is a heuristic applied to a price. What is not there is a timestamp a program can read without one, which is the first thing below. The second is a unit error in the field the prior art recommends:

1. **When the activations land**, counted against the exchange's own published calendar rather than a window I chose. 631 activations, and **57 of them take effect on a day the US market does not trade at all**. The premise is the organiser's own number rather than mine: the Solana Foundation's 13 September newsletter reports that **63% of tokenized-equity volume settles outside US market hours**.
2. **The price the issuer publishes beside each mint is not always in dollars.** 124 of the 777 mints track an equity that does not trade in dollars, and the reference feed quotes them in the underlying's own currency. Read at face value, the 27 London listings priced in pence took the market value from $5.99bn to $13.22bn.

---

## Who this is for

**Tomas holds about $9,000 of tokenized US equities in a Solana wallet.** He is not a trader. He bought them because they pay dividends and they settle in seconds.

In March, May and August the issuer reinvested his XOMx dividend. Each time it withheld 30% first. Tomas saw none of it. His token balance never moved, because Token-2022 leaves the raw amount alone and expects the reader to apply the multiplier. The one field his wallet did read was stale, so it showed a position that had not changed when it had. Nothing on chain told him a corporate action had happened, and nothing told him a third of his dividend had been withheld before the reinvestment.

The second reader is the protocol on the other side. A lending market that takes xStocks as collateral prices them by multiplying the raw amount by whatever the mint says. Get the field wrong and the position is mispriced at exactly the moment a dividend lands.

The third is a venue, and it is the one this entry is written for. The issuer's own docs ask trading venues to pause for fifteen minutes either side of an activation, and say nothing enforces it. A venue that wants to comply has to know when to, and no surface tells it.

---

## Try this, watch what happens

| Try this | Watch what happens |
|---|---|
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

It grows across the first four buckets. Across 434 reconciliations on 342 names:

| Age of the activation | Events | Median gap against today's price |
|---|---:|---:|
| 0 to 2 days | 41 | 1.5% |
| 3 to 10 days | 34 | 3.2% |
| 11 to 30 days | 151 | 4.9% |
| 31 to 60 days | 118 | 6.9% |
| over 60 days | 90 | 4.7% |

**The oldest bucket is the one where the gradient does not continue, and it is reported rather than smoothed.** From fresh to sixty days the gap goes 1.5% to 6.9%, which is what the identity predicts. Past sixty days it comes back to 4.7%. I do not have an explanation I can test for that, so the build asserts only what it can: a fresh activation reconciles tighter than an old one, at **2.1% within ten days against 4.7% beyond sixty**. A monotonic claim would be a stronger sentence and it would not be true of the data in front of me.

---

## When the multiplier moves

The issuer's own docs tell venues and protocols to pause for fifteen minutes around each activation, and say nothing enforces it. Whether that matters depends entirely on when the activations land, which the issuer publishes and, as far as I can find, nobody has counted. So I counted them. All **631** activations the feed and multiplier history carry, by the minute of the day they take effect:

| Time (UTC) | Activations | New York clock |
|---|---:|---|
| 00:30 | 399 | 20:30 ET the previous day |
| 23:55 | 177 | 19:55 ET, after the close |
| 00:15 | 11 | 20:15 ET the previous day |
| 01:15 | 5 | 21:15 ET the previous day |

**622 of the 631, or 98.6%, fall outside the US regular session as the exchange itself defines it, and 93.8% of the sample lands on those four minutes.**

That is measured twice, because one definition of "the market is open" would be mine and the other is the exchange's. The first is a fixed UTC window drawn wide at 13:00 to 21:00, which covers the session under both daylight time and standard time and can only make the finding harder to reach: by that definition 619 of the 631, or 98.1%, are outside. The second is the calendar Pyth publishes in its feed directory, with no key: `America/New_York`, a 09:30 to 16:00 session, and twelve listed holiday and half-day overrides. Against that, 9 of the 631 fall inside the session, so 622 are outside. The two are computed independently and the build requires both to agree the market is shut. They disagree about three activations and agree about the other 628.

The sharper number is the one neither window is needed for. **57 activations, 9.03% of the sample, take effect on a day the market does not trade at all.** On those days the pause is not merely advisable. The underlying is shut for the whole session and the token is not, which is the premise this finding rests on rather than an assumption: the Solana Foundation's 13 September newsletter reports that **63% of tokenized-equity volume settles outside US market hours**, and the event brief names that as a theme.

This is the part of the story that is not a bug in anyone's code. Activation is scheduled for 00:30 UTC the day after the ex-date, four and a half hours after the US close and ten hours before the next open. The fifteen minute pause is what stands in for a market, and it is a recommendation in a document rather than a constraint in a program. **Nothing can enforce it from inside a mint account, which is why this entry ships a program instead of a warning.**

---

## Two price fields, and one of them is not a price

A tokenized stock on Jupiter carries two prices. `usdPrice` is the pool quote. `stockData.price` is a reference feed. They are not interchangeable, and on 17 September 2026 they differed by more than 20% on **14** mints.

The worst is CLSKx. The pool quote was **$395.01**. The reference was **$12.95**. A factor of 30, on a token whose underlying is CleanSpark.

The reconciliation settles it, because it used no price at all. On SCHFx's activation of 24 June 2026, `net ÷ step` gives **$27.40**: within 0.4% of the reference of $27.51, and 74% away from the pool quote of $106.34. On MRKx, `net ÷ step` gives **$141.78** against a reference of $141.26 and a pool quote of $43.31.

Across the **ten** disagreeing events that have a reconciliation, the field this build uses reconciles to a **median error of 0.7%**, against **33.1%** for the field it rejected. The build asserts that, and it asserts an absolute bound as well as a relative one: beating the other field by four times is not evidence that a field is a price, so the chosen field also has to clear 25%. The relative-only form is what lets a field that is 100% out pass against one that is 1,000% out.

**Where there is no dividend event the reconciliation cannot adjudicate, and that is stated rather than hidden.** CLSKx has no activation in the feed, so its pool quote is rejected on the strength of the reference field alone, with no independent confirmation. That is why the build uses one field throughout and records the disagreement rather than switching field by field: a rule that picked whichever field looked better on the day would be unfalsifiable.

This is also the one place the entry is standing on somebody else's lesson. [ExDate](https://github.com/AlperJ/exdate) published its own verification ledger on 13 September 2026 and found that seven of its 65 payers were priced more than 30% off their underlying, one of them by 61.9x, carrying a quarter of its headline; it noted that Jupiter returns the reference price in the same payload and that the data to self-check "was already in hand and unused". This build uses that reference field as the price, and the checks above are the self-check it asked for.

---

## The reference price is not always in dollars

The field ExDate recommends is the right one, and taking it at face value is the next mistake along.

**124 of the 777 mints track an equity that does not trade in dollars.** The issuer's asset API publishes the underlying's currency and the listing exchange beside every deployment, and the reference feed quotes the price in that currency rather than in dollars. Of the 684 mints this build can price, 31 are non-dollar: 27 London listings, 2 in euros, 2 in Hong Kong dollars.

The London listings are the ones that bite, because they are quoted in **pence**. BARCx publishes `480.40` against a dollar price of $6.46. HSBAx publishes `1524.80` against $20.52. Every one of the 27 is published at **74.3 times** its dollar price, which is the pence-to-dollar factor at the rate used.

Read at face value, the market value of the 684 priced mints is **$13.22bn**. Converted out of each underlying's own currency at the ECB reference rate for 2026-09-16, it is **$5.99bn**. The pence listings are 1.65% of the book by value and they move the total by a factor of **2.21**. The exact figures for a given build are in `data.json` and printed on the evidence page, because they move with the prices; the factor does not.

Two checks carry this, because a wrong unit is not a wrong number and it does not look like one:

- **the reference price is converted out of the underlying's own currency**: 31 of 684 priced mints are non-dollar listings, every one is converted at the ECB rate for 2026-09-16, and **0 are converted by nothing**
- **the London listings are read as pence, not pounds**: 27 LSE mints priced, the published value is between 74 and 74 times the dollar price

The reason this matters beyond a market value nobody trades on is that **the yield denominator is built from these prices.** A book that is 2.21 times too large reports a yield 2.21 times too small, and a plausible-looking yield is exactly the number that does not get questioned. This is also the one finding in the entry I have not seen anywhere else: ExDate's ledger recommends the reference field, prices seven assets off the pool quote, and does not mention currency, exchange or pence at all.

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
| `settlement_window` | Seconds since the last activation. A protocol that wants to follow the issuer's own advice to pause refuses to settle while that is inside 900. |

The first four instructions are the read done properly. **`settlement_window` is the one that exists because of the timing finding, and it is the issuer's own recommended pattern moved on chain rather than an invention.** Their docs tell integrators to fetch the pending multiplier with its activation timestamp and "adopt it locally once the activation timestamp is reached". A poller that does that is a private opinion; a program that holds the timestamp in an account any other program can read is a shared fact, and it is the difference between advice and a primitive. The issuer's docs ask venues to pause fifteen minutes either side of an activation. Nothing can enforce that from inside a mint account, because a mint account has no opinion about who is trading against it. What a program can do is hold the timestamp of the last activation in an account any other program can read, which turns a recommendation in a document into a value a venue can branch on. It does not stop anyone trading; it makes non-compliance a choice rather than an accident.

The mint is parsed as raw bytes rather than through the `spl-token-2022` crate, because the program does not link against the program that owns the mint.

**The layout is not the one the Token-2022 docs describe.** A mint account is a 165-byte base region, then the account-type byte, then the `(type: u16, length: u16, body)` entries. The mint itself fills only the first 82 bytes of that region. The remaining 83 are padding. Walking the extensions from byte 83, as reading the documented layout as "82-byte base, then extensions" would have you do, therefore lands in padding and finds nothing.

The 165-byte base region is deliberate and it is in the Token-2022 source, with the reason written above the constant:

> Any account with extensions must be at least `Account::LEN`. Both mints and accounts can have extensions. A mint with extensions that takes it past 165 could be indiscernible from an Account with an extension, even if we add the account type… With this approach, we only start writing the TLV data after `Account::LEN`, which means we always know that the account type is going to be right after that.
>
> `interface/src/extension/mod.rs`, above `BASE_ACCOUNT_AND_TYPE_LENGTH`

The `spl-token` client agrees and slices the same offset itself: `getMint` requires `data[165] == AccountType.Mint` and then takes `data.slice(165 + 1)`. So the header is computed, not searched for: the base region is a fixed 165 bytes, the first extension header is at 166, and a non-zero byte in the padding is an error rather than something to step past. **Asserted on all 777 live mints: the padding is zero in 777 of 777, the account-type byte is 1 in 777 of 777, and the `ScaledUiAmountConfig` header is at byte 275 in all 777.**

The walk from one header to the next is exact, and that is a decision rather than an omission. **On all 777 mints the extension run is dense and ends exactly at the end of the account**, so there is nothing to skip over. A tolerant walk that advanced a byte whenever a header looked implausible would be a way to return a *different* plausible-looking body without failing, which is worse than failing. The density is measured on every run and the page is not built if it stops holding.

Multipliers are stored as `f64` because that is how the mint stores them, and converted once to a `u128` fixed-point value at 1e18. There is no float arithmetic after that.

---

## Run it yourself

```bash
# every number on the page, from public endpoints, with no key
python3 scripts/fetch.py          # writes data.json, asserts 28 checks
python3 scripts/build_page.py     # writes index.html

# the checks themselves
python3 scripts/test_checks.py    # 30 negative controls, no network needed
```

If any check fails the script prints why and **refuses to build the page**. It is not possible to publish a stale number through this pipeline by accident. The checks include the ledger agreeing with the headline to the cent, the withheld total covering every rate rather than only the dominant one, the mint layout holding on every account read, the read rule matching the issuer on every mint, the multiplier reinvesting the net dividend rather than the gross, the reference price being converted out of the underlying's own currency, and the age gradient above.

**The net-versus-gross check is the one that carries the entry, and it has a control.** Divide the feed's per-unit cashflow by the multiplier step and the answer is a share price. Done with the net figure it lands on the market at 1.035; done with the gross it reads 1.479. The 40 zero-rate events are the control: there the feed's net and gross are the same number, so the test must have no preference, and it does not, reading 1.007 either way. Without that control the check would be measuring the arithmetic rather than the unit.

**Two of those checks could not fail for the reason they claimed, and both were rewritten.** One selected its rows on the error it then reported. The other compared a deduplicated list against the set of its own keys, which is true whatever survived. `scripts/test_checks.py` holds the old form of each next to the new one, on inputs built to separate them: it shows the old price selection collapsing to nothing when the choice is swapped, and the old dedupe check passing on a list where the wrong row survived. A check that cannot fail is worse than no check, because it looks like rigour.

```bash
# the program
anchor build --arch v1                                       # --arch v1 is required, see below
cargo test --manifest-path programs/record_date/Cargo.toml   # 12 unit tests, 13 integration tests
scripts/deploy.sh devnet                                     # refuses to deploy a mismatched id
```

**The integration tests read a real mint, not a fixture written to match the parser.** `fixtures/nvdax_mint.bin` is the actual 679-byte account of the NVDAx mint (`Xsc9qvGR1efVDFGLrVsmkzv3qi45LTBjeUKSPmx9qEh`) read from Solana mainnet, and `the_fixture_is_the_real_account_it_claims_to_be` asserts the layout on that account rather than on a synthetic one. Which is which, so the claim is not read as larger than it is: the Rust parser is proven on **one** real xStock, and the 777-mint assertions come from `scripts/fetch.py`, which implements the same offsets independently. Two implementations agreeing on a layout is the cross-check.

```bash
# the whole loop on devnet, against the deployed program, with no mocks
npm install
npm run demo        # creates a mint, schedules a dividend, waits for it, cranks a receipt
npm run demo:status # read what is already on chain, change nothing
```

**`--arch v1` is not optional.** `anchor build` defaults to `--arch v3`, and LiteSVM 0.10.0 cannot load a v3 ELF: it reports `InvalidAccountData`, which reads like a broken program rather than a build flag. There is a second trap underneath it. `cargo-build-sbf` caches per arch in `target/sbpf<arch>-solana-solana`, but `anchor build` only copies to `target/deploy/` when it actually compiles, so switching arch with nothing to rebuild leaves the **previous** arch's binary in place to be deployed. `scripts/deploy.sh` therefore removes `target/deploy/record_date.so` first and then verifies the `e_flags` of what the build produced, rather than trusting the flag. The integration tests check the same thing, so a v3 build fails with an instruction instead of a mystery.

The binary deployed to devnet is the binary the tests ran against. The comparison is not `==`, and that is the point: `solana program dump` returns the whole programdata account, which is padded past the end of the ELF, so exact equality reports a difference that is not there.

```
built     273848 bytes  e_flags 1  sha256 8fff81586bb91029
deployed  283288 bytes  e_flags 1  sha256 c5180355299c7522  (9440 bytes of trailing padding)
deployed ELF content is identical to the built one: true
```

`scripts/deploy.sh` makes that comparison and refuses the deploy if it fails, and the check has been run against a deliberately mutated binary to confirm it fails for the reason it claims.

---

## What this does not claim

- **The 30% may not be a permanent loss.** It is withheld at source and a holder may be able to credit it at home, depending on where they live. What is not in doubt is the rate applied on 377 of 400 symbols, the exactness of its application, and that the holder is never shown it.
- **Why 30% rather than a treaty rate is not asserted.** The feed shows the rates and the arithmetic, not the reason. 20 symbols are at 0% and 3 at 5%, which is evidence a lower rate is achievable and nothing more than that.
- **The dividend is not lost, it is reinvested.** The holder's position grows by the net amount, and the multiplier step is what proves the net is the figure that reaches the token. The claim is that it grows by less than the company paid, and that no surface reports the difference.
- **No arbitrage is claimed.** Liquidity behind these tokens is thin. The deepest pool carries about $1.8m. The two-field section above is the reason a price comparison against the underlying was not pursued as a trading claim: on some mints the pool quote is not a price at all, so a spread against the underlying is measuring the pool's thinness rather than an opportunity.
- **The age gradient is not monotonic and is not claimed to be.** It holds from fresh to sixty days and comes back down in the oldest bucket. The build asserts the weaker, testable version.
- **The window is the feed's, not a clean year.** 6 March 2026 to 24 September 2026, scaled to a year rather than twelve measured months. Supply is today's rather than time weighted, so a token that minted heavily after its dividend is over-counted. The feed is an upcoming feed, so the window runs seven days past the build date: **$23,676,471 of the $24,245,993 gross is dated on or before today and $569,522 is still forward.** Both are reported and a check proves they add up. Read the annual figure as an order of magnitude.
- **The read rule, the withholding rate and the dollar total are not this entry's findings.** They are ExDate's, published on 13 September 2026, and Solana's own Token-2022 documentation describes the read rule. **Nor is the timing area unoccupied.** Kamino holds corporate-action timestamps and runs a price band outside trading hours, and other entrants in this event are working the same ground. What this entry adds is narrower than an earlier draft of it claimed: a timestamp a program can read without a price feed, and the currency the reference price is quoted in.
- **The field is not empty and this entry is not alone in it.** 91 projects were submitted at the time of writing and none are published, so this is what competitors claim rather than what I could verify they do. Four are on the same ground: `openbell-solana` gates execution on off-hours premiums and raw-versus-scaled amount mistakes, `basis-terminal` productises the price gap, `multiplier` is a corporate-actions oracle, and `corporate-action-guard` (3 September 2026, before this event opened) issues fail-closed preflight receipts against stale corporate-action state on a different chain. I read their READMEs, not their code.
- **This is one issuer.** 777 mints from Backed's own asset API, which is exhaustive over what Backed publishes and is not a census of every equity token on Solana.

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
| the underlying's currency and venue | `https://api.xstocks.fi/api/v2/public/assets` |
| the pause recommendation, and the polling pattern this program implements | [docs.xstocks.fi/developers/multipliers](https://docs.xstocks.fi/developers/multipliers) |
| 63% of tokenized-equity volume outside market hours | [Solana Foundation newsletter, 13 September 2026](https://solanacompass.com/news/solana-tokenized-stocks-beat-nyse-and-nasdaq-combined-in-volume-with-63-of-trades-after-market-hours) |
| the venue that already holds corporate-action timestamps | [Kamino governance forum, 14 July 2025](https://gov.kamino.finance/t/kamino-is-integrating-xstocks-powered-by-the-chainlink-data-standard-to-enable-tokenized-equities-lending/792) |
| dividend mechanics | [docs.xstocks.fi/docs/dividends-and-stock-splits](https://docs.xstocks.fi/docs/dividends-and-stock-splits) |
| the multiplier contract | [docs.xstocks.fi/developers/multipliers](https://docs.xstocks.fi/developers/multipliers) |
| the read rule, in code | [`process_update_multiplier`](https://github.com/solana-program/token-2022/blob/main/program/src/extension/scaled_ui_amount/processor.rs) |
| the account layout, in code | [`interface/src/extension/mod.rs`](https://github.com/solana-program/token-2022/blob/main/interface/src/extension/mod.rs) |

Pyth's price *values* need a key and are not used here. Its feed directory, which carries the exchange calendar, is public, and the build takes the session definition from it and the numbers beside it from elsewhere. The source comment in `fetch.py` says so rather than leaving it to be inferred.

**Prior art this entry is built on top of:** [ExDate](https://github.com/AlperJ/exdate) (the read rule, the withholding rate, the dollar total, and the split-versus-dividend distinction), [SolanaRWA](https://solanarwa.app) (per-holder dividend records on this chain since May 2026), [Kamino](https://gov.kamino.finance/t/kamino-is-integrating-xstocks-powered-by-the-chainlink-data-standard-to-enable-tokenized-equities-lending/792) (corporate-action timestamps and a price band outside trading hours, in production since July 2025), and [Lido's stETH reward history](https://stake.lido.fi/rewards) (the same mechanic on Ethereum since 2021).

Built for **Stocklana**, by Robert Moore. MIT licensed.

