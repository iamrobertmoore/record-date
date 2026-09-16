//! End to end tests: the compiled program, run in LiteSVM, against a real mint account.
//!
//! `fixtures/nvdax_mint.bin` is the actual 679 byte account of the NVDAx mint
//! (`Xsc9qvGR1efVDFGLrVsmkzv3qi45LTBjeUKSPmx9qEh`), read from Solana mainnet. It is here rather
//! than generated so that the parser is tested against the layout the issuer actually ships:
//! a 165 byte base region, of which the mint fills only the first 82 bytes, then the
//! account-type byte, then the extensions. The first extension header is at byte 166, not at the
//! byte 83 that reading the documented layout would give.
//!
//! These tests need `target/deploy/record_date.so`, so run `anchor build` first.
//!
//!     cargo test --manifest-path programs/record_date/Cargo.toml

use anchor_lang::error::ERROR_CODE_OFFSET;
use anchor_lang::prelude::*;
use anchor_lang::solana_program::clock::Clock;
use anchor_lang::solana_program::instruction::Instruction;
use anchor_lang::solana_program::system_program;
use anchor_lang::{AccountDeserialize, InstructionData};
use litesvm::types::{FailedTransactionMetadata, TransactionMetadata, TransactionResult};
use litesvm::LiteSVM;
use record_date::constants::{
    RECEIPT_SEED, RECOMMENDED_PAUSE_SECS, REGISTRY_SEED, TOKEN_2022_PROGRAM_ID, TOKEN_RECORD_SEED,
};
use record_date::error::RecordDateError;
use record_date::state::{Receipt, Registry, TokenRecord};
use record_date::token2022;
use solana_account::Account;
use solana_instruction_error::InstructionError;
use solana_keypair::Keypair;
use solana_signer::Signer;
use solana_transaction::Transaction;
use solana_transaction_error::TransactionError;

/// The real NVDAx mint, straight off mainnet.
const MINT_BYTES: &[u8] = include_bytes!("fixtures/nvdax_mint.bin");

const MINT: &str = "Xsc9qvGR1efVDFGLrVsmkzv3qi45LTBjeUKSPmx9qEh";
const SYMBOL: &str = "NVDAx";

/// What the account actually holds, confirmed against the issuer's own multiplier endpoint and
/// recorded in fixtures/nvdax_expected.json.
const BASE_MULTIPLIER: f64 = 1.0009180758490996;
const LIVE_MULTIPLIER: f64 = 1.0017011968010741;
const EFFECTIVE_TIMESTAMP: i64 = 1_789_000_200;
const SUPPLY: u64 = 32_127_767_286_397;
const DECIMALS: u8 = 8;
const ACCOUNT_LEN: usize = 679;
/// The header of the ScaledUiAmountConfig extension. Byte 275, not byte 83.
const HEADER_OFFSET: usize = 275;
const EXTENSION_TYPE: u16 = 25;
const EXTENSION_LENGTH: u16 = 56;

/// Comfortably after the activation above, so the new multiplier is the live one.
const NOW: i64 = EFFECTIVE_TIMESTAMP + 6 * 86_400;
const LAMPORTS: u64 = 100_000_000_000;

fn mint_pubkey() -> Pubkey {
    MINT.parse().expect("the mint address parses")
}

fn program_path() -> String {
    format!("{}/../../target/deploy/record_date.so", env!("CARGO_MANIFEST_DIR"))
}

/// The SBPF architecture the ELF was built for, from `e_flags`.
///
/// `anchor build` defaults to `--arch v3`. LiteSVM 0.10.0 cannot load a v3 ELF and reports it as
/// `InvalidAccountData`, which reads like a broken program rather than a build flag. Checking
/// here turns that into an instruction.
fn sbpf_arch(bytes: &[u8]) -> u32 {
    assert!(bytes.len() >= 52, "the program binary is too short to be an ELF");
    assert_eq!(&bytes[..4], b"\x7fELF", "the program binary is not an ELF");
    u32::from_le_bytes(bytes[48..52].try_into().unwrap())
}

