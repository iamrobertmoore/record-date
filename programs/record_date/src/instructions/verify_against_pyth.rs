use crate::constants::{PYTH_BINDING_SEED, TOKEN_RECORD_SEED};
use crate::error::RecordDateError;
use crate::pyth;
use crate::state::{PythBinding, TokenRecord};
use anchor_lang::prelude::*;
use anchor_lang::solana_program::program::set_return_data;

#[derive(Accounts)]
pub struct VerifyAgainstPyth<'info> {
    /// CHECK: the mint the binding is for. Only its key is read.
    pub mint: UncheckedAccount<'info>,

    /// Present so the check is tied to a mint this program knows about, and so the log line can
    /// name the symbol. A caller cannot verify against a Pyth feed for a mint nobody registered.
    #[account(seeds = [TOKEN_RECORD_SEED, mint.key().as_ref()], bump = token_record.bump)]
    pub token_record: Account<'info, TokenRecord>,

    #[account(seeds = [PYTH_BINDING_SEED, mint.key().as_ref()], bump = binding.bump)]
    pub binding: Account<'info, PythBinding>,

    /// CHECK: must be owned by the Pyth receiver program. Checked in the handler, because this
    /// program does not link against Pyth and so cannot use a typed account for it.
    pub price_update: UncheckedAccount<'info>,
}

/// Check a price against Pyth's, and refuse if it is stale or for the wrong feed.
///
/// This is the instruction that puts Pyth genuinely on chain. Everything else in this program
/// reads the issuer's own mint account, so a number taken from there is the issuer agreeing with
/// itself. A Pyth price is a third party's value for the same equity, and the three things
/// checked here are the three ways that comparison can be meaningless:
///
/// 1. **Ownership.** The account must be owned by the Pyth receiver. An account that merely has
///    the right bytes at the right offset is not a price.
/// 2. **Identity.** The feed id must equal the one bound to this mint. Without this the caller
///    chooses the feed, so a fresh price for a different stock would pass.
/// 3. **Age.** A price older than `max_age_secs` is refused. This is not theoretical: the devnet
///    AAPL feed was 33 days stale when it was captured, and a check that only compared numbers
///    would have accepted an August price against a September settlement.
///
/// `expected_price_fp` is the price the caller is about to settle at, in 1e18 fixed point.
/// `tolerance_bps` is how far it may differ. `max_age_secs` is how old a price the caller is
/// willing to accept, which is its own policy rather than this program's: see
/// `MAX_PRICE_AGE_CEILING_SECS`. Return data is the Pyth price (16 bytes LE) followed by the
/// deviation in basis points (16 bytes LE), so a caller can log what it accepted.
pub fn handle_verify_against_pyth(
    ctx: Context<VerifyAgainstPyth>,
    expected_price_fp: u128,
    tolerance_bps: u16,
    max_age_secs: i64,
) -> Result<()> {
    let now = Clock::get()?.unix_timestamp;

    require!(
        (0..=pyth::MAX_PRICE_AGE_CEILING_SECS).contains(&max_age_secs),
        RecordDateError::PriceAgeCeilingExceeded
    );

    require!(
        ctx.accounts.price_update.owner == &pyth::PYTH_RECEIVER_PROGRAM_ID,
        RecordDateError::NotPythReceiver
    );

    let data = ctx.accounts.price_update.try_borrow_data()?;
    let update = pyth::read(&data)?;
    drop(data);

    require!(
        update.feed_id == ctx.accounts.binding.feed_id,
        RecordDateError::FeedIdMismatch
    );
    require!(
        !update.is_stale(now, max_age_secs),
        RecordDateError::StalePythPrice
    );

    let pyth_fp = update.price_fp()?;
    let deviation = pyth::deviation_bps(pyth_fp, expected_price_fp);
    require!(
        deviation <= tolerance_bps as u128,
        RecordDateError::PriceDeviation
    );

    let mut out = [0u8; 32];
    out[..16].copy_from_slice(&pyth_fp.to_le_bytes());
    out[16..].copy_from_slice(&deviation.to_le_bytes());
    set_return_data(&out);

    let symbol_len = ctx.accounts.binding.feed_symbol_len as usize;
    let feed = String::from_utf8_lossy(
        &ctx.accounts.binding.feed_symbol[..symbol_len.min(crate::constants::SYMBOL_MAX)],
    )
    .into_owned();

    msg!(
        "Record Date: {} verified against Pyth {} at {} (age {}s of a {}s limit), caller said \
         {}e-18, deviation {}bps of a {}-bps tolerance, feed {}",
        symbol_of(&ctx.accounts.token_record),
        feed,
        update.price_at_exponent(),
        update.age(now),
        max_age_secs,
        expected_price_fp,
        deviation,
        tolerance_bps,
        b58(&update.feed_id)
    );
    Ok(())
}

fn symbol_of(record: &TokenRecord) -> String {
    let n = record.symbol_len as usize;
    String::from_utf8_lossy(&record.symbol[..n.min(crate::constants::SYMBOL_MAX)]).into_owned()
}

/// A feed id in the base58 a human pastes into Pyth's own tools. The log line is the only place
/// anyone reads this, and hex is what the fixture files use while base58 is what the directory
/// publishes.
fn b58(bytes: &[u8; 32]) -> String {
    const ALPHABET: &[u8; 58] =
        b"123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";
    let mut digits: Vec<u8> = vec![0];
    for byte in bytes.iter() {
        let mut carry = *byte as u32;
        for digit in digits.iter_mut() {
            carry += (*digit as u32) << 8;
            *digit = (carry % 58) as u8;
            carry /= 58;
        }
        while carry > 0 {
            digits.push((carry % 58) as u8);
            carry /= 58;
        }
    }
    let mut out = String::new();
    for byte in bytes.iter() {
        if *byte == 0 {
            out.push('1');
        } else {
            break;
        }
    }
    for digit in digits.iter().rev() {
        out.push(ALPHABET[*digit as usize] as char);
    }
    out
}
