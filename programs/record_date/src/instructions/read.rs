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

/// Returned in place of a timestamp when there is not one to report.
///
/// -1 rather than 0 because 0 is a real answer: it is 1970, and it is what a mint reports when it
/// has no dated activation. A venue has to be able to tell "no activation to measure from" apart
/// from "the activation was in 1970", which is exactly the confusion the staged window produced.
const NO_TIMESTAMP: i64 = -1;

/// The multiplier in force at `now`, and both timestamps the mint carries.
///
/// Both timestamps, rather than only the effective one, because they are mutually exclusive and a
/// caller needs to know which case it is in: either the value in force has a readable timestamp,
/// or the issuer has staged a value that has not arrived and the timestamp in force is gone.
struct Live {
    /// The multiplier in force, as 1e18 fixed point.
    fp: u128,
    /// When the value in force took effect, or 0 if the mint no longer says.
    effective_at: i64,
    /// When a staged activation takes effect, or 0 if none is staged.
    staged_at: i64,
}

fn live(account: &AccountInfo, now: i64) -> Result<Live> {
    require!(
        account.owner == &TOKEN_2022_PROGRAM_ID,
        RecordDateError::NotToken2022
    );
    let mint = token2022::read_mint(&account.try_borrow_data()?)?;
    let value = mint.scaled_ui_amount.effective(now);
    Ok(Live {
        fp: token2022::to_fixed_point(value)?,
        effective_at: mint.scaled_ui_amount.effective_at(now),
        staged_at: mint.scaled_ui_amount.staged_at(now),
    })
}

/// `raw` units in the units a wallet should display.
///
/// Return data is the answer as a little-endian u128.
pub fn handle_read_entitlement(ctx: Context<ReadMint>, raw_amount: u64) -> Result<()> {
    let now = Clock::get()?.unix_timestamp;
    let live = live(&ctx.accounts.mint, now)?;
    let scaled = token2022::scaled_amount(raw_amount, live.fp)?;

    set_return_data(&scaled.to_le_bytes());
    msg!(
        "Record Date: {} raw units of {} at multiplier {}e-18 = {} units, multiplier effective {}",
        raw_amount,
        symbol_of(&ctx.accounts.token_record),
        live.fp,
        scaled,
        live.effective_at
    );
    Ok(())
}

/// Whether a mint's multiplier has moved since this program last recorded it.
///
/// Return data is one byte: 1 when `record_activation` has work to do, 0 when it does not.
/// A cranker calls this first so it does not pay for a transaction that reverts.
///
/// **The multiplier is the whole of the test.** Comparing the activation timestamp as well made
/// the issuer's staging window look like an activation: for about four hours before a value takes
/// effect, `effective` still returns the old multiplier while `effective_at` returns 0, so the
/// timestamp had moved and the multiplier had not. A cranker was told to crank, anyone could, and
/// the receipt written recorded no change at a timestamp of 1970. The timestamp of the value in
/// force is genuinely unreadable during that window, and the answer to that is to keep it in a
/// `Receipt` rather than to treat its absence as a corporate action.
pub fn handle_activation_pending(ctx: Context<ReadMint>) -> Result<()> {
    let now = Clock::get()?.unix_timestamp;
    let live = live(&ctx.accounts.mint, now)?;
    let record = &ctx.accounts.token_record;

    let pending = (live.fp != record.live_multiplier_fp) as u8;
    set_return_data(&[pending]);
    msg!(
        "Record Date: activation pending for {}: {} (staged {}s ahead)",
        symbol_of(record),
        pending == 1,
        if live.staged_at > 0 { live.staged_at - now } else { 0 }
    );
    Ok(())
}

/// How far `now` is from the last activation, and from the next one.
///
/// Return data is two little-endian i64s. The first is seconds since the multiplier in force took
/// effect, or `-1` when no dated activation is known. The second is seconds until an activation
/// the issuer has staged, or `-1` when none is staged. A protocol that wants to follow the
/// issuer's advice to pause refuses to settle while either is inside `RECOMMENDED_PAUSE_SECS`,
/// which is 900.
///
/// Two numbers rather than one because the issuer's window is two-sided and the mint cannot
/// express the earlier half on its own. `ScaledUiAmountConfig` holds one timestamp, and while a
/// value is staged that timestamp describes the value that has not arrived, so the mint reports
/// nothing about when the value in force took effect. This program's `TokenRecord` does hold it,
/// which is the one thing here the mint has forgotten, and this is where that record is used for
/// something other than bookkeeping.
pub fn handle_settlement_window(ctx: Context<ReadMint>) -> Result<()> {
    let now = Clock::get()?.unix_timestamp;
    let live = live(&ctx.accounts.mint, now)?;
    let record = &ctx.accounts.token_record;

    // Which source can answer "when did the value in force take effect". Normally the mint, which
    // is the issuer's own account and the better witness. While a value is staged the mint has
    // overwritten that timestamp, so the record is the only source left.
    let since = if live.staged_at == 0 && live.effective_at > 0 {
        now - live.effective_at
    } else if record.live_effective_at > 0 {
        now - record.live_effective_at
    } else {
        NO_TIMESTAMP
    };
    let until = if live.staged_at > 0 {
        live.staged_at - now
    } else {
        NO_TIMESTAMP
    };

    let after = (0..=RECOMMENDED_PAUSE_SECS).contains(&since);
    let before = (0..=RECOMMENDED_PAUSE_SECS).contains(&until);

    let mut out = [0u8; 16];
    out[..8].copy_from_slice(&since.to_le_bytes());
    out[8..].copy_from_slice(&until.to_le_bytes());
    set_return_data(&out);
    msg!(
        "Record Date: {} last activation {}s ago, next in {}s, inside the recommended 15 minute pause: {}",
        symbol_of(record),
        since,
        until,
        before || after
    );
    Ok(())
}

fn symbol_of(record: &TokenRecord) -> String {
    let n = record.symbol_len as usize;
    String::from_utf8_lossy(&record.symbol[..n.min(crate::constants::SYMBOL_MAX)]).into_owned()
}