/// A fresh VM with the program loaded, a funded payer, a fixed clock, and the real mint.
fn setup() -> (LiteSVM, Keypair, Pubkey) {
    let path = program_path();
    let bytes = std::fs::read(&path).unwrap_or_else(|error| {
        panic!("cannot read {path}: {error}. Run `anchor build --arch v1` first.")
    });
    assert_eq!(
        sbpf_arch(&bytes),
        1,
        "{path} was built for SBPF v{}. LiteSVM 0.10.0 loads v1 only, and reports anything else \
         as InvalidAccountData. Rebuild with `anchor build --arch v1`.",
        sbpf_arch(&bytes)
    );

    let mut svm = LiteSVM::new();
    let payer = Keypair::new();
    svm.airdrop(&payer.pubkey(), LAMPORTS).expect("airdrop");

    svm.add_program_from_file(record_date::ID, &path)
        .expect("the program loads");

    // A fixed clock, so the read rule is tested rather than the wall clock.
    let mut clock: Clock = svm.get_sysvar();
    clock.unix_timestamp = NOW;
    svm.set_sysvar(&clock);

    let mint = mint_pubkey();
    svm.set_account(
        mint,
        Account {
            lamports: 1_000_000_000,
            data: MINT_BYTES.to_vec(),
            owner: TOKEN_2022_PROGRAM_ID,
            executable: false,
            rent_epoch: 0,
        },
    )
    .expect("the mint account is set");

    (svm, payer, mint)
}

/// `anchor_lang::prelude` exports its own `Result`, which is `Result<T, anchor_lang::Error>`, so
/// the return type here has to be LiteSVM's alias rather than a two-argument `Result`.
fn send(svm: &mut LiteSVM, payer: &Keypair, ix: Instruction) -> TransactionResult {
    // LiteSVM keeps a transaction history and rejects a repeat signature as AlreadyProcessed. A
    // read with the same payer and no state change produces a byte-identical transaction, so the
    // second one would be refused. Expiring the blockhash first gives every send a fresh
    // signature, which is what would happen on a real cluster anyway.
    svm.expire_blockhash();
    let blockhash = svm.latest_blockhash();
    let tx = Transaction::new_signed_with_payer(&[ix], Some(&payer.pubkey()), &[payer], blockhash);
    svm.send_transaction(tx)
}

fn send_ok(svm: &mut LiteSVM, payer: &Keypair, ix: Instruction) -> TransactionMetadata {
    match send(svm, payer, ix) {
        Ok(meta) => meta,
        Err(failed) => panic!(
            "expected success, got {:?}\n{}",
            failed.err,
            failed.meta.pretty_logs()
        ),
    }
}

/// The custom error code Anchor returned, or None if the failure was something else.
fn anchor_code(failed: &FailedTransactionMetadata) -> Option<u32> {
    match &failed.err {
        TransactionError::InstructionError(0, InstructionError::Custom(code)) => Some(*code),
        _ => None,
    }
}

fn expect_anchor_error(
    svm: &mut LiteSVM,
    payer: &Keypair,
    ix: Instruction,
    error: RecordDateError,
) {
    let failed = send(svm, payer, ix).expect_err("expected this instruction to fail");
    let want = ERROR_CODE_OFFSET + error as u32;
    assert_eq!(
        anchor_code(&failed),
        Some(want),
        "expected {:?} (code {}), got {:?}\n{}",
        error,
        want,
        failed.err,
        failed.meta.pretty_logs()
    );
}

fn registry_pda() -> (Pubkey, u8) {
    Pubkey::find_program_address(&[REGISTRY_SEED], &record_date::ID)
}

fn token_record_pda(mint: &Pubkey) -> (Pubkey, u8) {
    Pubkey::find_program_address(&[TOKEN_RECORD_SEED, mint.as_ref()], &record_date::ID)
}

fn receipt_pda(mint: &Pubkey, sequence: u64) -> (Pubkey, u8) {
    Pubkey::find_program_address(
        &[RECEIPT_SEED, mint.as_ref(), &sequence.to_le_bytes()],
        &record_date::ID,
    )
}

