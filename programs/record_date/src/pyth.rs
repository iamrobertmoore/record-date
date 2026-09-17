//! Reading a Pyth `PriceUpdateV2` account.
//!
//! The point of this module is the one thing the rest of the program cannot do for itself: an
//! **independent** price. Everything else here reads the mint, and the mint is the issuer's own
//! account. A Pyth price is a third party's number for the same equity, so comparing the two is a
//! check on the issuer rather than a restatement of it.
//!
//! The layout below is not taken from documentation. It was derived from a real account read off
//! Solana mainnet and then asserted: the discriminator is checked against
//! `sha256("account:PriceUpdateV2")`, and the field offsets were confirmed by decoding an account
//! whose price and publish time had to be plausible for AAPL. `scripts/capture_pyth_fixture.py`
//! captures such an account, and `tests/fixtures/pyth_aapl_mainnet.bin` is one, so the parser in
//! this file is tested against bytes nobody wrote by hand.
//!
//! Two details that a parser written from a summary would get wrong:
//!
//! * The account is **134 bytes and the fields occupy 133**. There is one trailing byte after
//!   `posted_slot` whose purpose has not been established. It is tolerated and not named, rather
//!   than assigned a meaning it has not been shown to have.
//! * `exponent` is a signed field and is **negative** for equity feeds, so the human price is
//!   `price * 10^exponent` and the fixed-point conversion is a multiply or a divide depending on
//!   its sign. Treating it as unsigned turns $334.25 into a number with thirty digits.

use crate::error::RecordDateError;
use anchor_lang::prelude::*;

/// The Pyth Solana receiver program. Every `PriceUpdateV2` account is owned by it, on every
/// cluster it is deployed to, which is why the ownership check is the first thing `read` does not
/// have to do: the caller does it against the account's owner.
pub const PYTH_RECEIVER_PROGRAM_ID: Pubkey = Pubkey::new_from_array([
    12, 183, 250, 187, 82, 247, 166, 72, 187, 91, 49, 125, 154, 1, 139, 144, 87, 203, 2, 71, 116,
    250, 254, 1, 230, 196, 223, 152, 204, 56, 88, 129,
]);

/// The Anchor discriminator for `PriceUpdateV2`: the first eight bytes of
/// `sha256("account:PriceUpdateV2")`.
///
/// Written out rather than computed, because a `const` cannot call sha256. The test
/// `the_discriminator_is_what_anchor_would_compute` recomputes it and asserts this equals it, so
/// the constant is checked rather than trusted.
pub const DISCRIMINATOR: [u8; 8] = [0x22, 0xf1, 0x23, 0x63, 0x9d, 0x7e, 0xf4, 0xcd];

const WRITE_AUTHORITY_OFFSET: usize = 8;
const VERIFICATION_LEVEL_OFFSET: usize = 40;
const FEED_ID_OFFSET: usize = 41;
const PRICE_OFFSET: usize = 73;
const CONF_OFFSET: usize = 81;
const EXPONENT_OFFSET: usize = 89;
const PUBLISH_TIME_OFFSET: usize = 93;
const PREV_PUBLISH_TIME_OFFSET: usize = 101;
const EMA_PRICE_OFFSET: usize = 109;
const EMA_CONF_OFFSET: usize = 117;
const POSTED_SLOT_OFFSET: usize = 125;

/// The last byte the parsed fields reach. Accounts are 134 bytes; the byte at 133 is unexplained.
pub const PARSED_LEN: usize = 133;

/// The longest age a caller may declare acceptable: one day.
///
/// The program bounds the caller rather than dictating to it. How fresh a price has to be is a
/// policy about the caller's own settlement, and the right answer differs by caller: a venue
/// settling inside the session might want a minute, and one settling a weekend corporate action has
/// to accept Friday's close, because no equity prints on a Saturday and so the price cannot move.
/// A program has no basis to know which case it is looking at, so it enforces a ceiling and lets
/// the caller choose below it. The ceiling is what keeps the check meaningful.
pub const MAX_PRICE_AGE_CEILING_SECS: i64 = 86_400;

/// What a caller should pass while the underlying market is open.
///
/// Measured rather than assumed, because the answer decides whether the instruction is usable at
/// all. Pyth's Solana pusher updates the live AAPL account continuously: sampled ten times over
/// three minutes on 17 September 2026, the account advanced its `publish_time` nine times and the
/// freshest price was never more than 24 seconds old. Those samples were taken between 06:50 and
/// 06:53 ET, with the US session still shut until 09:30, so the feed does not go quiet outside the
/// session. Five minutes is roughly fifteen times the observed interval.
///
/// **Weekend and holiday behaviour is not measured.** No equity prints then, so the price cannot
/// change, and whether the pusher still writes a fresh `publish_time` is untested here. That is
/// exactly why this is a recommendation and not an enforced value.
pub const RECOMMENDED_PRICE_AGE_SECS: i64 = 300;

/// How far ahead of the local clock a publish time may be. A price from the future is a
/// misconfiguration or a forged account, not a price, and unlike the age this is not the caller's
/// to relax.
pub const MAX_CLOCK_SKEW_SECS: i64 = 60;

