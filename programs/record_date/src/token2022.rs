//! Reading a Token-2022 mint account, and getting the multiplier right.
//!
//! Nothing here uses the spl-token-2022 crate. The program reads the raw account bytes,
//! which is what makes it able to run against a mint owned by a program it does not link.

use crate::error::RecordDateError;
use anchor_lang::prelude::*;

/// The Token-2022 `Mint` base state: mint authority (4 + 32), supply (8), decimals (1),
/// is_initialized (1), freeze authority (4 + 32).
pub const MINT_BASE_LEN: usize = 82;

/// The Token-2022 base region. It is always `Account::LEN` bytes, whichever base state the
/// account holds, because the program only starts writing TLV data after this point. That is
/// deliberate: it means the account-type byte is at the same offset whether the account is a
/// mint or a token account, so the two cannot be confused.
///
/// A mint fills only the first 82 of these 165 bytes. The other 83 are padding and must be zero.
/// This is the offset the documented "walk from byte 83" gets wrong.
pub const BASE_REGION_LEN: usize = 165;

/// `AccountType::Mint`, the byte at `BASE_REGION_LEN` on a mint that has extensions.
pub const ACCOUNT_TYPE_MINT: u8 = 1;

/// Where the extension walk starts: past the base region and the account-type byte.
pub const TLV_START: usize = BASE_REGION_LEN + 1;

/// `ExtensionType::ScaledUiAmountConfig`. This is the extension that holds the multiplier.
pub const EXT_SCALED_UI_AMOUNT: u16 = 25;

/// The `ScaledUiAmountConfig` body: authority (32), multiplier (8),
/// new_multiplier_effective_timestamp (8), new_multiplier (8).
pub const SCALED_UI_AMOUNT_LEN: usize = 56;

/// Offset of `multiplier` inside the body.
const OFF_MULTIPLIER: usize = 32;
/// Offset of `new_multiplier_effective_timestamp` inside the body.
const OFF_EFFECTIVE_TS: usize = 40;
/// Offset of `new_multiplier` inside the body.
const OFF_NEW_MULTIPLIER: usize = 48;

/// The highest value in the Token-2022 `ExtensionType` enum. A byte pair above this cannot be
/// an extension header.
const MAX_EXTENSION_TYPE: u16 = 27;

/// The multiplier as the mint stores it, plus the supply and decimals read alongside.
#[derive(Debug, Clone, Copy)]
pub struct ScaledUiAmount {
    /// `multiplier`: the value in force before the most recent activation.
    pub multiplier: f64,
    /// `new_multiplier_effective_timestamp`: when `new_multiplier` takes over.
    pub new_multiplier_effective_timestamp: i64,
    /// `new_multiplier`: the value that takes over at that timestamp.
    pub new_multiplier: f64,
}

impl ScaledUiAmount {
    /// The multiplier actually in force at `now`.
    ///
    /// This is the whole point of the program. The field named `multiplier` is the
    /// **previous** value; the live one is `new_multiplier`, once its timestamp has passed.
    /// A reader that takes `multiplier` is one corporate action behind. On 17 September 2026
    /// that was 370 of the 927 xStock mints on Solana.
    ///
    /// The timestamp decides, and nothing else does, because that is what Token-2022 does.
    /// `ScaledUiAmountConfig::current_multiplier` in
    /// `interface/src/extension/scaled_ui_amount/mod.rs` is `if unix_timestamp >=
    /// new_multiplier_effective_timestamp { new_multiplier } else { multiplier }`, with no test
    /// on the value. This function used to add `new_multiplier != 0.0`, which reads as defensive
    /// and is not: on an account whose `new_multiplier` is zero and whose timestamp has passed,
    /// the token program scales the position by zero while this program would have reported the
    /// full `multiplier`. That is an overstated holding, which is the wrong direction to be
    /// wrong in for a program a venue settles against. Zero is now caught by `to_fixed_point`,
    /// so the crafted account fails closed instead of reading as full value.
    pub fn effective(&self, now: i64) -> f64 {
        if self.new_multiplier_effective_timestamp <= now {
            self.new_multiplier
        } else {
            self.multiplier
        }
    }

