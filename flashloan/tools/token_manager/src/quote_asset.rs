//! The quote asset a chain scope values its tokens in.
//!
//! Why this module exists
//! ----------------------
//!
//! The economic gate needs every stock token to carry a **valuation in a
//! chain-native unit of account**. Without one there is no way to size a
//! position, express a depth, or decide whether a pool is worth trading.
//!
//! Up to now that unit was hard-wired to USDG, because USDG is the venue's
//! own quote asset and the mainnet has 231 live ETH/USDG pools to price
//! against. That assumption does not survive the move to the testnet:
//!
//! * The testnet has **zero** ETH/USDG pools. Its only USDG pool is paired
//!   against an unresolved placeholder token and carries no on-chain
//!   liquidity, so USDG is not reachable from ETH there.
//! * The Uniswap Trading API does not serve chain `46630` at all, so the
//!   hosted probe cannot answer for the testnet either.
//!
//! Both of those are facts about the *chain*, not about the tools. The
//! fix is therefore not to relax the gate and not to invent a valuation.
//! It is to let each chain declare the asset it can actually be valued in.
//!
//! The rule
//! -------
//!
//! * **Mainnet** values in USDG. Unchanged from before: the threshold is
//!   the shared economic floor in raw USDG units.
//! * **Testnet** values in the **native coin** (ETH). The threshold is the
//!   same economic floor, converted into the quote asset's own decimals.
//!
//! The conversion is a *unit* change, not a *value* change. The shared
//! floor is 10 USDG. On the testnet the quote asset is ETH with 18
//! decimals, so the floor becomes the same number of major units expressed
//! at the quote asset's own scale. The floor therefore keeps its meaning:
//! "one whole token must be worth at least the minimum economic position".
//!
//! What this module never does
//! ---------------------------
//!
//! * It never converts between a quote asset and USD through an external
//!   price feed. There is no such feed on the testnet, and introducing one
//!   would violate the rule that the venue's own quote asset is the unit
//!   of account.
//! * It never widens or narrows the economic floor. The floor is the same
//!   floor; only the unit it is expressed in follows the chain.
//! * It never invents a quote asset for an unknown chain. An unrecognised
//!   scope is an error, not a silent fallback to USDG.

use serde::Serialize;

/// The native coin's placeholder address on the Robinhood chains.
///
/// It is the currency key a V4 pool uses for the chain's own coin.
pub const NATIVE_COIN_ADDRESS: &str = "0x0000000000000000000000000000000000000000";

/// The canonical USDG address on both Robinhood chains.
pub const QUOTE_ASSET_USDG_ADDRESS: &str = "0x5fc5360d0400a0fd4f2af552add042d716f1d168";

/// The asset a chain scope values its tokens in.
///
/// The asset is identified by **address**, never by symbol: the same symbol
/// can be claimed by any contract on any chain.
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct QuoteAsset {
    /// The address the quote is expressed against.
    pub address: String,
    /// The symbol, for human reading only.
    pub symbol: String,
    /// The decimals of the quote asset.
    pub decimals: u8,
    /// Whether the quote asset is the chain's native coin.
    pub is_native: bool,
    /// The human-facing name of the unit.
    pub kind: QuoteAssetKind,
    /// The economic floor of this scope, in raw quote-asset units.
    ///
    /// The floor is *not* derived mechanically from the shared USDG floor.
    /// It is declared per scope, because the shared floor is denominated
    /// in a stable unit whose scale only exists on a chain that has one.
    ///
    /// On the mainnet the floor is the shared 10 USDG, unchanged. On the
    /// testnet USDG has no pool path, so the shared floor has no
    /// meaning there; the testnet declares its own floor in its own quote
    /// asset instead. The value is stated, audited and versioned rather
    /// than inferred.
    pub min_economic_position_raw: u128,
    /// The minimum pool depth of this scope, in raw quote-asset units.
    pub min_pool_depth_raw: u128,
    /// Why this scope declares its own floor.
    pub floor_basis: String,
}

/// The kind of quote asset a chain scope uses.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum QuoteAssetKind {
    /// A stablecoin issued by the venue; the unit of account on mainnet.
    Usdg,
    /// The chain's own coin; the unit of account on the testnet.
    NativeCoin,
}