/// Basis points, for the tolerance argument.
pub const BPS_DENOMINATOR: u128 = 10_000;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PriceUpdateV2 {
    pub write_authority: [u8; 32],
    /// 0 = Partial, 1 = Full. Recorded, not enforced: what matters is the price and its age.
    pub verification_level: u8,
    pub feed_id: [u8; 32],
    pub price: i64,
    pub conf: u64,
    pub exponent: i32,
    pub publish_time: i64,
    pub prev_publish_time: i64,
    pub ema_price: i64,
    pub ema_conf: u64,
    pub posted_slot: u64,
}

impl PriceUpdateV2 {
    /// The price as 1e18 fixed point, which is the unit the rest of this program uses.
    pub fn price_fp(&self) -> Result<u128> {
        to_fixed_point(self.price, self.exponent)
    }

    /// Seconds between the price and `now`. Negative means the price is from the future.
    pub fn age(&self, now: i64) -> i64 {
        now - self.publish_time
    }

    /// True when the price is older than `max_age_secs`, or ahead of the local clock.
    ///
    /// The two halves are not the same kind of rule. The age is the caller's to set, within the
    /// ceiling `verify_against_pyth` enforces. The future check is not: a price from the future is
    /// wrong at every tolerance, so it is fixed here.
    pub fn is_stale(&self, now: i64, max_age_secs: i64) -> bool {
        let age = self.age(now);
        age > max_age_secs || age < -MAX_CLOCK_SKEW_SECS
    }

    /// The full-width form of the price, for a human reading a log line.
    pub fn price_at_exponent(&self) -> f64 {
        (self.price as f64) * 10f64.powi(self.exponent)
    }
}

/// Parse a `PriceUpdateV2` account, or say why it is not one.
///
/// Length is checked before the discriminator and both before any field is read. A caller can
/// pass an account that merely has the right bytes at the right offset: a `memcmp` filter on a
/// cluster returns candidates that are not this type at all, which is how a devnet query for
/// BTC/USD hit an account too short to parse.
pub fn read(data: &[u8]) -> Result<PriceUpdateV2> {
    require!(
        data.len() >= PARSED_LEN,
        RecordDateError::PriceUpdateTooShort
    );
    require!(
        data[..DISCRIMINATOR.len()] == DISCRIMINATOR,
        RecordDateError::NotAPriceUpdate
    );

    let mut write_authority = [0u8; 32];
    write_authority.copy_from_slice(&data[WRITE_AUTHORITY_OFFSET..WRITE_AUTHORITY_OFFSET + 32]);

    let mut feed_id = [0u8; 32];
    feed_id.copy_from_slice(&data[FEED_ID_OFFSET..FEED_ID_OFFSET + 32]);

    Ok(PriceUpdateV2 {
        write_authority,
        verification_level: data[VERIFICATION_LEVEL_OFFSET],
        feed_id,
        price: i64_at(data, PRICE_OFFSET),
        conf: u64_at(data, CONF_OFFSET),
        exponent: i32_at(data, EXPONENT_OFFSET),
        publish_time: i64_at(data, PUBLISH_TIME_OFFSET),
        prev_publish_time: i64_at(data, PREV_PUBLISH_TIME_OFFSET),
        ema_price: i64_at(data, EMA_PRICE_OFFSET),
        ema_conf: u64_at(data, EMA_CONF_OFFSET),
        posted_slot: u64_at(data, POSTED_SLOT_OFFSET),
    })
}

/// `price * 10^exponent`, as 1e18 fixed point.
///
/// `exponent` is negative on every equity feed Pyth publishes, so this is usually a division by a
/// power of ten. It is written as a signed shift rather than a fixed divide because the sign is a
/// property of the feed and not of the format.
pub fn to_fixed_point(price: i64, exponent: i32) -> Result<u128> {
    require!(price >= 0, RecordDateError::NegativePythPrice);
    let magnitude = price as u128;
    let shift = 18i32.saturating_add(exponent);

    if shift >= 0 {
        let scale = 10u128
            .checked_pow(shift as u32)
            .ok_or_else(|| error!(RecordDateError::PriceOutOfRange))?;
        magnitude
            .checked_mul(scale)
            .ok_or_else(|| error!(RecordDateError::PriceOutOfRange))
    } else {
        let scale = 10u128
            .checked_pow(shift.unsigned_abs())
            .ok_or_else(|| error!(RecordDateError::PriceOutOfRange))?;
        Ok(magnitude / scale)
    }
}

/// The gap between two fixed-point prices, in basis points of the larger.
///
/// Relative to the larger rather than the first, so the answer does not depend on which side is
/// called the reference.
pub fn deviation_bps(a: u128, b: u128) -> u128 {
    let (high, low) = if a >= b { (a, b) } else { (b, a) };
    if high == 0 {
        return 0;
    }
    (high - low).saturating_mul(BPS_DENOMINATOR) / high
}

fn i64_at(data: &[u8], offset: usize) -> i64 {
    let mut buf = [0u8; 8];
    buf.copy_from_slice(&data[offset..offset + 8]);
    i64::from_le_bytes(buf)
}

fn u64_at(data: &[u8], offset: usize) -> u64 {
    let mut buf = [0u8; 8];
    buf.copy_from_slice(&data[offset..offset + 8]);
    u64::from_le_bytes(buf)
}

fn i32_at(data: &[u8], offset: usize) -> i32 {
    let mut buf = [0u8; 4];
    buf.copy_from_slice(&data[offset..offset + 4]);
    i32::from_le_bytes(buf)
}
