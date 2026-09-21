use crate::constants::{PYTH_BINDING_SEED, REGISTRY_SEED, SYMBOL_MAX, TOKEN_RECORD_SEED};
use crate::error::RecordDateError;
use crate::state::{PythBinding, Registry, TokenRecord};
use anchor_lang::prelude::*;

#[derive(Accounts)]
pub struct BindPythFeed<'info> {
    #[account(mut)]
    pub authority: Signer<'info>,

    /// The registry, and `has_one = authority` with it.
    ///
    /// This account is here for one reason, and it is not bookkeeping. The binding exists so that
    /// a caller cannot choose which feed the check is against: `PythBinding` is what makes
    /// `verify_against_pyth` ask "is there a fresh price for *this* stock" rather than "is there a
    /// fresh price anywhere". With the bind open to any signer, the caller chooses the feed again,
    /// in the same transaction if they like, and the refusal the binding exists to provide can be
    /// undone by whoever wants to undo it. The authority is the thing that closes that, so the
    /// check is on the bind rather than on the verify.
    #[account(mut, seeds = [REGISTRY_SEED], bump = registry.bump, has_one = authority)]
    pub registry: Account<'info, Registry>,

    /// CHECK: the mint this binding is for. Only its key is read; that the mint exists and is a
    /// Token-2022 mint was established by `register_mint`, which the record below proves ran.
    pub mint: UncheckedAccount<'info>,

    /// The mint has to be registered before it can be bound, which is what this account asserts:
    /// it is the PDA for this mint, so its existence is the evidence.
    #[account(seeds = [TOKEN_RECORD_SEED, mint.key().as_ref()], bump = token_record.bump)]
    pub token_record: Account<'info, TokenRecord>,

    /// `init_if_needed` rather than `init` so that re-running a demo does not fail on the second
    /// run. Rebinding overwrites, which is what makes a corrected feed id fixable, and it is
    /// authority-only for the reason given on the registry account above: a rebind that anyone
    /// can perform is a feed choice that anyone can make.
    #[account(
        init_if_needed,
        payer = authority,
        space = 8 + PythBinding::INIT_SPACE,
        seeds = [PYTH_BINDING_SEED, mint.key().as_ref()],
        bump
    )]
    pub binding: Account<'info, PythBinding>,

    pub system_program: Program<'info, System>,
}

/// Bind a registered mint to a Pyth price feed.
///
/// The feed id is stored rather than the account address. A `PriceUpdateV2` account is rewritten
/// on every update and its address is not stable across feeds, so binding to an address would
/// break on the next publish. The id is what identifies the feed.
pub fn handle_bind_pyth_feed(
    ctx: Context<BindPythFeed>,
    feed_id: [u8; 32],
    feed_symbol: String,
) -> Result<()> {
    let symbol = feed_symbol.as_bytes();
    require!(symbol.len() <= SYMBOL_MAX, RecordDateError::FeedSymbolTooLong);

    let clock = Clock::get()?;
    let binding = &mut ctx.accounts.binding;
    binding.mint = ctx.accounts.mint.key();
    binding.feed_id = feed_id;
    binding.feed_symbol = [0u8; SYMBOL_MAX];
    binding.feed_symbol[..symbol.len()].copy_from_slice(symbol);
    binding.feed_symbol_len = symbol.len() as u8;
    binding.bound_at = clock.unix_timestamp;
    binding.bump = ctx.bumps.binding;

    msg!(
        "Record Date: bound {} to Pyth feed {} ({})",
        ctx.accounts.mint.key(),
        feed_symbol,
        hex32(&feed_id)
    );
    Ok(())
}

/// A feed id as hex, for the log line. Solana log lines are the only place a human sees this.
fn hex32(bytes: &[u8; 32]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut out = String::with_capacity(64);
    for byte in bytes.iter() {
        out.push(HEX[(byte >> 4) as usize] as char);
        out.push(HEX[(byte & 0x0f) as usize] as char);
    }
    out
}
