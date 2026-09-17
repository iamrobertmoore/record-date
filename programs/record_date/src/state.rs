use crate::constants::SYMBOL_MAX;
use anchor_lang::prelude::*;

/// One per deployment. Holds the authority and the counters.
#[account]
#[derive(InitSpace)]
pub struct Registry {
    pub authority: Pubkey,
    /// Mints registered.
    pub mints: u64,
    /// Activations recorded, across every mint.
    pub activations: u64,
    pub bump: u8,
}

/// One per registered mint. The last state this program saw, and the counters.
#[account]
#[derive(InitSpace)]
pub struct TokenRecord {
    pub mint: Pubkey,
    #[max_len(SYMBOL_MAX)]
    pub symbol: [u8; SYMBOL_MAX],
    pub symbol_len: u8,
    pub decimals: u8,

    /// The raw f64 bits of the field the mint calls `multiplier`. Kept so the trap is
    /// auditable: this is the value a naive reader would take, and it is the previous one.
    pub base_multiplier_bits: u64,

    /// The raw f64 bits of the multiplier actually in force.
    pub live_multiplier_bits: u64,
    /// The same value as 1e18 fixed point, which is what everything downstream uses.
    pub live_multiplier_fp: u128,
    /// When the live multiplier took effect.
    pub live_effective_at: i64,

    /// How many activations this program has recorded for this mint.
    pub activations: u64,
    /// The slot of the last read.
    pub last_seen_slot: u64,
    pub bump: u8,
}

/// One per activation, ever. Written by `record_activation` and never changed after.
///
/// This is the object the whole program exists to produce: a corporate action on an xStock
/// as a permanent, ordered account that any program can read in one call, instead of an
/// HTTP API and a float in a mint.
#[account]
#[derive(InitSpace)]
pub struct Receipt {
    pub mint: Pubkey,
    /// 0 for the first activation this program saw, then 1, 2, ...
    pub sequence: u64,

    /// The multiplier before, as the mint stored it.
    pub previous_bits: u64,
    /// The multiplier after, as the mint stored it.
    pub new_bits: u64,
    /// The multiplier before, as 1e18 fixed point.
    pub previous_fp: u128,
    /// The multiplier after, as 1e18 fixed point.
    pub new_fp: u128,

    /// When the change took effect, from the mint's own timestamp.
    pub effective_at: i64,
    /// When this program wrote the receipt.
    pub recorded_at: i64,
    /// The mint's raw supply at the moment of recording.
    pub raw_supply: u64,
    pub bump: u8,
}

impl Receipt {
    /// The change in the multiplier, in 1e18 fixed point. This is the reinvested dividend and
    /// the split factor, in one number, taken from the chain rather than from a feed.
    pub fn delta_fp(&self) -> u128 {
        self.new_fp.saturating_sub(self.previous_fp)
    }

    /// True when the change did not move the multiplier, which is not an activation.
    pub fn is_flat(&self) -> bool {
        self.new_fp == self.previous_fp
    }
}

/// One per mint that has been bound to a Pyth feed.
///
/// The binding is what makes `verify_against_pyth` more than a price lookup. Without it the
/// instruction would accept any feed the caller passed, including the feed for a different
/// equity, so the check would be "is there a fresh Pyth price anywhere" rather than "is there a
/// fresh Pyth price for *this* stock". The feed id is stored, not the account address, because a
/// feed is republished into a new account on every update and only the id is stable.
#[account]
#[derive(InitSpace)]
pub struct PythBinding {
    pub mint: Pubkey,
    pub feed_id: [u8; 32],
    /// Pyth's own ticker for the feed, for a human reading the account.
    #[max_len(SYMBOL_MAX)]
    pub feed_symbol: [u8; SYMBOL_MAX],
    pub feed_symbol_len: u8,
    /// When the binding was written.
    pub bound_at: i64,
    pub bump: u8,
}
