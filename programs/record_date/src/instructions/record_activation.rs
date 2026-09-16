use crate::constants::{RECEIPT_SEED, REGISTRY_SEED, TOKEN_2022_PROGRAM_ID, TOKEN_RECORD_SEED};
use crate::error::RecordDateError;
use crate::state::{Receipt, Registry, TokenRecord};
use crate::token2022;
use anchor_lang::prelude::*;

/// Permissionless. Anyone can crank a mint whose multiplier has moved, and the receipt is
/// written once, in sequence, and never edited.
#[derive(Accounts)]
pub struct RecordActivation<'info> {
    #[account(mut)]
    pub cranker: Signer<'info>,

    #[account(mut, seeds = [REGISTRY_SEED], bump = registry.bump)]
    pub registry: Account<'info, Registry>,

    /// CHECK: must be owned by the Token-2022 program. Checked in the handler.
    pub mint: UncheckedAccount<'info>,

    #[account(
        mut,
        seeds = [TOKEN_RECORD_SEED, mint.key().as_ref()],
        bump = token_record.bump
    )]
    pub token_record: Account<'info, TokenRecord>,

    #[account(
        init,
        payer = cranker,
        space = 8 + Receipt::INIT_SPACE,
        seeds = [RECEIPT_SEED, mint.key().as_ref(), &token_record.activations.to_le_bytes()],
        bump
    )]
    pub receipt: Account<'info, Receipt>,

    pub system_program: Program<'info, System>,
}

pub fn handle_record_activation(ctx: Context<RecordActivation>) -> Result<()> {
    require!(
        ctx.accounts.mint.owner == &TOKEN_2022_PROGRAM_ID,
        RecordDateError::NotToken2022
    );

    let clock = Clock::get()?;
    let mint = token2022::read_mint(&ctx.accounts.mint.try_borrow_data()?)?;

    let live = mint.scaled_ui_amount.effective(clock.unix_timestamp);
    let live_fp = token2022::to_fixed_point(live)?;
    let live_at = mint.scaled_ui_amount.effective_at(clock.unix_timestamp);

    let record = &mut ctx.accounts.token_record;
    require_keys_eq!(
        record.mint,
        ctx.accounts.mint.key(),
        RecordDateError::NotRegistered
    );

    // The multiplier is the state. If it has not moved, and the activation timestamp has not
    // moved, there is nothing to record. `activation_pending` answers this without paying.
    require!(
        live_fp != record.live_multiplier_fp || live_at != record.live_effective_at,
        RecordDateError::NothingToRecord
    );

    let receipt = &mut ctx.accounts.receipt;
    receipt.mint = record.mint;
    receipt.sequence = record.activations;
    receipt.previous_bits = record.live_multiplier_bits;
    receipt.new_bits = live.to_bits();
    receipt.previous_fp = record.live_multiplier_fp;
    receipt.new_fp = live_fp;
    receipt.effective_at = live_at;
    receipt.recorded_at = clock.unix_timestamp;
    receipt.raw_supply = mint.supply;
    receipt.bump = ctx.bumps.receipt;

    let delta_fp = receipt.delta_fp();
    let sequence = receipt.sequence;

    record.live_multiplier_bits = live.to_bits();
    record.live_multiplier_fp = live_fp;
    record.live_effective_at = live_at;
    record.activations += 1;
    record.last_seen_slot = clock.slot;

    ctx.accounts.registry.activations += 1;

    msg!(
        "Record Date: activation {} recorded, multiplier {} -> {} (delta {}e-18), effective {}",
        sequence,
        f64::from_bits(receipt.previous_bits),
        live,
        delta_fp,
        live_at
    );
    Ok(())
}