    /// When the multiplier in force at `now` took effect, or 0 if the mint has never had a
    /// dated activation.
    pub fn effective_at(&self, now: i64) -> i64 {
        if self.new_multiplier_effective_timestamp <= now {
            self.new_multiplier_effective_timestamp
        } else {
            0
        }
    }

    /// The multiplier in force at `now`, as 1e18 fixed point.
    ///
    /// The mint stores an f64. Converted once, here, and never touched as a float again:
    /// Solana's own docs say the scaled-ui-amount helpers use floating point and are not
    /// guaranteed to round-trip, so every downstream calculation is integer.
    pub fn effective_fixed(&self, now: i64) -> Result<u128> {
        to_fixed_point(self.effective(now))
    }
}

/// An f64 multiplier as 1e18 fixed point, rejecting anything not usable as a price ratio.
///
/// The test is `is_normal()`, not `is_finite()`, and that is the token program's own rule rather
/// than a stricter one invented here. `try_validate_multiplier` in Token-2022's scaled-ui-amount
/// processor requires `is_sign_positive() && is_normal()` before it will write this field, so
/// zero, a subnormal, a negative, an infinity and a NaN are all values the extension cannot
/// legitimately hold. `is_normal()` is true of a negative normal, so the sign test beside it is
/// not redundant.
///
/// The truncation check at the end is the second half of the same idea. By then `scaled` is
/// finite and positive, but a multiplier below 1e-18 still converts to a fixed point of zero,
/// which downstream reads as a position worth nothing. Returning that zero would be a wrong
/// number of the right shape, which is the defect this program exists to report.
pub fn to_fixed_point(x: f64) -> Result<u128> {
    if !x.is_normal() || x <= 0.0 {
        return Err(RecordDateError::BadMultiplier.into());
    }
    let scaled = x * 1e18;
    if scaled >= u128::MAX as f64 {
        return Err(RecordDateError::BadMultiplier.into());
    }
    if scaled < 1.0 {
        return Err(RecordDateError::BadMultiplier.into());
    }
    Ok(scaled as u128)
}

/// `raw` units of a mint, expressed in the units a wallet should display.
///
/// This is `raw * multiplier`, done in integers.
pub fn scaled_amount(raw: u64, multiplier_fp: u128) -> Result<u128> {
    (raw as u128)
        .checked_mul(multiplier_fp)
        .map(|v| v / crate::constants::FP_SCALE)
        .ok_or_else(|| RecordDateError::BadMultiplier.into())
}

/// A mint account as this program needs it.
#[derive(Debug, Clone, Copy)]
pub struct MintView {
    pub supply: u64,
    pub decimals: u8,
    pub scaled_ui_amount: ScaledUiAmount,
}