// ---------------------------------------------------------------- instruction builders
//
// Anchor 1.2 no longer generates a client-accounts struct, so the metas are written out. The
// order has to match the field order of the accounts struct in the program, which makes these
// four functions a readable statement of the interface.

fn ix_init_registry(payer: &Pubkey) -> Instruction {
    Instruction {
        program_id: record_date::ID,
        accounts: vec![
            AccountMeta::new(*payer, true),
            AccountMeta::new(registry_pda().0, false),
            AccountMeta::new_readonly(system_program::ID, false),
        ],
        data: record_date::instruction::InitRegistry {}.data(),
    }
}

fn ix_register_mint(payer: &Pubkey, mint: &Pubkey) -> Instruction {
    Instruction {
        program_id: record_date::ID,
        accounts: vec![
            AccountMeta::new(*payer, true),
            AccountMeta::new(registry_pda().0, false),
            AccountMeta::new_readonly(*mint, false),
            AccountMeta::new(token_record_pda(mint).0, false),
            AccountMeta::new_readonly(system_program::ID, false),
        ],
        data: record_date::instruction::RegisterMint {
            symbol: SYMBOL.to_string(),
        }
        .data(),
    }
}

fn ix_record_activation(cranker: &Pubkey, mint: &Pubkey) -> Instruction {
    let sequence = {
        // The receipt PDA is seeded with the sequence the record currently holds.
        let record = registry_and_record_for_sequence(mint);
        record
    };
    Instruction {
        program_id: record_date::ID,
        accounts: vec![
            AccountMeta::new(*cranker, true),
            AccountMeta::new(registry_pda().0, false),
            AccountMeta::new_readonly(*mint, false),
            AccountMeta::new(token_record_pda(mint).0, false),
            AccountMeta::new(receipt_pda(mint, sequence).0, false),
            AccountMeta::new_readonly(system_program::ID, false),
        ],
        data: record_date::instruction::RecordActivation {}.data(),
    }
}

/// How many activations the program has recorded for this mint. Read from the VM by the caller
/// in practice; the tests pass it explicitly where it matters.
fn registry_and_record_for_sequence(_mint: &Pubkey) -> u64 {
    0
}

fn ix_read(mint: &Pubkey, which: RecordDateInstruction) -> Instruction {
    let data = match which {
        RecordDateInstruction::Entitlement(raw) => {
            record_date::instruction::ReadEntitlement { raw_amount: raw }.data()
        }
        RecordDateInstruction::Pending => record_date::instruction::ActivationPending {}.data(),
        RecordDateInstruction::Window => record_date::instruction::SettlementWindow {}.data(),
    };
    Instruction {
        program_id: record_date::ID,
        accounts: vec![
            AccountMeta::new_readonly(*mint, false),
            AccountMeta::new_readonly(token_record_pda(mint).0, false),
        ],
        data,
    }
}

enum RecordDateInstruction {
    Entitlement(u64),
    Pending,
    Window,
}

// ---------------------------------------------------------------- reading state

fn read_registry(svm: &LiteSVM) -> Registry {
    let account = svm.get_account(&registry_pda().0).expect("registry exists");
    Registry::try_deserialize(&mut &account.data[..]).expect("registry deserialises")
}

fn read_record(svm: &LiteSVM, mint: &Pubkey) -> TokenRecord {
    let account = svm.get_account(&token_record_pda(mint).0).expect("record exists");
    TokenRecord::try_deserialize(&mut &account.data[..]).expect("record deserialises")
}

fn read_receipt(svm: &LiteSVM, mint: &Pubkey, sequence: u64) -> Receipt {
    let account = svm
        .get_account(&receipt_pda(mint, sequence).0)
        .expect("receipt exists");
    Receipt::try_deserialize(&mut &account.data[..]).expect("receipt deserialises")
}