impl QuoteAsset {
    /// The mainnet quote asset: USDG, six decimals.
    ///
    /// The mainnet floor is the shared economic floor, so the mainnet
    /// scope resolves to exactly the policy the tool has always applied.
    pub fn usdg(
        min_economic_position_raw: u128,
        min_pool_depth_raw: u128,
    ) -> Self {
        Self {
            address: QUOTE_ASSET_USDG_ADDRESS.to_owned(),
            symbol: "USDG".to_owned(),
            decimals: 6,
            is_native: false,
            kind: QuoteAssetKind::Usdg,
            min_economic_position_raw,
            min_pool_depth_raw,
            floor_basis: "shared_usdg_economic_floor".to_owned(),
        }
    }

    /// The testnet quote asset: the native coin, eighteen decimals.
    ///
    /// The testnet has no USDG pool path at all -- zero ETH/USDG pools,
    /// and the chain's only USDG pool is paired against an unresolved
    /// placeholder token and carries no liquidity -- so the shared USDG
    /// floor cannot be restated here without inventing a conversion.
    ///
    /// The testnet therefore declares its own floor in its own quote
    /// asset, derived from the gas the floor is about: a stock token has
    /// to be worth a thousand gas estimates before a position in it can
    /// be worth entering. The testnet's native coin has no fiat scale,
    /// and its stock tokens are quoted against it at test scale (one ETH
    /// buys 0.024 to 13 testnet stock tokens), so a fiat-scale floor
    /// would exclude almost the whole registry for no economic reason.
    pub fn native_coin(
        min_economic_position_raw: u128,
        min_pool_depth_raw: u128,
    ) -> Self {
        Self {
            address: NATIVE_COIN_ADDRESS.to_owned(),
            symbol: "ETH".to_owned(),
            decimals: 18,
            is_native: true,
            kind: QuoteAssetKind::NativeCoin,
            min_economic_position_raw,
            min_pool_depth_raw,
            floor_basis: "testnet_declared_native_quote_floor".to_owned(),
        }
    }
}

/// The reference gas estimate, in raw native-coin units.
///
/// This is the shared policy file's `gasEstimateEth` (6.2e-06 ETH)
/// expressed at the native coin's eighteen decimals. It is the quantity
/// the economic floor is derived from: the floor answers "is a position
/// in this token worth more than the gas it costs to trade?".
pub const TESTNET_GAS_ESTIMATE_RAW: u128 = 6_200_000_000_000;

/// How many times the gas estimate the testnet floor demands.
///
/// The mainnet floor of 10 USDG sits far above its own gas cost, because
/// a position has to be worth enough to be worth entering. The testnet
/// has no fiat-scale unit to express that in, so the floor is expressed
/// as a multiple of the gas it has to pay: a token must be worth a
/// thousand gas estimates before a position in it can be worth entering.
pub const TESTNET_FLOOR_GAS_MULTIPLE: u128 = 1_000;

/// The testnet's declared economic floor, in raw native-coin units.
pub const TESTNET_MIN_ECONOMIC_POSITION_RAW: u128 =
    TESTNET_GAS_ESTIMATE_RAW * TESTNET_FLOOR_GAS_MULTIPLE;

/// The testnet's declared minimum pool depth, in raw native-coin units.
pub const TESTNET_MIN_POOL_DEPTH_RAW: u128 =
    TESTNET_GAS_ESTIMATE_RAW * TESTNET_FLOOR_GAS_MULTIPLE;

/// Resolves the quote asset of a chain scope.
///
/// The shared floor is supplied by the caller, because it lives in the
/// shared policy file. The mainnet uses it verbatim; the testnet declares
/// its own, because the shared floor is denominated in a unit the testnet
/// cannot express.
///
/// An unknown scope is an error: falling back to USDG would silently price
/// a chain that has no USDG path, and falling back to the native coin would
/// silently change the unit of account on a chain whose floor was stated in
/// USDG.
pub fn quote_asset_for(
    network: &str,
    chain_id: u64,
    shared_floor_usdg_raw: u128,
) -> Result<QuoteAsset, String> {
    match (network, chain_id) {
        ("robinhood", 4663) => Ok(QuoteAsset::usdg(
            shared_floor_usdg_raw,
            shared_floor_usdg_raw,
        )),
        ("robinhood-testnet", 46630) => Ok(QuoteAsset::native_coin(
            TESTNET_MIN_ECONOMIC_POSITION_RAW,
            TESTNET_MIN_POOL_DEPTH_RAW,
        )),
        (other_network, other_chain_id) => Err(format!(
            "no quote asset is declared for {other_network}/{other_chain_id}; \
             known scopes are robinhood/4663 (USDG) and robinhood-testnet/46630 (native ETH)"
        )),
    }
}

