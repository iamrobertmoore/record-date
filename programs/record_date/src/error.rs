use anchor_lang::prelude::*;

// Anchor assigns these codes positionally, starting at 6000, so the order is part of the
// program's public interface: `StalePythPrice` is 6016 and 6016 is quoted in the README, on the
// desk page and in the build checks that refuse to publish if it moves. **Add new variants at
// the end.** Inserting one in the middle renumbers everything below it and breaks a documented
// claim without breaking anything that runs.
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
    #[msg("the price update account is not owned by the Pyth receiver program")]
    NotPythReceiver,
    #[msg("the account is not a Pyth PriceUpdateV2")]
    NotAPriceUpdate,
    #[msg("the account is shorter than a PriceUpdateV2, so it cannot be parsed")]
    PriceUpdateTooShort,
    #[msg("the Pyth price is for a different feed than the one bound to this mint")]
    FeedIdMismatch,
    #[msg("the Pyth price is older than this program will settle against")]
    StalePythPrice,
    #[msg("the price differs from Pyth's by more than the tolerance")]
    PriceDeviation,
    #[msg("the price is not a number this program can represent")]
    PriceOutOfRange,
    #[msg("the Pyth price is negative")]
    NegativePythPrice,
    #[msg("the feed symbol is longer than 12 bytes")]
    FeedSymbolTooLong,
    #[msg("the caller asked for a price age longer than this program will accept")]
    PriceAgeCeilingExceeded,
    #[msg("the account ends in a partial extension header")]
    TruncatedExtensionHeader,
}
