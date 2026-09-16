use crate::constants::{RECOMMENDED_PAUSE_SECS, TOKEN_2022_PROGRAM_ID, TOKEN_RECORD_SEED};
use crate::error::RecordDateError;
use crate::state::TokenRecord;
use crate::token2022;
use anchor_lang::prelude::*;
use anchor_lang::solana_program::program::set_return_data;

#[derive(Accounts)]
pub struct ReadMint<'info> {
    /// CHECK: must be owned by the Token-2022 program. Checked in each handler.
    pub mint: UncheckedAccount<'info>,

    #[account(seeds = [TOKEN_RECORD_SEED, mint.key().as_ref()], bump = token_record.bump)]
    pub token_record: Account<'info, TokenRecord>,
}

/// The effective multiplier for a mint, read live, plus its activation timestamp.
fn live(account: &AccountInfo, now: i64) -> Result<(u128, i64, f64)> {
    require!(
        account.owner == &TOKEN_2022_PROGRAM_ID,
        RecordDateError::NotToken2022
    );
    let mint = token2022::read_mint(&account.try_borrow_data()?)?;
    let value = mint.scaled_ui_amount.effective(now);
    Ok((
        token2022::to_fixed_point(value)?,
        mint.scaled_ui_amount.effective_at(now),
        value,
    ))
}

/// `raw` units in the units a wallet should display.
///
/// Return data is the answer as a little-endian u128.
pub fn handle_read_entitlement(ctx: Context<ReadMint>, raw_amount: u64) -> Result<()> {
    let now = Clock::get()?.unix_timestamp;
    let (fp, effective_at, _) = live(&ctx.accounts.mint, now)?;
    let scaled = token2022::scaled_amount(raw_amount, fp)?;

    set_return_data(&scaled.to_le_bytes());
    msg!(
        "Record Date: {} raw units of {} at multiplier {}e-18 = {} units, multiplier effective {}",
        raw_amount,
        symbol_of(&ctx.accounts.token_record),
        fp,
        scaled,
        effective_at
    );
    Ok(())
}

/// Whether a mint's multiplier has moved since this program last recorded it.
///
/// Return data is one byte: 1 when `record_activation` has work to do, 0 when it does not.
/// A cranker calls this first so it does not pay for a transaction that reverts.
pub fn handle_activation_pending(ctx: Context<ReadMint>) -> Result<()> {
    let now = Clock::get()?.unix_timestamp;
    let (fp, effective_at, _) = live(&ctx.accounts.mint, now)?;
    let record = &ctx.accounts.token_record;

    let pending = (fp != record.live_multiplier_fp || effective_at != record.live_effective_at) as u8;
    set_return_data(&[pending]);
    msg!(
        "Record Date: activation pending for {}: {}",
        symbol_of(record),
        pending == 1
    );
    Ok(())
}

/// How far `now` is from the last activation the issuer published.
///
/// Return data is a little-endian i64: seconds since the multiplier took effect. Negative
/// would mean the activation is still ahead. A protocol that wants to follow the issuer's
/// advice to pause around an activation refuses to settle while the absolute value is inside
/// `RECOMMENDED_PAUSE_SECS`, which is 900.
pub fn handle_settlement_window(ctx: Context<ReadMint>) -> Result<()> {
    let now = Clock::get()?.unix_timestamp;
    let (_, effective_at, _) = live(&ctx.accounts.mint, now)?;
    let delta = now - effective_at;
    let in_window = delta.abs() <= RECOMMENDED_PAUSE_SECS;

    set_return_data(&delta.to_le_bytes());
    msg!(
        "Record Date: {} activation was {}s ago, inside the recommended 15 minute pause: {}",
        symbol_of(&ctx.accounts.token_record),
        delta,
        in_window
    );
    Ok(())
}

fn symbol_of(record: &TokenRecord) -> String {
    let n = record.symbol_len as usize;
    String::from_utf8_lossy(&record.symbol[..n.min(crate::constants::SYMBOL_MAX)]).into_owned()
}