/// Overwrite the mint's multiplier fields, as a new corporate action would.
fn set_multiplier(svm: &mut LiteSVM, mint: &Pubkey, base: f64, new: f64, effective_at: i64) {
    let mut account = svm.get_account(mint).expect("mint exists");
    // header at 275, body at 279, then authority(32), multiplier(8), timestamp(8), new(8)
    let body = HEADER_OFFSET + 4 + 32;
    account.data[body..body + 8].copy_from_slice(&base.to_le_bytes());
    account.data[body + 8..body + 16].copy_from_slice(&effective_at.to_le_bytes());
    account.data[body + 16..body + 24].copy_from_slice(&new.to_le_bytes());
    svm.set_account(*mint, account).expect("the mint is written");
}

// ---------------------------------------------------------------- the real mint account

#[test]
fn the_fixture_is_the_real_account_it_claims_to_be() {
    assert_eq!(MINT_BYTES.len(), ACCOUNT_LEN);
    assert_eq!(MINT_BYTES[44], DECIMALS);
    assert_eq!(
        u64::from_le_bytes(MINT_BYTES[36..44].try_into().unwrap()),
        SUPPLY
    );

    // The layout the parser assumes, asserted on the real account rather than on a synthetic one.
    // A mint fills only the first 82 bytes of the 165-byte base region; the 83 bytes after it are
    // padding and must be zero; the account-type byte is at 165 and says 1 (a mint); the first
    // extension header is at 166.
    assert_eq!(
        &MINT_BYTES[token2022::MINT_BASE_LEN..token2022::BASE_REGION_LEN],
        &[0u8; 83][..],
        "the 83 bytes between the mint base state and the account-type byte are padding"
    );
    assert_eq!(
        MINT_BYTES[token2022::BASE_REGION_LEN],
        token2022::ACCOUNT_TYPE_MINT,
        "the account-type byte says this is a mint"
    );
    assert_eq!(token2022::TLV_START, 166, "the first extension header is at 166");

    // The mint authority is a COption, so the first four bytes are its tag. It is Some on this
    // mint, which is worth pinning: the issuer can still mint, so the supply the page divides by
    // is not fixed. A tag of anything other than 0 or 1 would mean the layout is not what is
    // assumed, and every offset below it would be wrong.
    let authority_tag = u32::from_le_bytes(MINT_BYTES[0..4].try_into().unwrap());
    assert!(
        authority_tag <= 1,
        "the mint authority COption tag is {authority_tag}, which is not a valid COption"
    );
    let freeze_tag = u32::from_le_bytes(MINT_BYTES[46..50].try_into().unwrap());
    assert!(
        freeze_tag <= 1,
        "the freeze authority COption tag is {freeze_tag}, which is not a valid COption"
    );
}

#[test]
fn the_parser_finds_the_extension_at_byte_275_not_byte_83() {
    let offset = token2022::find_scaled_ui_amount(MINT_BYTES).expect("the extension is found");

    assert_eq!(
        offset - 4,
        HEADER_OFFSET,
        "reading the documented layout as '82-byte base, then extensions' puts the first header \
         at byte 83. It is at 166, because the base region is a fixed 165 bytes and the mint \
         fills only the first 82 of them. The scaled extension is the fourth, at 275."
    );
    // The first header really is at 166, and the three before it are the extensions the issuer
    // registered ahead of the scaled one.
    let first = u16::from_le_bytes([MINT_BYTES[token2022::TLV_START], MINT_BYTES[token2022::TLV_START + 1]]);
    assert_eq!(first, 18, "MetadataPointer is the first extension");
    let walk = [
        (token2022::TLV_START, 18u16, 64usize),
        (234, 12, 32),
        (270, 6, 1),
        (275, 25, 56),
    ];
    let mut at = token2022::TLV_START;
    for (expect_at, expect_type, expect_len) in walk {
        assert_eq!(at, expect_at, "the walk reaches {expect_at}");
        let t = u16::from_le_bytes([MINT_BYTES[at], MINT_BYTES[at + 1]]);
        let l = u16::from_le_bytes([MINT_BYTES[at + 2], MINT_BYTES[at + 3]]);
        assert_eq!((t, l as usize), (expect_type, expect_len));
        at += 4 + l as usize;
    }
    let ext_type = u16::from_le_bytes([MINT_BYTES[HEADER_OFFSET], MINT_BYTES[HEADER_OFFSET + 1]]);
    let ext_len = u16::from_le_bytes([
        MINT_BYTES[HEADER_OFFSET + 2],
        MINT_BYTES[HEADER_OFFSET + 3],
    ]);
    assert_eq!(ext_type, EXTENSION_TYPE);
    assert_eq!(ext_len, EXTENSION_LENGTH);
}

