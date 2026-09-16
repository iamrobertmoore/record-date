use crate::constants::REGISTRY_SEED;
use crate::state::Registry;
use anchor_lang::prelude::*;

#[derive(Accounts)]
pub struct InitRegistry<'info> {
    #[account(mut)]
    pub authority: Signer<'info>,

    #[account(
        init,
        payer = authority,
        space = 8 + Registry::INIT_SPACE,
        seeds = [REGISTRY_SEED],
        bump
    )]
    pub registry: Account<'info, Registry>,

    pub system_program: Program<'info, System>,
}

pub fn handle_init_registry(ctx: Context<InitRegistry>) -> Result<()> {
    let registry = &mut ctx.accounts.registry;
    registry.authority = ctx.accounts.authority.key();
    registry.mints = 0;
    registry.activations = 0;
    registry.bump = ctx.bumps.registry;
    msg!("Record Date: registry initialised for {}", registry.authority);
    Ok(())
}