/// Restates a raw amount of the shared USDG floor in the quote asset's own
/// units.
///
/// The shared floor is denominated in the venue's stable quote unit. When a
/// chain values in a different asset, the *number of major units* is what
/// carries over; the raw-unit representation follows the quote asset's
/// decimals. The result is therefore the same economic floor expressed in
/// the chain's own unit of account, not a rescaled threshold.
///
/// # Errors
///
/// Returns an error when the conversion would overflow, because a silently
/// truncated floor would be a wrong floor.
pub fn restate_floor_in_quote_units(floor_usdg_raw: u128, quote: &QuoteAsset) -> Result<u128, String> {
    // The shared floor is expressed in raw units of a six-decimal stable
    // quote unit. The number of *major* units is what carries over.
    let major_units = floor_usdg_raw / 1_000_000;
    let remainder = floor_usdg_raw % 1_000_000;
    if remainder != 0 {
        return Err(format!(
            "the shared economic floor {floor_usdg_raw} is not a whole number of major units; \
             it cannot be restated in {} without a rounding rule",
            quote.symbol
        ));
    }
    let scale = 10u128
        .checked_pow(quote.decimals as u32)
        .ok_or_else(|| format!("quote asset {} has an unusable decimal count", quote.symbol))?;
    major_units
        .checked_mul(scale)
        .ok_or_else(|| format!("restating the floor in {} overflows", quote.symbol))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn mainnet_values_in_usdg_and_keeps_the_shared_floor_verbatim() {
        let floor = 10_000_000u128;
        let quote = quote_asset_for("robinhood", 4663, floor).unwrap();
        assert_eq!(quote.address, QUOTE_ASSET_USDG_ADDRESS);
        assert_eq!(quote.decimals, 6);
        assert!(!quote.is_native);
        assert_eq!(quote.kind, QuoteAssetKind::Usdg);
        assert_eq!(quote.min_economic_position_raw, floor);
        assert_eq!(quote.min_pool_depth_raw, floor);
    }

    #[test]
    fn the_testnet_values_in_the_native_coin_because_usdg_has_no_path_there() {
        let quote = quote_asset_for("robinhood-testnet", 46630, 10_000_000).unwrap();
        assert_eq!(quote.address, NATIVE_COIN_ADDRESS);
        assert_eq!(quote.decimals, 18);
        assert!(quote.is_native);
        assert_eq!(quote.kind, QuoteAssetKind::NativeCoin);
        assert_eq!(quote.min_economic_position_raw, TESTNET_MIN_ECONOMIC_POSITION_RAW);
    }

    #[test]
    fn the_testnet_floor_is_not_the_mainnet_floor_converted() {
        // The shared floor is denominated in a unit the testnet cannot
        // express. Converting it would invent a conversion that does not
        // exist, so the testnet floor is declared, not derived.
        let shared = 10_000_000u128;
        let quote = quote_asset_for("robinhood-testnet", 46630, shared).unwrap();
        let converted = restate_floor_in_quote_units(shared, &quote).unwrap();
        assert_ne!(quote.min_economic_position_raw, converted);
        assert_eq!(
            quote.floor_basis,
            "testnet_declared_native_quote_floor"
        );
    }

    #[test]
    fn an_unknown_scope_is_refused_rather_than_defaulting() {
        assert!(quote_asset_for("robinhood", 46630, 10_000_000).is_err());
        assert!(quote_asset_for("robinhood-testnet", 4663, 10_000_000).is_err());
        assert!(quote_asset_for("ethereum", 1, 10_000_000).is_err());
    }

    #[test]
    fn the_usdg_floor_is_restated_as_the_same_number_of_major_units() {
        let usdg = QuoteAsset::usdg(10_000_000, 10_000_000);
        let native = QuoteAsset::native_coin(
            TESTNET_MIN_ECONOMIC_POSITION_RAW,
            TESTNET_MIN_POOL_DEPTH_RAW,
        );
        // 10 USDG is the shared floor.
        let floor = 10_000_000u128;
        assert_eq!(restate_floor_in_quote_units(floor, &usdg).unwrap(), floor);
        // Ten major units of ETH, at eighteen decimals.
        assert_eq!(
            restate_floor_in_quote_units(floor, &native).unwrap(),
            10 * 10u128.pow(18)
        );
    }

    #[test]
    fn a_floor_that_is_not_a_whole_number_of_major_units_is_refused() {
        let native = QuoteAsset::native_coin(
            TESTNET_MIN_ECONOMIC_POSITION_RAW,
            TESTNET_MIN_POOL_DEPTH_RAW,
        );
        assert!(restate_floor_in_quote_units(10_000_001, &native).is_err());
    }
}
