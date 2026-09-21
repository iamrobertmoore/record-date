use crate::constants::{SYMBOL_MAX, TOKEN_2022_PROGRAM_ID, TOKEN_RECORD_SEED, REGISTRY_SEED};
use crate::error::RecordDateError;
use crate::state::{Registry, TokenRecord};
use crate::token2022;
use anchor_lang::prelude::*;

#[derive(Accounts)]
pub struct RegisterMint<'info> {
    #[account(mut)]
    pub authority: Signer<'info>,

    /// `has_one = authority` is the whole access rule for registration. Without it any signer
    /// could register any Token-2022 account, which makes the registry a public list of whatever
    /// anyone happened to pass rather than the set of mints this deployment was pointed at.
    #[account(mut, seeds = [REGISTRY_SEED], bump = registry.bump, has_one = authority)]
    pub registry: Account<'info, Registry>,

    /// CHECK: must be owned by the Token-2022 program, and must parse as a mint. Both are
    /// checked in the handler rather than by a type, because the mint is owned by a program
    /// this one does not link against.
    pub mint: UncheckedAccount<'info>,

    #[account(
        init,
        payer = authority,
        space = 8 + TokenRecord::INIT_SPACE,
        seeds = [TOKEN_RECORD_SEED, mint.key().as_ref()],
        bump
    )]
    pub token_record: Account<'info, TokenRecord>,

    pub system_program: Program<'info, System>,
}

pub fn handle_register_mint(ctx: Context<RegisterMint>, symbol: String) -> Result<()> {
    require!(
        ctx.accounts.mint.owner == &TOKEN_2022_PROGRAM_ID,
        RecordDateError::NotToken2022
    );
    let symbol_bytes = symbol.as_bytes();
    require!(symbol_bytes.len() <= SYMBOL_MAX, RecordDateError::SymbolTooLong);

    let clock = Clock::get()?;
    let mint = token2022::read_mint(&ctx.accounts.mint.try_borrow_data()?)?;

    let live = mint.scaled_ui_amount.effective(clock.unix_timestamp);
    let live_fp = token2022::to_fixed_point(live)?;

    let record = &mut ctx.accounts.token_record;
    record.mint = ctx.accounts.mint.key();
    record.symbol = [0u8; SYMBOL_MAX];
    record.symbol[..symbol_bytes.len()].copy_from_slice(symbol_bytes);
    record.symbol_len = symbol_bytes.len() as u8;
    record.decimals = mint.decimals;
    record.base_multiplier_bits = mint.scaled_ui_amount.multiplier.to_bits();
    record.live_multiplier_bits = live.to_bits();
    record.live_multiplier_fp = live_fp;
    record.live_effective_at = mint.scaled_ui_amount.effective_at(clock.unix_timestamp);
    record.activations = 0;
    record.last_seen_slot = clock.slot;
    record.bump = ctx.bumps.token_record;

    ctx.accounts.registry.mints += 1;

    msg!(
        "Record Date: registered {} ({}) live multiplier {} from base {}",
        symbol,
        ctx.accounts.mint.key(),
        live,
        mint.scaled_ui_amount.multiplier
    );
    Ok(())
}