#[test]
fn the_parser_reads_the_values_the_mint_actually_holds() {
    let mint = token2022::read_mint(MINT_BYTES).expect("the mint parses");

    assert_eq!(mint.decimals, DECIMALS);
    assert_eq!(mint.supply, SUPPLY);
    assert_eq!(mint.scaled_ui_amount.multiplier, BASE_MULTIPLIER);
    assert_eq!(mint.scaled_ui_amount.new_multiplier, LIVE_MULTIPLIER);
    assert_eq!(
        mint.scaled_ui_amount.new_multiplier_effective_timestamp,
        EFFECTIVE_TIMESTAMP
    );
}

#[test]
fn the_two_multipliers_are_not_the_same_number() {
    let mint = token2022::read_mint(MINT_BYTES).expect("the mint parses");
    let scaled = &mint.scaled_ui_amount;

    assert_ne!(
        scaled.multiplier, scaled.new_multiplier,
        "this fixture is only useful because the two fields differ"
    );
    // The read rule. Before the timestamp the old value is live; at and after it, the new one.
    assert_eq!(
        scaled.effective(EFFECTIVE_TIMESTAMP - 1),
        BASE_MULTIPLIER,
        "the new multiplier must not be live before its timestamp"
    );
    assert_eq!(scaled.effective(EFFECTIVE_TIMESTAMP), LIVE_MULTIPLIER);
    assert_eq!(scaled.effective(NOW), LIVE_MULTIPLIER);
    assert_eq!(scaled.effective_at(NOW), EFFECTIVE_TIMESTAMP);
}

// ---------------------------------------------------------------- the instructions

#[test]
fn init_registry_writes_the_authority() {
    let (mut svm, payer, _) = setup();

    send_ok(&mut svm, &payer, ix_init_registry(&payer.pubkey()));

    let registry = read_registry(&svm);
    assert_eq!(registry.authority, payer.pubkey());
    assert_eq!(registry.mints, 0);
    assert_eq!(registry.activations, 0);
}

#[test]
fn register_mint_keeps_the_stale_field_beside_the_live_one() {
    let (mut svm, payer, mint) = setup();
    send_ok(&mut svm, &payer, ix_init_registry(&payer.pubkey()));
    send_ok(&mut svm, &payer, ix_register_mint(&payer.pubkey(), &mint));

    let record = read_record(&svm, &mint);
    assert_eq!(record.mint, mint);
    assert_eq!(&record.symbol[..record.symbol_len as usize], SYMBOL.as_bytes());
    assert_eq!(record.decimals, DECIMALS);
    assert_eq!(record.base_multiplier_bits, BASE_MULTIPLIER.to_bits());
    assert_eq!(record.live_multiplier_bits, LIVE_MULTIPLIER.to_bits());
    assert_eq!(
        record.live_multiplier_fp,
        token2022::to_fixed_point(LIVE_MULTIPLIER).unwrap()
    );
    assert_eq!(record.live_effective_at, EFFECTIVE_TIMESTAMP);
    assert_eq!(record.activations, 0);
    assert_eq!(read_registry(&svm).mints, 1);

    // The point of keeping both: a reader can see that the field the mint names `multiplier` is
    // not the multiplier, and by how much.
    assert_ne!(record.base_multiplier_bits, record.live_multiplier_bits);
}

