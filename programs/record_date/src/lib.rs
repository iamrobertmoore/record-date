//! Record Date: a tokenized stock's dividend entitlement, on chain.
//!
//! On Solana an xStock's dividend is reinvested by raising a multiplier stored in the
//! Token-2022 mint account. The balance does not change, so the multiplier is the only
//! record that a payment happened at all, and every integrator is individually responsible
//! for applying it. Two things make that harder than it reads:
//!
//! 1. The field named `multiplier` is the **previous** value. The live one is
//!    `new_multiplier`, and it takes over at `new_multiplier_effective_timestamp`.
//!    On 16 September 2026, 357 of the 737 xStock mints on Solana differed between the two.
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
}
