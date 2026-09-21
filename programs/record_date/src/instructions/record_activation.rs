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

    // The multiplier is the state, and only the multiplier. The issuer stages the next value
    // about four hours before it takes effect, and during that window `effective` still returns
    // the value in force while `effective_at` returns 0, so the timestamp has moved and the
    // multiplier has not. Comparing the timestamp as well treated that as an activation: a
    // receipt was written recording no change, dated at the Unix epoch, and `Registry.activations`
    // counted it. On mainnet that happens before every dividend, so "how often has this stock
    // moved" would have read double.
    //
    // Deciding on the multiplier alone is also what makes a flat receipt impossible rather than
    // merely unlikely: `new_fp` below is `live_fp` and `previous_fp` is
    // `record.live_multiplier_fp`, so `Receipt::is_flat` cannot be true of anything written past
    // this line. That is why there is no separate check on it here; a second check that cannot
    // fail is worse than none, and `a_receipt_never_records_a_flat_change` asserts the invariant
    // where it can be seen.
    require!(
        live_fp != record.live_multiplier_fp,
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