/// Find the `ScaledUiAmountConfig` extension body and return its offset.
///
/// The layout is not what the Token-2022 docs suggest. A mint account is a 165-byte base region,
/// then the account-type byte, then a run of `(type: u16, length: u16, body: [u8; length])`
/// entries. The mint itself occupies only the first 82 bytes of that region. Walking the
/// extensions from byte 83, as the documented layout reads, therefore lands in 83 bytes of
/// padding and finds nothing.
///
/// The base region is a fixed 165 bytes, not 82 plus an account-type byte, because the program
/// only writes TLV data after `Account::LEN` so that a mint and a token account cannot be
/// confused. `interface/src/extension/mod.rs` in the Token-2022 source says so, in the comment
/// above `BASE_ACCOUNT_AND_TYPE_LENGTH`, and does the same check on the padding that this does.
///
/// The walk is exact. It steps from one header to the next and never skips a byte looking for
/// something that resembles a header. That is a decision, not an omission: on all 927 xStock
/// mints the run is dense and ends exactly at the end of the account, so there is nothing to
/// skip over. A tolerant walk that advanced a byte whenever a header looked implausible would be
/// a way to return a *different* plausible-looking body without failing, which is worse than
/// failing. `scripts/fetch.py` measures the density and fails if it ever stops holding.
pub fn find_scaled_ui_amount(data: &[u8]) -> Result<usize> {
    // A mint with no extensions is exactly the 82-byte base state and carries no account-type
    // byte at all, so there is nothing to find and no error to report.
    if data.len() <= MINT_BASE_LEN {
        return Err(RecordDateError::NoScaledUiAmount.into());
    }
    if data.len() < TLV_START {
        return Err(RecordDateError::MintTooShort.into());
    }
    // The padding is checked rather than skipped. A non-zero byte here means the account is not
    // laid out the way this parser assumes, and every offset after it would be wrong. This is
    // the check that can fail for the reason it claims: it is the structural claim itself.
    if data[MINT_BASE_LEN..BASE_REGION_LEN].iter().any(|&b| b != 0) {
        return Err(RecordDateError::MintPaddingNotZero.into());
    }
    if data[BASE_REGION_LEN] != ACCOUNT_TYPE_MINT {
        return Err(RecordDateError::NotAMint.into());
    }

    let mut off = TLV_START;
    while off + 4 <= data.len() {
        let ext_type = u16::from_le_bytes([data[off], data[off + 1]]);
        let ext_len = u16::from_le_bytes([data[off + 2], data[off + 3]]);

        // `ExtensionType::Uninitialized`. The initialised run has ended, so any extension after
        // this point is not there.
        if ext_type == 0 {
            return Err(RecordDateError::NoScaledUiAmount.into());
        }
        if ext_type > MAX_EXTENSION_TYPE {
            return Err(RecordDateError::UnknownExtension.into());
        }
        // Every entry has to fit inside the account. This is the bound that matters, and it is
        // derived from the account rather than chosen: the largest body the walk steps over on
        // any live xStock mint is 205 bytes, but a variable-length `TokenMetadata` entry could
        // legitimately be larger than any constant picked here.
        //
        // The order here is the point, and it was wrong. This check used to run *after* the
        // `ScaledUiAmountConfig` branch below, which returns. The loop condition is only
        // `off + 4 <= data.len()`, so a header at the very end of the account declaring a 56-byte
        // body passed the loop condition, matched the type, matched the length, and returned an
        // offset that `read_mint` then sliced past the end of the buffer. Slicing out of bounds
        // panics; it does not return an error. A crafted mint account could do it, and the one
        // entry the walk was asked to find was the one entry it never bounds-checked.
        if off + 4 + ext_len as usize > data.len() {
            return Err(RecordDateError::ExtensionOverrunsAccount.into());
        }
        if ext_type == EXT_SCALED_UI_AMOUNT {
            if ext_len as usize != SCALED_UI_AMOUNT_LEN {
                return Err(RecordDateError::BadExtensionLength.into());
            }
            return Ok(off + 4);
        }
        off += 4 + ext_len as usize;
    }
    Err(RecordDateError::NoScaledUiAmount.into())
}

