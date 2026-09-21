# Corrections

Every correction made to this entry's published claims, with the date it was made and what the
check now is. The README carries the claims that hold today. This file carries the ones that did
not, because a retraction belongs in a record rather than on the first screen of a product page.

**A correction is only worth recording if a check now stands behind the corrected form.** Where
that is not true it is said so rather than implied.

---

## 21 September 2026

**The published README contradicted itself about the mint's history.** The first screen said the
mint "keeps two multipliers and no more". Line 182 of the same file said it "holds the most recent
change and overwrites it". Both sentences were in the published tree at the same time, and the
first is the right one. A mint account keeps the value from before the last change and the latest
beside it. What it does not keep is anything earlier than those two.

**The first bold line of the README could be falsified and the build passed.** The reviewer moved
one figure at a time in the headline sentence and ran the page build. Three of four mutations
built clean: `lose 30% of every dividend` changed to `lose 26.2%`, `631 fall outside the US
regular session` changed to `628`, and `377 of the 400` changed to `370 of the 400`. Each of those
substituted a real figure from the build into a sentence where it means something else. 26.2% is
the withheld share of gross rather than the withholding rate, and 628 is the count under a fixed
UTC window rather than under the exchange's own calendar. The three sentences are now pinned as
exact strings in `scripts/published.py`, and three controls in `scripts/test_checks.py` prove each
pin can fail.

---

## 20 September 2026

**The test count was wrong by one in the README.** It said 96 negative controls where the suite
ran 99. The count was corrected in five files and both superseded forms are held as stale strings
so a revert cannot restore one.

**Pyth was described wrongly in the sources section.** An earlier version said Pyth's price values
"need a key and are not used here". Both halves were wrong. The values reach a Solana program with
no key because the account is on chain, and they are used, by `verify_against_pyth`. The claim sat
in the sources section of the published page where a judge would read it, which is why the
correction is recorded rather than quietly applied.

---

## 17 September 2026

**The activation timestamp was claimed as this entry's own.** An earlier version said the timestamp
was not readable on chain, and that holding it in an account was the difference between advice and
a primitive. That was wrong. The mint's `ScaledUiAmountConfig` carries the activation timestamp,
any program can read it, and EquityGuard branches on it directly with no price feed. What the mint
does not carry is history, and that is the narrower thing this entry actually adds.

**An on-chain program was called near-unique differentiation.** That was wrong when it was written.
Of the 75 repositories the event had produced at the time, 19 shipped an on-chain program. The
count has since moved to 27 of 108 and is measured by a dated sweep rather than by the build.

**Kamino's control was described as a price band.** It is not. Scope suspends a price for the 24
hours before the activation timestamp its Chainlink report carries, and has since November 2025.

**The record claim was unconditional in thirteen places.** An earlier version said the program
"records every activation", and several surfaces said "every activation on chain". `record_activation`
writes when a crank calls it, so the record holds every activation that was cranked before the next
one, and two moves with no crank between them leave one receipt. The corrected form is asserted on
the surfaces that carry it.

**The settlement window was described as one number.** An earlier version said the program "writes
a receipt for every activation, and exposes the seconds since the last one". The window returns two
numbers, seconds since the last activation and seconds until one the issuer has staged. The second
is the half the mint cannot express, because while a value is staged the mint's single timestamp
describes the value that has not arrived.

---

## Earlier

**`1.53%` where the build held `1.54%`.** The pence slice of the book. The two figures came from
two rounding paths over the same number: a float prints 0.01535 as 1.54 and `%f` on a `Decimal`
prints it as 1.53. The rule is now written out with `ROUND_HALF_UP` in one place.

**The page and the banner printed the same worked example differently.** The banner carried one
mint's two multipliers rounded to ten decimals and the page carried the same pair truncated, so the
banner's figures appeared nowhere in the page and 209 of the 758 printed multipliers differed in
the last digit. There is now one rounding helper, and the build refuses to write a page whose
figures the banner does not carry.

**A figure could be moved on the README and every check passed.** The checks asked whether a string
was present somewhere, not whether it was current. The build now also asks the other direction:
every money token, percentage, `N of M` pair and three-decimal ratio on the README has to be
derivable from the build or be on an explicit allow-list with a stated reason, and an allow-list
entry that fires for nothing fails the build.