#[test]
fn register_mint_refuses_an_account_token_2022_does_not_own() {
    let (mut svm, payer, _) = setup();
    send_ok(&mut svm, &payer, ix_init_registry(&payer.pubkey()));

    let impostor = Pubkey::new_unique();
    svm.set_account(
        impostor,
        Account {
            lamports: 1_000_000_000,
            data: MINT_BYTES.to_vec(),
            owner: system_program::ID,
            executable: false,
            rent_epoch: 0,
        },
    )
    .unwrap();

    expect_anchor_error(
        &mut svm,
        &payer,
        ix_register_mint(&payer.pubkey(), &impostor),
        RecordDateError::NotToken2022,
    );
}

#[test]
fn record_activation_refuses_when_the_multiplier_has_not_moved() {
    let (mut svm, payer, mint) = setup();
    send_ok(&mut svm, &payer, ix_init_registry(&payer.pubkey()));
    send_ok(&mut svm, &payer, ix_register_mint(&payer.pubkey(), &mint));

    // register_mint stored the live multiplier, so there is nothing new to record.
    expect_anchor_error(
        &mut svm,
        &payer,
        ix_record_activation(&payer.pubkey(), &mint),
        RecordDateError::NothingToRecord,
    );
}

#[test]
fn record_activation_writes_a_receipt_when_the_multiplier_moves() {
    let (mut svm, payer, mint) = setup();
    send_ok(&mut svm, &payer, ix_init_registry(&payer.pubkey()));
    send_ok(&mut svm, &payer, ix_register_mint(&payer.pubkey(), &mint));

    // A second corporate action: the live multiplier becomes the previous one, and a higher
    // value takes over. This is exactly the shape the issuer ships.
    let second = LIVE_MULTIPLIER * 1.004;
    let second_at = NOW - 60;
    set_multiplier(&mut svm, &mint, LIVE_MULTIPLIER, second, second_at);

    send_ok(&mut svm, &payer, ix_record_activation(&payer.pubkey(), &mint));

    let receipt = read_receipt(&svm, &mint, 0);
    assert_eq!(receipt.mint, mint);
    assert_eq!(receipt.sequence, 0);
    assert_eq!(receipt.previous_bits, LIVE_MULTIPLIER.to_bits());
    assert_eq!(receipt.new_bits, second.to_bits());
    assert_eq!(receipt.effective_at, second_at);
    assert_eq!(receipt.raw_supply, SUPPLY);
    assert!(!receipt.is_flat(), "the receipt records a real change");

    let record = read_record(&svm, &mint);
    assert_eq!(record.activations, 1);
    assert_eq!(record.live_multiplier_bits, second.to_bits());
    assert_eq!(read_registry(&svm).activations, 1);
}

#[test]
fn a_second_activation_gets_its_own_receipt_in_sequence() {
    let (mut svm, payer, mint) = setup();
    send_ok(&mut svm, &payer, ix_init_registry(&payer.pubkey()));
    send_ok(&mut svm, &payer, ix_register_mint(&payer.pubkey(), &mint));

    let second = LIVE_MULTIPLIER * 1.004;
    set_multiplier(&mut svm, &mint, LIVE_MULTIPLIER, second, NOW - 120);
    send_ok(&mut svm, &payer, ix_record_activation(&payer.pubkey(), &mint));

    let third = second * 1.003;
    set_multiplier(&mut svm, &mint, second, third, NOW - 60);
    // Sequence 1, so the PDA is a different account.
    let ix = Instruction {
        program_id: record_date::ID,
        accounts: vec![
            AccountMeta::new(payer.pubkey(), true),
            AccountMeta::new(registry_pda().0, false),
            AccountMeta::new_readonly(mint, false),
            AccountMeta::new(token_record_pda(&mint).0, false),
            AccountMeta::new(receipt_pda(&mint, 1).0, false),
            AccountMeta::new_readonly(system_program::ID, false),
        ],
        data: record_date::instruction::RecordActivation {}.data(),
    };
    send_ok(&mut svm, &payer, ix);

    let first = read_receipt(&svm, &mint, 0);
    let second_receipt = read_receipt(&svm, &mint, 1);
    assert_eq!(first.sequence, 0);
    assert_eq!(second_receipt.sequence, 1);
    assert_eq!(second_receipt.previous_bits, second.to_bits());
    assert_eq!(second_receipt.new_bits, third.to_bits());
    assert_eq!(read_record(&svm, &mint).activations, 2);
    assert_eq!(read_registry(&svm).activations, 2);
}

