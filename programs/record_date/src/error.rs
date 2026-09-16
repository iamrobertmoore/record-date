use anchor_lang::prelude::*;

#[error_code]
pub enum RecordDateError {
    #[msg("the mint account is not owned by the Token-2022 program")]
    NotToken2022,
    #[msg("the account is too short to be a Token-2022 mint")]
    MintTooShort,
    #[msg("the bytes between the mint base state and the account-type byte are not zero")]
    MintPaddingNotZero,
    #[msg("the account-type byte does not say this account is a mint")]
    NotAMint,
    #[msg("the mint has no scaled-ui-amount extension, so it has no multiplier")]
    NoScaledUiAmount,
    #[msg("the scaled-ui-amount extension is not the length Token-2022 defines for it")]
    BadExtensionLength,
    #[msg("an extension type in the account is not in the Token-2022 ExtensionType enum")]
    UnknownExtension,
    #[msg("an extension entry claims a body that does not fit inside the account")]
    ExtensionOverrunsAccount,
    #[msg("the multiplier is not a usable number")]
    BadMultiplier,
    #[msg("the symbol is longer than 12 bytes")]
    SymbolTooLong,
    #[msg("this mint is not registered")]
    NotRegistered,
    #[msg("the multiplier has not moved since this program last recorded it")]
    NothingToRecord,
}
