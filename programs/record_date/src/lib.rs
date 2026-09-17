//! Record Date: a tokenized stock's dividend entitlement, on chain.
//!
//! On Solana an xStock's dividend is reinvested by raising a multiplier stored in the
//! Token-2022 mint account. The balance does not change, so the multiplier is the only
//! record that a payment happened at all, and every integrator is individually responsible
//! for applying it. Two things make that harder than it reads:
//!
//! 1. The field named `multiplier` is the **previous** value. The live one is
//!    `new_multiplier`, and it takes over at `new_multiplier_effective_timestamp`.
//!    On 17 September 2026, 370 of the 927 xStock mints on Solana differed between the two.
//! 2. There is no account that records the sequence of activations, so the history lives in
//!    an HTTP API and in transaction logs.
//!
//! This program reads the mint directly and writes each activation as a `Receipt`: an
//! ordered, permanent, tamper-evident account holding the multiplier before and after, the
//! timestamp the issuer set, and the supply at the time. Any program can then read a token's
//! corporate-action history in one call.

pub mod constants;
pub mod error;
pub mod instructions;
pub mod pyth;
pub mod state;
pub mod token2022;

use anchor_lang::prelude::*;

pub use constants::*;
pub use instructions::*;
pub use state::*;

// Must equal the public key of target/deploy/record_date-keypair.json, or Anchor returns
// DeclaredProgramIdMismatch on every instruction and the deploy looks like a success. Check it
// with: solana-keygen pubkey target/deploy/record_date-keypair.json
declare_id!("ycg2obpKmccwAz1zGf4QqgV2vnkWd6CQKLdJSGDxtmG");

#[program]
pub mod record_date {
    use super::*;

    /// Create the registry. Once per deployment.
    pub fn init_registry(ctx: Context<InitRegistry>) -> Result<()> {
        crate::instructions::init_registry::handle_init_registry(ctx)
    }

    /// Register an xStock mint. Reads the mint, records the live multiplier and the field the
    /// mint calls `multiplier`, so the difference between them is auditable later.
    pub fn register_mint(ctx: Context<RegisterMint>, symbol: String) -> Result<()> {
        crate::instructions::register_mint::handle_register_mint(ctx, symbol)
    }

    /// Record a corporate action. Permissionless.
    pub fn record_activation(ctx: Context<RecordActivation>) -> Result<()> {
        crate::instructions::record_activation::handle_record_activation(ctx)
    }

    /// `raw` units in the units a wallet should display. Return data: little-endian u128.
    pub fn read_entitlement(ctx: Context<ReadMint>, raw_amount: u64) -> Result<()> {
        crate::instructions::read::handle_read_entitlement(ctx, raw_amount)
    }

    /// Is there an activation this program has not recorded yet? Return data: 1 or 0.
    pub fn activation_pending(ctx: Context<ReadMint>) -> Result<()> {
        crate::instructions::read::handle_activation_pending(ctx)
    }

    /// Seconds since the last activation. Return data: little-endian i64.
    pub fn settlement_window(ctx: Context<ReadMint>) -> Result<()> {
        crate::instructions::read::handle_settlement_window(ctx)
    }

    /// Bind a registered mint to a Pyth price feed. Additive: it writes a new account and does
    /// not change the layout of any existing one, so receipts already on chain keep reading.
    pub fn bind_pyth_feed(
        ctx: Context<BindPythFeed>,
        feed_id: [u8; 32],
        feed_symbol: String,
    ) -> Result<()> {
        crate::instructions::bind_pyth_feed::handle_bind_pyth_feed(ctx, feed_id, feed_symbol)
    }

    /// Check a price against Pyth's own, refusing a stale price or the wrong feed.
    ///
    /// `max_age_secs` is the caller's own freshness policy, which is why it is an argument and not
    /// a constant here. It is bounded by `pyth::MAX_PRICE_AGE_CEILING_SECS`.
    ///
    /// Return data: the Pyth price as 1e18 fixed point (16 bytes LE), then the deviation in basis
    /// points (16 bytes LE).
    pub fn verify_against_pyth(
        ctx: Context<VerifyAgainstPyth>,
        expected_price_fp: u128,
        tolerance_bps: u16,
        max_age_secs: i64,
    ) -> Result<()> {
        crate::instructions::verify_against_pyth::handle_verify_against_pyth(
            ctx,
            expected_price_fp,
            tolerance_bps,
            max_age_secs,
        )
    }
}
