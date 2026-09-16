<div align="center">

<img src="docs/brand/readme-banner.svg" alt="Record Date. A tokenized stock pays its dividend by raising a multiplier inside the mint account. Nothing on Solana reads it." width="820">

<br>

**Reads a tokenized stock's dividend out of the Token-2022 mint account, resolves the multiplier that is actually live, and records every activation on chain so entitlement is checkable rather than assumed.**

<a href="https://iamrobertmoore.github.io/record-date/"><b>Evidence page</b></a> &nbsp;·&nbsp;
<a href="https://explorer.solana.com/address/ycg2obpKmccwAz1zGf4QqgV2vnkWd6CQKLdJSGDxtmG?cluster=devnet"><b>Program on devnet</b></a> &nbsp;·&nbsp;
<a href="docs/architecture.svg"><b>Architecture</b></a> &nbsp;·&nbsp;
<a href="#run-it-yourself"><b>Reproduce the numbers</b></a>

</div>

---

## The problem, measured

**375 of the 398 tokenized stocks on Solana that publish a withholding rate lose 30% of every dividend before it is reinvested. Across 6.6 months of the issuer's own feed that is $6,306,456 withheld, about $11.5m a year, and no surface a holder looks at reports it.**

And the dividend itself is not paid in cash. It is reinvested by quietly raising a `multiplier` stored inside the token's Token-2022 mint account. **On 357 of 777 mints the field named `multiplier` is not the multiplier**: it holds the value from before the most recent corporate action, and the live value sits in a second field beside it. A reader that takes the obvious one is one corporate action behind.

That read rule is not inferred from the field names. It is what Token-2022's own `process_update_multiplier` does, and it was then checked against the issuer's own published current value on **every** mint rather than a sample: **777 agreed, 0 disagreed, 0 published nothing to compare against.** The comparison is one of the build's checks and it fails the page if it ever stops holding.

---

## Who this is for

**Tomas holds about $9,000 of tokenized US equities in a Solana wallet.** He is not a trader. He bought them because they pay dividends and they settle in seconds.

In March, May and August the issuer reinvested his XOMx dividend. Each time it withheld 30% first. Tomas saw none of it. His token balance never moved, because Token-2022 leaves the raw amount alone and expects the reader to apply the multiplier. His wallet did not apply it. The one field his wallet did read was stale, so it showed a position that had not changed when it had. Nothing on chain told him a corporate action had happened, and nothing told him a third of his dividend had been withheld before the reinvestment.

The second reader is the protocol on the other side. A lending market that takes xStocks as collateral prices them by multiplying the raw amount by whatever the mint says. Get the field wrong and the position is mispriced at exactly the moment a dividend lands.

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

on the activation date, recovered from the chain and the issuer's feed alone, with no oracle and no price feed. That claim makes a prediction. The implied price is fixed on the activation date and the market price is today's, so the gap has to be the stock's own movement, which means the gap must **grow** with the age of the activation. If the identity were an artefact of how the feed is written, the gap would be noise and the same size in every bucket.

It grows, and it grows monotonically. Across 429 reconciliations on 338 names:

| Age of the activation | Events | Median gap against today's price |
|---|---:|---:|
| 0 to 2 days | 35 | 1.7% |
| 3 to 10 days | 34 | 2.9% |
| 11 to 30 days | 151 | 4.0% |
| 31 to 60 days | 119 | 6.7% |
| over 60 days | 90 | 7.6% |

The arithmetic is identical in every bucket. The only thing that changed is how long the stock has had to move.

---

## Two price fields, and one of them is not a price

A tokenized stock on Jupiter carries two prices. `usdPrice` is the pool quote. `stockData.price` is a reference feed. They are not interchangeable, and on 16 September 2026 they differed by more than 20% on **nine** mints.

The worst is PYPLx. The pool quote was **$3,337.04**. The reference was **$52.92**. A factor of 63, on a token whose underlying is PayPal.