/// Parse a mint account.
pub fn read_mint(data: &[u8]) -> Result<MintView> {
    if data.len() < MINT_BASE_LEN {
        return Err(RecordDateError::MintTooShort.into());
    }
    let supply = u64::from_le_bytes(data[36..44].try_into().unwrap());
    let decimals = data[44];

    let body = find_scaled_ui_amount(data)?;
    let body = &data[body..body + SCALED_UI_AMOUNT_LEN];

    Ok(MintView {
        supply,
        decimals,
        scaled_ui_amount: ScaledUiAmount {
            multiplier: f64::from_le_bytes(
                body[OFF_MULTIPLIER..OFF_MULTIPLIER + 8].try_into().unwrap(),
            ),
            new_multiplier_effective_timestamp: i64::from_le_bytes(
                body[OFF_EFFECTIVE_TS..OFF_EFFECTIVE_TS + 8].try_into().unwrap(),
            ),
            new_multiplier: f64::from_le_bytes(
                body[OFF_NEW_MULTIPLIER..OFF_NEW_MULTIPLIER + 8].try_into().unwrap(),
            ),
        },
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A mint account built the way the chain builds one: the 82-byte base state, 83 bytes of
    /// padding to fill out the 165-byte base region, the account-type byte, then
    /// `(type, length, body)` entries. Returns (bytes, offset of the scaled-ui body).
    fn synth_mint(multiplier: f64, ts: i64, new_multiplier: f64) -> (Vec<u8>, usize) {
        let mut d = vec![0u8; TLV_START];
        d[44] = 8;
        d[36..44].copy_from_slice(&123u64.to_le_bytes());
        d[BASE_REGION_LEN] = ACCOUNT_TYPE_MINT;
        // MetadataPointer, body 64 zeros
        d.extend_from_slice(&18u16.to_le_bytes());
        d.extend_from_slice(&64u16.to_le_bytes());
        d.extend(std::iter::repeat_n(0u8, 64));
        // ScaledUiAmountConfig
        let hdr = d.len();
        d.extend_from_slice(&EXT_SCALED_UI_AMOUNT.to_le_bytes());
        d.extend_from_slice(&(SCALED_UI_AMOUNT_LEN as u16).to_le_bytes());
        let mut body = vec![0u8; SCALED_UI_AMOUNT_LEN];
        body[OFF_MULTIPLIER..OFF_MULTIPLIER + 8].copy_from_slice(&multiplier.to_le_bytes());
        body[OFF_EFFECTIVE_TS..OFF_EFFECTIVE_TS + 8].copy_from_slice(&ts.to_le_bytes());
        body[OFF_NEW_MULTIPLIER..OFF_NEW_MULTIPLIER + 8]
            .copy_from_slice(&new_multiplier.to_le_bytes());
        d.extend_from_slice(&body);
        (d, hdr + 4)
    }

    #[test]
    fn finds_the_extension_after_the_base_region_not_after_the_base_state() {
        let (d, expect) = synth_mint(1.0, 0, 0.0);

        // The constants are the claim: the base region is 165 bytes, so the first extension
        // header is at 166. Reading the documented layout as "82-byte base, then extensions"
        // would put it at 83, which is 83 bytes early and lands in padding.
        assert_eq!(BASE_REGION_LEN, 165);
        assert_eq!(TLV_START, 166);

        // The first header really is at 166, and the account-type byte really is at 165.
        let first_type = u16::from_le_bytes([d[TLV_START], d[TLV_START + 1]]);
        assert_eq!(first_type, 18, "the MetadataPointer header is first");
        assert_eq!(d[BASE_REGION_LEN], ACCOUNT_TYPE_MINT);

        // And the parser returns the body of the second extension, after the 64-byte pointer.
        assert_eq!(expect, TLV_START + 4 + 64 + 4);
        assert_eq!(find_scaled_ui_amount(&d).unwrap(), expect);
    }

    #[test]
    fn a_mint_with_no_multiplier_is_an_error_not_a_one() {
        let mut d = vec![0u8; TLV_START];
        d[BASE_REGION_LEN] = ACCOUNT_TYPE_MINT;
        assert!(find_scaled_ui_amount(&d).is_err());
    }

    #[test]
    fn a_mint_with_no_extensions_at_all_is_not_a_scaled_mint() {
        // A mint with no extensions is exactly the 82-byte base state.
        assert!(find_scaled_ui_amount(&[0u8; MINT_BASE_LEN]).is_err());
    }

    #[test]
    fn non_zero_padding_is_rejected_rather_than_skipped() {
        let (mut d, _) = synth_mint(1.0, 0, 0.0);
        // A single stray byte in the 83-byte gap means the account is not laid out as assumed,
        // so every offset after it is suspect. Scanning past it would silently produce a
        // plausible-looking wrong multiplier, which is the failure this program exists to stop.
        d[MINT_BASE_LEN + 40] = 7;
        assert!(find_scaled_ui_amount(&d).is_err());
    }

    #[test]
    fn an_account_type_that_is_not_a_mint_is_rejected() {
        let (mut d, _) = synth_mint(1.0, 0, 0.0);
        d[BASE_REGION_LEN] = 2; // AccountType::Account
        assert!(find_scaled_ui_amount(&d).is_err());
    }

    #[test]
    fn an_entry_that_claims_a_body_past_the_end_of_the_account_is_rejected() {
        let mut d = vec![0u8; TLV_START];
        d[BASE_REGION_LEN] = ACCOUNT_TYPE_MINT;
        d.extend_from_slice(&18u16.to_le_bytes()); // MetadataPointer
        d.extend_from_slice(&500u16.to_le_bytes()); // a body the account does not contain
        d.extend(std::iter::repeat_n(0u8, 8));
        assert!(find_scaled_ui_amount(&d).is_err());
    }

    #[test]
    fn an_entry_that_fits_its_header_but_not_its_body_is_refused_not_sliced() {
        // The header fits, because the loop condition is only `off + 4 <= data.len()`. The body
        // does not: 8 bytes are present where the header promised 56.
        //
        // This is the negative control for the ordering of the bounds check. With the check below
        // the returning branch, this walk returned an offset 48 bytes past the end of the account
        // and `read_mint` sliced `data[body..body + 56]` on a buffer that did not contain it,
        // which panics rather than returning an error. A test that panics is a test that fails,
        // so this test failing is exactly what the defect looked like.
        let mut d = vec![0u8; TLV_START];
        d[BASE_REGION_LEN] = ACCOUNT_TYPE_MINT;
        d.extend_from_slice(&EXT_SCALED_UI_AMOUNT.to_le_bytes());
        d.extend_from_slice(&(SCALED_UI_AMOUNT_LEN as u16).to_le_bytes());
        d.extend(std::iter::repeat_n(0u8, 8));

        let err = read_mint(&d).expect_err("a truncated extension body must not be read");
        assert!(
            err.to_string().contains("does not fit inside the account"),
            "expected the overrun error, got: {err}"
        );
        // And the walk alone refuses it too, so the refusal is not coming from somewhere later.
        assert!(find_scaled_ui_amount(&d).is_err());
    }

    #[test]
    fn an_uninitialised_entry_ends_the_run_rather_than_being_skipped() {
        // Type 0 is `ExtensionType::Uninitialized`. Nothing after it is initialised, so a
        // scaled-ui-amount entry placed behind a zero header must not be found. This is the test
        // that separates the exact walk from a tolerant one: a walk that advanced a byte whenever
        // a header looked implausible would step over the terminator and return the body below.
        let (good, body) = synth_mint(1.0, 0, 0.0);
        assert_eq!(find_scaled_ui_amount(&good).unwrap(), body);

        let mut d = vec![0u8; TLV_START];
        d[BASE_REGION_LEN] = ACCOUNT_TYPE_MINT;
        d.extend_from_slice(&0u16.to_le_bytes());
        d.extend_from_slice(&0u16.to_le_bytes());
        d.extend_from_slice(&good[TLV_START..]);
        assert!(find_scaled_ui_amount(&d).is_err());
    }

    #[test]
    fn the_walk_claims_every_byte_of_the_account() {
        let (d, body) = synth_mint(1.0, 0, 0.0);
        assert_eq!(find_scaled_ui_amount(&d).unwrap(), body);
        // MetadataPointer is 4 + 64, ScaledUiAmountConfig is 4 + 56, and that is the whole
        // account. Nothing is unclaimed, which is what makes the exact walk safe.
        assert_eq!(
            TLV_START + (4 + 64) + (4 + SCALED_UI_AMOUNT_LEN),
            d.len()
        );
    }

    #[test]
    fn reads_supply_decimals_and_multiplier() {
        let (d, _) = synth_mint(1.0268028384810615, 1_788_481_800, 1.0344000941634355);
        let m = read_mint(&d).unwrap();
        assert_eq!(m.supply, 123);
        assert_eq!(m.decimals, 8);
        assert!((m.scaled_ui_amount.multiplier - 1.0268028384810615).abs() < 1e-15);
        assert!((m.scaled_ui_amount.new_multiplier - 1.0344000941634355).abs() < 1e-15);
    }

    #[test]
    fn effective_multiplier_prefers_the_new_value_once_its_timestamp_has_passed() {
        let s = ScaledUiAmount {
            multiplier: 1.0268028384810615,
            new_multiplier_effective_timestamp: 1_788_481_800,
            new_multiplier: 1.0344000941634355,
        };
        // one second before activation the old value is in force
        assert_eq!(s.effective(1_788_481_799), 1.0268028384810615);
        // at the timestamp the new value is
        assert_eq!(s.effective(1_788_481_800), 1.0344000941634355);
        // and it stays
        assert_eq!(s.effective(i64::MAX), 1.0344000941634355);
    }

    #[test]
    fn scaled_amount_is_integer_arithmetic() {
        // 1_000_000 raw at multiplier 1.0344000941634355 -> 1_034_400 scaled, less the
        // float tail that integer division drops on purpose
        let fp = to_fixed_point(1.0344000941634355).unwrap();
        assert_eq!(scaled_amount(1_000_000, fp).unwrap(), 1_034_400);
        // a 10-for-1 split, the shape KLACx actually took
        let fp = to_fixed_point(10.00892302917).unwrap();
        assert_eq!(scaled_amount(4_000, fp).unwrap(), 40_035);
    }

    #[test]
    fn rejects_multipliers_that_are_not_usable() {
        assert!(to_fixed_point(0.0).is_err());
        assert!(to_fixed_point(-0.0).is_err());
        assert!(to_fixed_point(-1.0).is_err());
        assert!(to_fixed_point(f64::NAN).is_err());
        assert!(to_fixed_point(f64::INFINITY).is_err());
        assert!(to_fixed_point(f64::NEG_INFINITY).is_err());
        // A subnormal is positive and finite and still not a value this extension can hold:
        // Token-2022's `try_validate_multiplier` requires `is_normal()` before it will write one,
        // so this is the token program's rule rather than a stricter one invented here.
        assert!(to_fixed_point(f64::MIN_POSITIVE / 2.0).is_err());
        // A positive normal that is too small to survive the conversion. 1e-19 times 1e18 is 0.1,
        // which truncates to a fixed point of zero, and a fixed point of zero reads downstream as
        // a position worth nothing. Returning it would be a wrong number of the right shape.
        assert!(to_fixed_point(1e-19).is_err());
        // The smallest value that does survive, and the largest that is ordinary, so the two
        // bounds are where this test claims they are rather than merely present.
        assert_eq!(to_fixed_point(1e-18).unwrap(), 1);
        assert_eq!(to_fixed_point(1.0).unwrap(), 1_000_000_000_000_000_000);
    }

    #[test]
    fn effective_multiplier_follows_the_timestamp_and_not_the_value() {
        // This is the test that separates this program's rule from a defensive one. Token-2022's
        // `current_multiplier` tests the timestamp alone, so a stored `new_multiplier` of zero
        // whose timestamp has passed means the token program scales the position by zero. An
        // earlier version of `effective` also required `new_multiplier != 0.0`, which reported the
        // full `multiplier` for that account instead: an overstated holding, and a disagreement
        // with the program this one is supposed to be reporting.
        let s = ScaledUiAmount {
            multiplier: 1.5,
            new_multiplier_effective_timestamp: 100,
            new_multiplier: 0.0,
        };
        // Before the timestamp, the previous value is in force either way.
        assert_eq!(s.effective(99), 1.5);
        // At and after it, the stored new value is, even when it is zero.
        assert_eq!(s.effective(100), 0.0);
        assert_eq!(s.effective(i64::MAX), 0.0);
        // And zero is then refused rather than scaled, so the account fails closed.
        assert!(s.effective_fixed(100).is_err());

        // The ordinary case is unchanged: a real activation, whose value the token program's own
        // validation guarantees is a positive normal.
        let real = ScaledUiAmount {
            multiplier: 1.0268028384810615,
            new_multiplier_effective_timestamp: 1_788_481_800,
            new_multiplier: 1.0344000941634355,
        };
        assert_eq!(real.effective(1_788_481_799), 1.0268028384810615);
        assert_eq!(real.effective(1_788_481_800), 1.0344000941634355);
        assert_eq!(real.effective_at(1_788_481_799), 0);
        assert_eq!(real.effective_at(1_788_481_800), 1_788_481_800);

        // A mint that has never had an activation carries `new_multiplier` equal to `multiplier`
        // with a timestamp of zero, which is what Token-2022's initialiser writes. The timestamp
        // has always passed, so the new value is returned and it is the same number.
        let fresh = ScaledUiAmount {
            multiplier: 1.0,
            new_multiplier_effective_timestamp: 0,
            new_multiplier: 1.0,
        };
        assert_eq!(fresh.effective(0), 1.0);
        assert_eq!(fresh.effective_at(0), 0);
    }
}
