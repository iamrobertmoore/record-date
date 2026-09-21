<div align="center">

<img src="docs/brand/readme-banner.svg" alt="Record Date: the dividend is a multiplier inside the mint account" width="820">

<br>

**Tokenized stocks pay dividends invisibly, 30% short, and at hours no market is open.**

<a href="https://iamrobertmoore.github.io/record-date/desk.html"><b>Settlement desk</b></a> &nbsp;·&nbsp;
<a href="https://iamrobertmoore.github.io/record-date/"><b>Evidence page</b></a> &nbsp;·&nbsp;
<a href="https://explorer.solana.com/address/ycg2obpKmccwAz1zGf4QqgV2vnkWd6CQKLdJSGDxtmG?cluster=devnet"><b>Program on devnet</b></a> &nbsp;·&nbsp;
<a href="docs/architecture.svg"><b>Architecture</b></a> &nbsp;·&nbsp;
<a href="#reproduce-it">Reproduce the numbers</a>

</div>

---

**927 mints read on chain · 640 activations timed against the exchange's own calendar · 370 of 927 mints where the obvious field is the wrong one · 56 tests · 32 checks · 99 negative controls**

Depth after the counts: the 32 checks refuse to build the page, the 99 controls prove each check can fail, and the deployed binary is verified against its own chain bytes rather than against a deploy log.

---

## Verify every claim below in five minutes