The reconciliation settles it, because it used no price at all. On PYPLx's activation of 4 September 2026, `net ÷ step` gives **$56.69**: within 7.6% of the reference, and 98.3% away from the pool quote. On SCHFx, `net ÷ step` gives $27.40 against a reference of $27.74 and a pool quote of $105.89. Where the two fields agree, as on MUx at 1.01x, both are close to the implied price, which is the control that shows the method is not simply rejecting whichever field is larger.

Across the nine disagreeing mints the field this build uses reconciles to a **median error of 0.6%**, against **33.1%** for the field it rejected. The build asserts that, and it asserts an absolute bound as well as a relative one: beating the other field by four times is not evidence that a field is a price, so the chosen field also has to clear 25%.

That is worth stating on its own. The price of a tokenized equity cannot be read off the pool for every name, the depth behind these tokens is thin, and a protocol that prices collateral from a mid is pricing it from something that is not always a price.

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
python3 scripts/fetch.py          # writes data.json, asserts 21 checks
python3 scripts/build_page.py     # writes index.html

# the checks themselves
python3 scripts/test_checks.py    # 16 negative controls, no network needed
```

If any check fails the script prints why and **refuses to build the page**. It is not possible to publish a stale number through this pipeline by accident. The checks include the ledger agreeing with the headline to the cent, the withheld total covering every rate rather than only the dominant one, the mint layout holding on every account read, the read rule matching the issuer on every mint, and the age gradient above.

**Two of those checks could not fail for the reason they claimed, and both were rewritten.** One selected its rows on the error it then reported. The other compared a deduplicated list against the set of its own keys, which is true whatever survived. `scripts/test_checks.py` holds the old form of each next to the new one, on inputs built to separate them: it shows the old price selection collapsing to nothing when the choice is swapped, and the old dedupe check passing on a list where the wrong row survived. A check that cannot fail is worse than no check, because it looks like rigour.

```bash
# the program
anchor build --arch v1                                       # --arch v1 is required, see below
cargo test --manifest-path programs/record_date/Cargo.toml   # 12 unit tests, 13 integration tests
scripts/deploy.sh devnet                                     # refuses to deploy a mismatched id
```

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

- **The 30% may not be a permanent loss.** It is withheld at source and a holder may be able to credit it at home, depending on where they live. What is not in doubt is the rate applied on 375 of 398 symbols, the exactness of its application, and that the holder is never shown it.
- **Why 30% rather than a treaty rate is not asserted.** The feed shows the rates and the arithmetic, not the reason. 20 symbols are at 0% and 3 at 5%, which is evidence a lower rate is achievable and nothing more than that.
- **The dividend is not lost, it is reinvested.** The holder's position grows by the net amount. The claim is that it grows by less than the company paid, and that no surface reports the difference.
- **No arbitrage is claimed.** Liquidity behind these tokens is thin. The deepest pool carries about $1.8m. The two-field section above is the reason a price comparison against the underlying was not pursued as a trading claim: on some mints the pool quote is not a price at all, so a spread against the underlying is measuring the pool's thinness rather than an opportunity.
- **The window is the feed's, not a clean year.** 6 March 2026 to 23 September 2026, scaled to a year rather than twelve measured months. Supply is today's rather than time weighted, so a token that minted heavily after its dividend is over-counted. The feed is an upcoming feed, so the window runs seven days past the build date: **$23,387,847 of the $24,134,135 gross is dated on or before today and $746,288 is still forward.** Both are reported and a check proves they add up. Read the annual figure as an order of magnitude.
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
| dividend mechanics | [docs.xstocks.fi/docs/dividends-and-stock-splits](https://docs.xstocks.fi/docs/dividends-and-stock-splits) |
| the multiplier contract | [docs.xstocks.fi/developers/multipliers](https://docs.xstocks.fi/developers/multipliers) |
| the read rule, in code | [`process_update_multiplier`](https://github.com/solana-program/token-2022/blob/main/program/src/extension/scaled_ui_amount/processor.rs) |
| the account layout, in code | [`interface/src/extension/mod.rs`](https://github.com/solana-program/token-2022/blob/main/interface/src/extension/mod.rs) |

Built for **Stocklana**, by Robert Moore. MIT licensed.