#[test]
fn read_entitlement_scales_a_raw_amount() {
    let (mut svm, payer, mint) = setup();
    send_ok(&mut svm, &payer, ix_init_registry(&payer.pubkey()));
    send_ok(&mut svm, &payer, ix_register_mint(&payer.pubkey(), &mint));

    let raw: u64 = 4_000_000_000;
    let meta = send_ok(
        &mut svm,
        &payer,
        ix_read(&mint, RecordDateInstruction::Entitlement(raw)),
    );

    let returned = meta.return_data.data;
    assert_eq!(returned.len(), 16, "a u128 is sixteen bytes");
    let mut bytes = [0u8; 16];
    bytes.copy_from_slice(&returned);
    let scaled = u128::from_le_bytes(bytes);

    let expected = token2022::scaled_amount(raw, token2022::to_fixed_point(LIVE_MULTIPLIER).unwrap())
        .unwrap();
    assert_eq!(scaled, expected);
    assert!(scaled > raw as u128, "the multiplier is above one, so the scaled amount is larger");
}

#[test]
fn activation_pending_answers_one_only_after_a_move() {
    let (mut svm, payer, mint) = setup();
    send_ok(&mut svm, &payer, ix_init_registry(&payer.pubkey()));
    send_ok(&mut svm, &payer, ix_register_mint(&payer.pubkey(), &mint));

    let meta = send_ok(&mut svm, &payer, ix_read(&mint, RecordDateInstruction::Pending));
    assert_eq!(meta.return_data.data, vec![0], "nothing has moved yet");

    set_multiplier(&mut svm, &mint, LIVE_MULTIPLIER, LIVE_MULTIPLIER * 1.004, NOW - 30);
    let meta = send_ok(&mut svm, &payer, ix_read(&mint, RecordDateInstruction::Pending));
    assert_eq!(meta.return_data.data, vec![1], "the multiplier moved");

    send_ok(&mut svm, &payer, ix_record_activation(&payer.pubkey(), &mint));
    let meta = send_ok(&mut svm, &payer, ix_read(&mint, RecordDateInstruction::Pending));
    assert_eq!(meta.return_data.data, vec![0], "and now it is recorded");
}

#[test]
fn settlement_window_reports_seconds_since_the_activation() {
    let (mut svm, payer, mint) = setup();
    send_ok(&mut svm, &payer, ix_init_registry(&payer.pubkey()));
    send_ok(&mut svm, &payer, ix_register_mint(&payer.pubkey(), &mint));

    // The mint's activation is the one already in the fixture, six days before NOW.
    let meta = send_ok(&mut svm, &payer, ix_read(&mint, RecordDateInstruction::Window));
    let mut bytes = [0u8; 8];
    bytes.copy_from_slice(&meta.return_data.data);
    let delta = i64::from_le_bytes(bytes);
    assert_eq!(delta, NOW - EFFECTIVE_TIMESTAMP);
    assert!(
        delta.abs() > RECOMMENDED_PAUSE_SECS,
        "six days is well outside the recommended pause"
    );

    // Now put an activation inside the pause and check the sign flips.
    let just_after = NOW - (RECOMMENDED_PAUSE_SECS - 60);
    set_multiplier(&mut svm, &mint, LIVE_MULTIPLIER, LIVE_MULTIPLIER * 1.004, just_after);
    let meta = send_ok(&mut svm, &payer, ix_read(&mint, RecordDateInstruction::Window));
    let mut bytes = [0u8; 8];
    bytes.copy_from_slice(&meta.return_data.data);
    let delta = i64::from_le_bytes(bytes);
    assert_eq!(delta, RECOMMENDED_PAUSE_SECS - 60);
    assert!(delta.abs() <= RECOMMENDED_PAUSE_SECS);
}