| Claim | How | What you should see |
|---|---|---|
| 927 mints read, 370 carrying the wrong value in the obvious field | `python3 scripts/fetch.py` | `927 agreed, 0 disagreed, 0 published nothing to compare against` |
| Every figure on this page matches the build | `python3 scripts/build_page.py` | exits 0, or names the figure it wanted and writes no page |
| The checks themselves can fail | `python3 scripts/test_checks.py` | `99 of 99 as expected`, no network needed |
| The deployed program is the tested binary | `cat docs/deploy.txt` | `deployed ELF identical to the tested binary` |
| The program answers a question | [Settlement desk](https://iamrobertmoore.github.io/record-date/desk.html) | both sides of the window, and a hold or settle verdict |
| The record is on chain | [Program on devnet](https://explorer.solana.com/address/ycg2obpKmccwAz1zGf4QqgV2vnkWd6CQKLdJSGDxtmG?cluster=devnet) | the `Receipt` accounts the program wrote |

---

## What this is

**A tokenized stock pays its dividend by quietly raising a `multiplier` stored inside its Token-2022 mint account.** The mint keeps two multipliers and no more: the value from before the last change, and the latest beside it with the timestamp it takes effect. **On 370 of 927 mints the field named `multiplier` is not the multiplier.** It holds the previous one, and the live value sits in a second field beside it. A reader that takes the obvious one is one corporate action behind.

That is not a guess from the field names. It is what Token-2022's own `process_update_multiplier` does, and it was checked against the issuer's own published current value on **every** mint rather than a sample: **927 agreed, 0 disagreed, 0 published nothing to compare against**. The comparison is one of the build's checks and it fails the page if it ever stops holding.

**Tomas holds about $9,000 of tokenized US equities in a Solana wallet.** He is not a trader. He bought them because they pay dividends and they settle in seconds. In March, May and August the issuer reinvested his XOMx dividend, and each time it withheld 30% first. Tomas saw none of it, because Token-2022 leaves the raw amount alone and expects the reader to apply the multiplier. **This is the reader that applies the right one, and the program that keeps the record the mint throws away.**

Two more readers. A lending market that takes xStocks as collateral prices them by multiplying the raw amount by whatever the mint says, so getting the field wrong misprices the position at the moment a dividend lands. And a venue: the issuer's own docs ask venues to pause for fifteen minutes either side of an activation and say nothing enforces it.

---

## The proof, on one real mint

XOMx, the Exxon Mobil xStock, read from its mint account on mainnet:

| | |
|---|---|
| the field named `multiplier` | **1.0130866** |
| the live value, in force since 2026-08-15 00:30 UTC | **1.0176490** |
| a 100-share position, as a wallet reading the obvious field shows it | **101.31** |
| the same position, as the mint actually holds it | **101.76** |

**Two numbers in one account, and the one with the obvious name is a dividend behind.** The same mint's feed shows $3.09 a share paid gross and $2.163 reinvested: **$0.927 a share withheld at 30%**, $112,966 across the float, and nothing in the holder's wallet says so. The [settlement desk](https://iamrobertmoore.github.io/record-date/desk.html) opens on this mint and reads it live, and you can type every one of the 379 is on the live board beneath it.

---

## Try this, watch what happens

| Try this | Watch what happens |
|---|---|
| [Open the settlement desk](https://iamrobertmoore.github.io/record-date/desk.html) | Pick a mint and the desk shows the field named `multiplier` against the live value, what your wallet would show, what you own, the dividends reinvested, what was withheld at 30%, and the next staged activation with its hold or settle verdict |
| [Open the evidence page](https://iamrobertmoore.github.io/record-date/) | Every figure in this file, with the eight freshest reconciliations and the median gap against the market bucketed by age |
| [Fetch NVDAx's multiplier history](https://api.xstocks.fi/api/v2/public/assets/NVDAx/multiplier/history?page=0&pageSize=5&network=Solana) | The issuer's own record of each activation, with the previous and new multiplier side by side and no API key |
| [Open the program on devnet](https://explorer.solana.com/address/ycg2obpKmccwAz1zGf4QqgV2vnkWd6CQKLdJSGDxtmG?cluster=devnet) | The deployed program, its IDL, and the `Receipt` accounts it writes, **each activation on chain as a crank** writes it |

---

## The problem, measured

**377 of the 400 tokenized stocks on Solana that publish a withholding rate lose 30% of every dividend before it is reinvested. Across 6.6 months of the issuer's own feed that is $6,373,807 withheld, about $11,588,741 a year, and nothing in the holder's wallet shows it.** Of that, $23,795,729 of the $24,365,251 gross is dated on or before today and $569,522 is still forward, both reported rather than netted off.

The multiplier reinvests the **net** dividend, and that is asserted rather than assumed, because it is the claim the whole entry rests on. Divide the feed's per-unit cashflow by the multiplier step and the answer is the share price: doing that with the net figure lands on the market at **1.033** where the gross figure reads **1.476**. The control is the 40 zero-rate events, where the feed's net and gross are the same number and the test correctly has no preference.

**And the dividend does not arrive during trading.** 640 activations, of which **631 fall outside the US regular session as the exchange itself defines it**, and **57 of them take effect on a day the market does not trade at all**. The premise is the organiser's own number rather than mine: the Solana Foundation's 13 September newsletter reports that **63% of tokenized-equity volume settles outside US market hours**. The issuer's own documentation tells venues to pause for fifteen minutes around each activation and attaches no enforcement to it.

---

## The business

**The program is free and permissionless. Record Date sells the feed, not the primitive.** One subscription per integrating protocol, **$500 a month**, covering every xStock that protocol lists on Solana. The buyer is a venue or a lending market that must not settle inside a window the issuer only recommends pausing for, and that today has no record to read.

**Who pays:** the protocol, not the holder. **What grows:** every issuer that ships an equity token on Token-2022, because the same subscription covers them all without a new integration, and every protocol that lists one. The first two integrations are free.

---

## How the platform is used

Six distinct uses of Solana, each load-bearing. **Remove any one and either the mechanism disappears or the number is the issuer agreeing with itself.**

| # | Use | Why it is load-bearing | Where |
|---|---|---|---|
| 1 | Token-2022 `ScaledUiAmountConfig`, both multipliers | the whole footgun is that two fields exist and the obvious one is named `multiplier` | `programs/record_date/src/token2022.rs:71` |
| 2 | Resolving which multiplier is live from the activation timestamp | remove it and every reader is one corporate action behind | `programs/record_date/src/token2022.rs:98` |
| 3 | The Anchor program: the record and the window | remove it and there is no series to read and no verdict to branch on | `programs/record_date/src/lib.rs:53`, `programs/record_date/src/lib.rs:76` |
| 4 | A Pyth `PriceUpdateV2` account read inside the program | remove it and the price is the issuer agreeing with itself | `programs/record_date/src/instructions/verify_against_pyth.rs:46` |
| 5 | Pyth's own published calendar | the timing finding is the exchange's own definition rather than a window I chose | `scripts/fetch.py:240` |
| 6 | The desk's browser-built transaction | a judge asks the deployed program with no key, no backend and no library | `scripts/desk_template.html:920` |

`bind_pyth_feed` stores the feed id against a registered mint rather than the price account's address, because a `PriceUpdateV2` account is rewritten on every publish and its address is not stable.

---

## The count nobody had made

The issuer publishes when each activation lands. As far as I can find it had not been counted by the minute, so I counted it. **640 activations**, drawn from the 370 symbols that both pay a dividend and carry a price, by the minute of the day they take effect:

| Time (UTC) | Activations | New York clock |
|---|---:|---|
| 00:30 | 407 | 20:30 ET the previous day |
| 23:55 | 177 | 19:55 ET, after the close |
| 00:15 | 11 | 20:15 ET the previous day |
| 01:15 | 5 | 21:15 ET the previous day |

**631 of the 640, or 98.6%, fall outside the US regular session as the exchange itself defines it, and 93.8% of the sample lands on those four minutes.**

That is measured twice, because one definition of "the market is open" would be mine and the other is the exchange's. A fixed UTC window drawn wide at 13:00 to 21:00 can only make the finding harder to reach, and by it 628 of the 640 are outside. The calendar Pyth publishes in its keyless feed directory, `America/New_York` with a 09:30 to 16:00 session and twelve holiday and half-day overrides, puts 631 outside. The build requires both to agree the market is shut.

The sharper number needs neither window. **57 activations, 8.9% of the sample, take effect on a day the market does not trade at all.** On those days the pause is not merely advisable. The underlying is shut for the whole session and the token is not.

This is the part of the story that is not a bug in anyone's code. Activation is scheduled for 00:30 UTC the day after the ex-date, four and a half hours after the US close and thirteen hours before the next open. The fifteen minute pause is what stands in for a market, and it is a recommendation in a document rather than a constraint in a program. **Nothing can enforce it from inside a mint account, which is why this entry ships a program instead of a warning.**

---

## Two price fields, and one of them is not a price

A tokenized stock on Jupiter carries two prices. `usdPrice` is the pool quote. `stockData.price` is a reference feed. They are not interchangeable, and on 2026-09-17 they **differed by more than 20% on **15** mints**. **The widest is a factor of **30****, on `Xsn3H7AC…`, whose pool quote is $395.01 against a reference of $13.355.

The reconciliation settles which one is a price, and it used no price at all. Across the **443 reconciliations on 351 names** it covers, `net ÷ step` lands on the market at **1.033** while the gross reads **1.476**. It grows with the age of the activation, which is what the identity predicts:

| Age of the activation | Events | Median gap against today's price |
|---|---:|---:|
| 0 to 2 days | 41 | 1.7% |
| 3 to 10 days | 34 | 2.5% |
| 11 to 30 days | 160 | 4.9% |
| 31 to 60 days | 118 | 6.9% |
| over 60 days | 90 | 4.8% |

**370 of the 379 tokens priced here carry a past activation timestamp, median age 26.7 days.** From fresh to sixty days the gap goes 1.7% to 6.9%, and the build asserts the tested form: **2.1% within ten days against 4.8% beyond sixty**.

---

## The reference price is not always in dollars

The field ExDate recommends is the right one, and taking it at face value is the next mistake along.

**174 of the 927 mints track an equity that does not trade in dollars.** The reference feed quotes the price in the underlying's own currency rather than in dollars, and the London listings are quoted in **pence**. A FTSE share is published at roughly **75 times** the dollar price of the same share, because the exchange quotes in pence and the feed carries that figure through as though it were pounds. On this build every one of the 27 is published at **74.8 times** its dollar price, a ratio pinned to 100 divided by the GBP rate rather than varying with the share, which is why it is one number and not a range.

Read at face value, the market value of the 784 priced mints is **$13.81bn**. Converted out of each underlying's own currency at the ECB reference rate for 2026-09-17, it is **$6.46bn**. The London listings are **1.54% of the book** by value and **1.54% of the converted book** is all of the difference: reading a pence figure as dollars multiplies that slice by 75, so a slice that rounds to nothing is the whole of the error. The reason this matters beyond a market value nobody trades on is that **the yield denominator is built from these prices**, and a book that is **2.14 times too large** reports a yield 2.14 times too small. A plausible-looking yield is exactly the number that does not get questioned.

---

## What is on chain

The program is deployed to devnet at `ycg2obpKmccwAz1zGf4QqgV2vnkWd6CQKLdJSGDxtmG`. Eight instructions. `register_mint` stores the live multiplier **alongside** the value in the field the mint calls `multiplier`, so the gap between them is auditable. `record_activation` is permissionless and writes a `Receipt` when the multiplier has moved, holding the previous value, the new one, the issuer's own timestamp and the raw supply, ordered and never edited. `read_entitlement` returns raw units in the units a wallet should display, in integer arithmetic throughout. `activation_pending` returns one byte, so a cranker does not pay for a transaction that reverts.

**`settlement_window` is the one that exists because of the timing finding.** It returns two little-endian `i64`s, seconds since the last activation and seconds until one the issuer has staged, and `-1` on either means there is nothing to report because `0` is a real answer and means 1970. **The mint carries a timestamp a program can read, and no history.** `ScaledUiAmountConfig` holds the activation timestamp and EquityGuard branches on it directly with no price feed. What the mint does **not** carry is history: it keeps the value from before the last change and the latest beside it, and everything earlier is gone. While a value is staged, the mint's single timestamp describes the value that has **not** arrived. The window returns both halves, and the second is the one the mint cannot express at all.

**A receipt's `effective_at` is the issuer's own timestamp, and it can be 0.** The issuer stages the next multiplier about four hours before it takes effect, and staging overwrites the timestamp of the value now in force, so a crank landing in that window records the change it can see and dates it `0`. Measured on SATAx: the issuer staged its next value at 20:20:17Z for `2026-09-17T00:30:00Z`, so the mint carried a timestamp that had not arrived for 4h 09m 43s.

**The age limit on a Pyth price is the caller's number, not the program's.** A venue settling inside the session might want a minute, while one settling a weekend corporate action has to accept Friday's close, so the program enforces a one-day ceiling and lets the caller choose below it. Sampling the live AAPL account ten times over three minutes on 2026-09-17, it advanced its `publish_time` **nine times** and was never more than **24 seconds** old. The devnet AAPL fixture tests the staleness refusal because it was genuinely **33.6 days** old when it was captured.

**The mint is parsed as raw bytes rather than through the `spl-token-2022` crate**, because the program does not link against the program that owns the mint, and the layout is not the one the Token-2022 docs describe. The header is computed rather than searched for, and **asserted on all 927 live mints: the padding is zero in 927 of 927, the account-type byte is 1 in 927 of 927, and the `ScaledUiAmountConfig` header is at byte 275 in all 927.** `tests/mints/` holds all 927 mint accounts as raw bytes, captured from mainnet, and `tests/walk_census.rs` walks every one of them on a plain `cargo test` with no network: **927 accounts, 927 walked, 0 refused, body offset 279 on every one.** The Rust parser is also proven on a real account rather than a synthetic one, `fixtures/nvdax_mint.bin`, and `cargo clippy --all-targets -- -D warnings` is clean and wired into `scripts/deploy.sh`, so a warning stops a deploy. And two multipliers are never compared as floats: `TokenRecord` keeps the raw bits of both the trap value and the live one, and every decision compares a `u128` fixed point, because `0.0 == -0.0` is true and `NaN != NaN` is true.

---

## The Pyth track

This entry is built for **Best use of Pyth market data**, and it is worth naming rather than leaving a judge to infer it. The track's own description offers three Apple feeds and says builders can *"work with both the underlying market and the on-chain asset representing exposure to it"*. That is the comparison this entry is built on, and both halves are wired.

**The feed is bound on chain.** `bind_pyth_feed` stores the id `Equity.US.AAPL/USD` against a registered mint, and `verify_against_pyth` reads a `PriceUpdateV2` account owned by Pyth's receiver program. Pyth is consumed inside a Solana program rather than beside one. **The other side of the comparison is the mint**: the price Pyth is checked against is the mint's own, which is what makes the check worth making rather than a formality, because on 370 of the 927 mints the field named `multiplier` and the live value disagree.

---

## Scope

- **One issuer.** 927 mints from Backed's own asset API, every xStock it publishes on Solana.
- **The record on devnet holds demo activations.** The read rule is proven on all 927 real mints; feeding the on-chain record from them is the first roadmap item.
- **The annual figure is scaled** from 6.6 months of the issuer's feed, so read it as an order of magnitude.

**Built on:** the read rule is described in Solana's Token-2022 documentation, and **ExDate** published the withholding rate and a dollar total on 13 September 2026. **Kamino**'s Scope oracle suspends a price for the 24 hours before an activation. Record Date adds the count, the timing against the exchange's own calendar, and the on-chain record.

## Roadmap

1. Deploy to mainnet and register real mints, so the record holds real corporate actions.
2. Run a cranker, so activations are written as they happen rather than when someone calls.
3. Two venue integrations, which is what the subscription is priced for.
4. Replace the webhook-per-venue pattern with a subscription the protocol can poll.
5. Add issuers beyond this one, which the same subscription already covers.

Corrections to earlier versions of this file are recorded in [CORRECTIONS.md](CORRECTIONS.md).

---

## Reproduce it

```bash
# every number on the page, from public endpoints, with no key
python3 scripts/fetch.py          # writes data.json, asserts 32 checks
python3 scripts/build_page.py     # writes index.html, after checking the hand-written figures

# the checks themselves
python3 scripts/test_checks.py    # 99 negative controls, no network needed
```

`fetch.py` takes about twelve minutes, most of it the 927-mint read-rule pass against mainnet. If a price moves while it runs, `build_page.py` names the figure that moved and writes no page; run the fetch again.

```bash
# the program
anchor build --arch v1                                       # --arch v1 is required
cargo test --manifest-path programs/record_date/Cargo.toml   # 56 tests: 18 unit, 37 integration, 1 census
cargo clippy --manifest-path programs/record_date/Cargo.toml --all-targets -- -D warnings
scripts/deploy.sh devnet                                     # refuses to deploy a mismatched id
```

The binary deployed to devnet is byte-for-byte the binary the tests ran against: the deployed prefix equals the built file and everything after it is zero padding. `anchor build --arch v1` is required, because LiteSVM cannot load the default v3 ELF.

```
built            326696 bytes, sha256 fa4a700e3c304880
deployed         332952 bytes, 6256 of trailing padding
deployed ELF     identical to the tested binary
```

```bash
# the whole loop on devnet, against the deployed program, with no mocks
npm install
npm run demo        # creates a mint, schedules a dividend, waits for it, cranks a receipt
npm run demo:status # read what is already on chain, change nothing
```

---

## Sources

All public, none authenticated.

| | |
|---|---|
| mint list, currency and venue | `https://api.xstocks.fi/api/v2/public/assets` |
| corporate actions | `https://api.xstocks.fi/api/v2/public/corporate-actions/upcoming` |
| multiplier, current and history | `https://api.xstocks.fi/api/v2/public/assets/{SYMBOL}/multiplier/history` |
| mint accounts | `https://api.mainnet-beta.solana.com` |
| prices | `https://api.jup.ag/price/v3` |
| exchange rates, for the non-dollar listings | `https://api.frankfurter.app/latest` (ECB reference rates) |
| the exchange calendar, for the timing claim | `https://hermes.pyth.network/v2/price_feeds` |
| a Pyth price account, for the on-chain check | `https://api.mainnet-beta.solana.com`, reading the `PriceUpdateV2` account itself |
| the pause recommendation | [docs.xstocks.fi/developers/multipliers](https://docs.xstocks.fi/developers/multipliers) |
| 63% of tokenized-equity volume outside market hours | [Solana Foundation newsletter, 13 September 2026](https://solanacompass.com/news/solana-tokenized-stocks-beat-nyse-and-nasdaq-combined-in-volume-with-63-of-trades-after-market-hours) |
| the venue that already branches on the activation timestamp | [Kamino governance forum](https://gov.kamino.finance/t/kamino-is-integrating-xstocks-powered-by-the-chainlink-data-standard-to-enable-tokenized-equities-lending/792), and [Scope's `chainlink.rs`](https://github.com/Kamino-Finance/scope/blob/master/programs/scope/src/oracles/chainlink.rs) |
| the read rule, in code | [`process_update_multiplier`](https://github.com/solana-program/token-2022/blob/main/program/src/extension/scaled_ui_amount/processor.rs) |
| the account layout, in code | [`interface/src/extension/mod.rs`](https://github.com/solana-program/token-2022/blob/main/interface/src/extension/mod.rs) |

Pyth appears twice and neither use needs a key. Its **feed directory** is public, and the session definition above is taken from it. Its **price values** reach a Solana program without a key because Pyth posts every update to Solana as a `PriceUpdateV2` account owned by its receiver program, and a posted account is public state. No HTTP price API is called anywhere in this repository.

**Prior art this entry is built on top of:** [ExDate](https://github.com/AlperJ/exdate) (the read rule, the withholding rate, the dollar total), [SolanaRWA](https://solanarwa.app) (per-holder dividend records on this chain since May 2026), [Kamino](https://gov.kamino.finance/t/kamino-is-integrating-xstocks-powered-by-the-chainlink-data-standard-to-enable-tokenized-equities-lending/792) (the 24 hour suspension), and [Lido's stETH reward history](https://stake.lido.fi/rewards) (the same mechanic on Ethereum since 2021).

**And the same event, on the same ground.** A sweep of the 108 repositories this hackathon had produced when I counted on 20 September found **27 of the 108 repositories** shipping an on-chain program. The four closest are named here rather than left for a judge to find: [EquityGuard](https://github.com/Maheshsiddu29/EquityGuard) (an `assert_safe_execution` guard reading the mint's activation timestamp, deployed on devnet 13 September), [DividendX](https://github.com/notorious-d-e-v/dividendx-stocklana), [KEEL](https://github.com/Marc-Dvci/KEEL), and [stockcurve](https://github.com/ExpertVagabond/stockcurve) (which reads the same keyless `PriceUpdateV2` account this entry does). **131 projects** had been submitted by 20 September; entries stay hidden until the close.

Built for **Stocklana**, by Robert Moore. MIT licensed.
