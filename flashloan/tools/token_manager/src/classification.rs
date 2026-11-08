use crate::metadata::{MetadataSourceKind, NormalizedTokenMetadata};
use crate::model::{AdmissionStatus, LibraryKey, TokenWrite};
use crate::sources::{
    mainnet_core4_allowlist, V4PoolCurrencyReference, V4PoolLiquidityEvidence, ROBINHOOD_CHAIN_ID,
    ROBINHOOD_NETWORK, ROBINHOOD_TESTNET_CHAIN_ID, ROBINHOOD_TESTNET_NETWORK,
};
use manager_contracts::normalize_address;
use serde::Serialize;
use std::collections::{BTreeMap, BTreeSet};

pub const ZERO_ADDRESS: &str = "0x0000000000000000000000000000000000000000";
const MAX_CANDIDATE_FIXED_FEE: i64 = 3000;

pub const TESTNET_TEST_TOKEN_ADDRESSES: [&str; 1] = ["0x05a31b34a1bf8edb08cbfa2e9d860e6d0939313e"];

pub const MAINNET_TEST_TOKEN_CANDIDATES: [(&str, &str, &str); 3] = [
    (
        "TZ",
        "0xa8cf7c51eee671646ba440ffa180f970902fd2fb",
        "mainnet-v4-amm-create-tokens",
    ),
    (
        "TA",
        "0xd1b532ea8dbda84e6937fedabb2fd5c33a049830",
        "mainnet-v4-amm-create-tokens",
    ),
    (
        "TB",
        "0x02a0bacc642d501422af804a462f98335b2bbf09",
        "mainnet-v4-amm-create-tokens",
    ),
];

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ClassificationPolicy {
    pub network: String,
    pub chain_id: u64,
    pub core_addresses: BTreeSet<String>,
    pub test_addresses: BTreeSet<String>,
}

impl ClassificationPolicy {
    pub fn new(
        network: impl Into<String>,
        chain_id: u64,
        core_addresses: impl IntoIterator<Item = String>,
        test_addresses: impl IntoIterator<Item = String>,
    ) -> Self {
        Self {
            network: network.into(),
            chain_id,
            core_addresses: core_addresses.into_iter().collect(),
            test_addresses: test_addresses.into_iter().collect(),
        }
    }

    pub fn mainnet() -> Self {
        let core_addresses = mainnet_core4_allowlist()
            .into_iter()
            .filter_map(|record| record.token_address)
            .filter_map(|address| normalize_address(&address).ok());
        Self::new(
            ROBINHOOD_NETWORK,
            ROBINHOOD_CHAIN_ID,
            core_addresses,
            std::iter::once(MAINNET_TEST_TOKEN_CANDIDATES[0].1.to_owned()),
        )
    }

    pub fn testnet_test_policy() -> Self {
        Self::new(
            ROBINHOOD_TESTNET_NETWORK,
            ROBINHOOD_TESTNET_CHAIN_ID,
            std::iter::empty(),
            testnet_test_token_addresses(),
        )
    }
}

pub fn testnet_test_token_addresses() -> BTreeSet<String> {
    TESTNET_TEST_TOKEN_ADDRESSES
        .iter()
        .map(|address| (*address).to_owned())
        .collect()
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum ExtensionAnchor {
    CoreToken,
    StockToken,
    TestToken,
}

impl ExtensionAnchor {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::CoreToken => "CORE_TOKEN",
            Self::StockToken => "STOCK_TOKEN",
            Self::TestToken => "TEST_TOKEN",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum ClassificationReason {
    CoreAllowlist,
    StockRegistry,
    TestAllowlist,
    ExtensionMember,
    NoLibraryMembership,
    SourceConflict,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum ClassificationIssueCode {
    ChainMismatch,
    InvalidPoolCurrency,
    AmbiguousSourceMetadata,
}

impl ClassificationIssueCode {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::ChainMismatch => "CHAIN_MISMATCH",
            Self::InvalidPoolCurrency => "INVALID_POOL_CURRENCY",
            Self::AmbiguousSourceMetadata => "AMBIGUOUS_SOURCE_METADATA",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ClassificationIssue {
    pub code: ClassificationIssueCode,
    pub token_address: Option<String>,
    pub source_locator: Option<String>,
    pub message: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LibraryMembership {
    pub library: LibraryKey,
    pub admission_status: AdmissionStatus,
    pub reason: ClassificationReason,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ClassifiedToken {
    pub token: NormalizedTokenMetadata,
    pub primary_library: Option<LibraryKey>,
    pub memberships: Vec<LibraryMembership>,
    pub extension_anchors: Vec<ExtensionAnchor>,
    pub source_inputs: Vec<MetadataSourceKind>,
}

impl ClassifiedToken {
    pub fn extension_member(&self) -> bool {
        self.memberships
            .iter()
            .any(|membership| membership.library == LibraryKey::Extension)
    }

    pub fn to_token_writes(&self) -> Vec<TokenWrite> {
        self.memberships
            .iter()
            .map(|membership| self.token.to_token_write(membership.admission_status))
            .collect()
    }

    pub fn to_token_writes_for_library(&self, library: LibraryKey) -> Vec<TokenWrite> {
        self.memberships
            .iter()
            .filter(|membership| membership.library == library)
            .map(|membership| self.token.to_token_write(membership.admission_status))
            .collect()
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DuplicateSymbolGroup {
    pub symbol: String,
    pub token_key_ids: Vec<String>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct ClassificationResult {
    pub tokens: Vec<ClassifiedToken>,
    pub issues: Vec<ClassificationIssue>,
    pub duplicate_symbols: Vec<DuplicateSymbolGroup>,
    pub source_overrides: usize,
}

impl ClassificationResult {
    pub fn primary_count_for_library(&self, library: LibraryKey) -> usize {
        self.tokens
            .iter()
            .filter(|token| token.primary_library == Some(library))
            .count()
    }

    pub fn membership_count_for_library(&self, library: LibraryKey) -> usize {
        self.tokens
            .iter()
            .filter(|token| {
                token
                    .memberships
                    .iter()
                    .any(|membership| membership.library == library)
            })
            .count()
    }

    pub fn unclassified_count(&self) -> usize {
        self.tokens
            .iter()
            .filter(|token| token.memberships.is_empty())
            .count()
    }

    pub fn extension_member_count(&self) -> usize {
        self.tokens
            .iter()
            .filter(|token| token.extension_member())
            .count()
    }

    pub fn writes_by_library(&self) -> BTreeMap<LibraryKey, Vec<TokenWrite>> {
        [
            LibraryKey::Core,
            LibraryKey::Stocks,
            LibraryKey::Test,
            LibraryKey::Extension,
        ]
        .into_iter()
        .map(|library| {
            let writes = self
                .tokens
                .iter()
                .flat_map(|token| token.to_token_writes_for_library(library))
                .collect();
            (library, writes)
        })
        .collect()
    }
}

#[derive(Debug)]
struct MergedToken {
    token: NormalizedTokenMetadata,
    source_inputs: Vec<MetadataSourceKind>,
    source_conflict: bool,
    primary_library: Option<LibraryKey>,
    primary_reason: ClassificationReason,
}

pub fn classify_tokens(
    records: &[NormalizedTokenMetadata],
    pool_references: &[V4PoolCurrencyReference],
    policy: &ClassificationPolicy,
) -> ClassificationResult {
    classify_tokens_internal(records, pool_references, None, policy)
}

pub fn classify_tokens_with_liquidity(
    records: &[NormalizedTokenMetadata],
    pool_references: &[V4PoolCurrencyReference],
    pool_liquidity: &[V4PoolLiquidityEvidence],
    policy: &ClassificationPolicy,
) -> ClassificationResult {
    classify_tokens_internal(records, pool_references, Some(pool_liquidity), policy)
}

fn classify_tokens_internal(
    records: &[NormalizedTokenMetadata],
    pool_references: &[V4PoolCurrencyReference],
    pool_liquidity: Option<&[V4PoolLiquidityEvidence]>,
    policy: &ClassificationPolicy,
) -> ClassificationResult {
    let mut issues = Vec::new();
    let mut grouped = BTreeMap::<String, Vec<NormalizedTokenMetadata>>::new();
    for record in records {
        if record.token_key_id.network() != policy.network
            || record.token_key_id.chain_id() != policy.chain_id
        {
            issues.push(ClassificationIssue {
                code: ClassificationIssueCode::ChainMismatch,
                token_address: Some(record.token_address.clone()),
                source_locator: Some(record.source_locator.clone()),
                message: format!(
                    "{}:{} is outside classification target {}:{}",
                    record.token_key_id.network(),
                    record.token_key_id.chain_id(),
                    policy.network,
                    policy.chain_id
                ),
            });
            continue;
        }
        grouped
            .entry(record.token_address.clone())
            .or_default()
            .push(record.clone());
    }

    let mut merged = Vec::with_capacity(grouped.len());
    let mut source_overrides = 0_usize;
    for (address, mut candidates) in grouped {
        candidates.sort_by(|left, right| {
            right
                .source
                .precedence()
                .cmp(&left.source.precedence())
                .then_with(|| left.source_locator.cmp(&right.source_locator))
                .then_with(|| left.metadata_checksum.cmp(&right.metadata_checksum))
        });
        let selected = candidates[0].clone();
        source_overrides += candidates.len().saturating_sub(1);
        let top_precedence = selected.source.precedence();
        let top_checksums = candidates
            .iter()
            .filter(|candidate| candidate.source.precedence() == top_precedence)
            .map(|candidate| candidate.metadata_checksum.as_str())
            .collect::<BTreeSet<_>>();
        let source_conflict = top_checksums.len() > 1;
        if source_conflict {
            issues.push(ClassificationIssue {
                code: ClassificationIssueCode::AmbiguousSourceMetadata,
                token_address: Some(address.clone()),
                source_locator: Some(selected.source_locator.clone()),
                message: format!("multiple {} records disagree", selected.source),
            });
        }
        let source_inputs = candidates
            .iter()
            .map(|candidate| candidate.source)
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect::<Vec<_>>();
        let present_in_stock_registry = source_inputs.contains(&MetadataSourceKind::StockRegistry);
        let (primary_library, primary_reason) = if source_conflict {
            (None, ClassificationReason::SourceConflict)
        } else if policy.core_addresses.contains(&address) {
            (Some(LibraryKey::Core), ClassificationReason::CoreAllowlist)
        } else if policy.test_addresses.contains(&address) {
            (Some(LibraryKey::Test), ClassificationReason::TestAllowlist)
        } else if present_in_stock_registry {
            (
                Some(LibraryKey::Stocks),
                ClassificationReason::StockRegistry,
            )
        } else {
            (None, ClassificationReason::NoLibraryMembership)
        };
        merged.push(MergedToken {
            token: selected,
            source_inputs,
            source_conflict,
            primary_library,
            primary_reason,
        });
    }
    merged.sort_by(|left, right| left.token.token_address.cmp(&right.token.token_address));

    let stock_addresses = merged
        .iter()
        .filter(|token| {
            token
                .source_inputs
                .contains(&MetadataSourceKind::StockRegistry)
        })
        .map(|token| token.token.token_address.clone())
        .collect::<BTreeSet<_>>();
    let liquidity_by_pool = pool_liquidity
        .map(|evidence| {
            evidence
                .iter()
                .filter(|evidence| {
                    evidence.network == policy.network
                        && evidence.chain_id == policy.chain_id
                        && evidence.protocol == "uniswap_v4"
                })
                .map(|evidence| (evidence.pool_id.clone(), evidence))
                .collect::<BTreeMap<_, _>>()
        })
        .unwrap_or_default();
    let extension_anchors = extension_memberships(
        pool_references,
        if pool_liquidity.is_some() {
            Some(&liquidity_by_pool)
        } else {
            None
        },
        policy,
        &stock_addresses,
        &mut issues,
    );

    let mut tokens = Vec::with_capacity(merged.len());
    for merged_token in merged {
        let anchors = extension_anchors
            .get(&merged_token.token.token_address)
            .cloned()
            .unwrap_or_default()
            .into_iter()
            .collect::<Vec<_>>();
        let mut memberships = Vec::new();
        if let Some(primary_library) = merged_token.primary_library {
            memberships.push(LibraryMembership {
                library: primary_library,
                admission_status: AdmissionStatus::Admitted,
                reason: merged_token.primary_reason,
            });
        }
        if !anchors.is_empty() && !merged_token.source_conflict {
            memberships.push(LibraryMembership {
                library: LibraryKey::Extension,
                admission_status: AdmissionStatus::CandidateOnly,
                reason: ClassificationReason::ExtensionMember,
            });
        }
        tokens.push(ClassifiedToken {
            token: merged_token.token,
            primary_library: merged_token.primary_library,
            memberships,
            extension_anchors: anchors,
            source_inputs: merged_token.source_inputs,
        });
    }
    tokens.sort_by(|left, right| left.token.token_address.cmp(&right.token.token_address));
    issues.sort_by(|left, right| {
        left.token_address
            .cmp(&right.token_address)
            .then_with(|| left.code.as_str().cmp(right.code.as_str()))
    });

    let mut symbols = BTreeMap::<String, BTreeSet<String>>::new();
    for token in tokens.iter().filter(|token| !token.memberships.is_empty()) {
        symbols
            .entry(token.token.token_symbol.clone())
            .or_default()
            .insert(token.token.token_key_id.to_string());
    }
    let mut duplicate_symbols = symbols
        .into_iter()
        .filter(|(_, token_key_ids)| token_key_ids.len() > 1)
        .map(|(symbol, token_key_ids)| DuplicateSymbolGroup {
            symbol,
            token_key_ids: token_key_ids.into_iter().collect(),
        })
        .collect::<Vec<_>>();
    duplicate_symbols.sort_by(|left, right| left.symbol.cmp(&right.symbol));

    ClassificationResult {
        tokens,
        issues,
        duplicate_symbols,
        source_overrides,
    }
}

fn extension_memberships(
    references: &[V4PoolCurrencyReference],
    liquidity_by_pool: Option<&BTreeMap<String, &V4PoolLiquidityEvidence>>,
    policy: &ClassificationPolicy,
    stock_addresses: &BTreeSet<String>,
    issues: &mut Vec<ClassificationIssue>,
) -> BTreeMap<String, BTreeSet<ExtensionAnchor>> {
    let mut extension = BTreeMap::<String, BTreeSet<ExtensionAnchor>>::new();
    let mut pools = BTreeMap::<String, (Option<String>, Option<String>)>::new();
    for reference in references {
        if reference.network != policy.network || reference.chain_id != policy.chain_id {
            issues.push(ClassificationIssue {
                code: ClassificationIssueCode::ChainMismatch,
                token_address: reference.token_address.clone(),
                source_locator: Some(reference.source_locator.clone()),
                message: format!(
                    "{}:{} is outside classification target {}:{}",
                    reference.network, reference.chain_id, policy.network, policy.chain_id
                ),
            });
            continue;
        }
        let normalized = reference
            .token_address
            .as_deref()
            .and_then(|address| normalize_address(address).ok());
        if normalized.is_none() {
            issues.push(ClassificationIssue {
                code: ClassificationIssueCode::InvalidPoolCurrency,
                token_address: reference.token_address.clone(),
                source_locator: Some(reference.source_locator.clone()),
                message: "V4 pool currency is missing or invalid".to_owned(),
            });
        }
        let pool = pools
            .entry(reference.pool_id.clone())
            .or_insert((None, None));
        if reference.side == "token0" {
            pool.0 = normalized;
        } else {
            pool.1 = normalized;
        }
    }

    for (pool_id, (token0, token1)) in pools {
        let Some(token0) = token0 else {
            continue;
        };
        let Some(token1) = token1 else {
            continue;
        };
        let qualified = liquidity_by_pool
            .map(|liquidity_by_pool| {
                liquidity_by_pool
                    .get(&pool_id)
                    .is_some_and(|evidence| pool_is_liquidity_qualified(evidence))
            })
            .unwrap_or(true);
        if !qualified {
            continue;
        }
        if policy.core_addresses.contains(&token0) || policy.core_addresses.contains(&token1) {
            add_extension_member(&mut extension, policy, &token0, ExtensionAnchor::CoreToken);
            add_extension_member(&mut extension, policy, &token1, ExtensionAnchor::CoreToken);
        }
        if stock_addresses.contains(&token0) || stock_addresses.contains(&token1) {
            add_extension_member(&mut extension, policy, &token0, ExtensionAnchor::StockToken);
            add_extension_member(&mut extension, policy, &token1, ExtensionAnchor::StockToken);
        }
        if policy.test_addresses.contains(&token0) {
            add_extension_member(&mut extension, policy, &token1, ExtensionAnchor::TestToken);
        }
        if policy.test_addresses.contains(&token1) {
            add_extension_member(&mut extension, policy, &token0, ExtensionAnchor::TestToken);
        }
    }
    extension
}

pub fn pool_is_liquidity_qualified(pool: &V4PoolLiquidityEvidence) -> bool {
    let Some(fee) = pool.fee else {
        return false;
    };
    if fee <= 0 || fee > MAX_CANDIDATE_FIXED_FEE {
        return false;
    }
    if pool.dynamic_fee != 0 || pool.special_fee != 0 {
        return false;
    }
    if pool
        .hooks
        .as_deref()
        .is_some_and(|hooks| !(hooks.is_empty() || hooks == ZERO_ADDRESS))
    {
        return false;
    }
    if !pool.has_liquidity || pool.liquidity_index.unwrap_or(0.0) <= 0.0 {
        return false;
    }
    true
}

fn add_extension_member(
    extension: &mut BTreeMap<String, BTreeSet<ExtensionAnchor>>,
    policy: &ClassificationPolicy,
    address: &str,
    anchor: ExtensionAnchor,
) {
    if policy.test_addresses.contains(address) {
        return;
    }
    extension
        .entry(address.to_owned())
        .or_default()
        .insert(anchor);
}

#[cfg(test)]
mod tests {
    use super::{
        classify_tokens, classify_tokens_with_liquidity, pool_is_liquidity_qualified,
        testnet_test_token_addresses, ClassificationPolicy, ClassificationReason, ExtensionAnchor,
        MAINNET_TEST_TOKEN_CANDIDATES,
    };
    use crate::metadata::{MetadataSourceKind, NormalizedTokenMetadata};
    use crate::model::{AdmissionStatus, LibraryKey};
    use crate::sources::{V4PoolCurrencyReference, V4PoolLiquidityEvidence};
    use manager_contracts::TokenKeyId;
    use std::collections::BTreeMap;

    const CORE: &str = "0x1111111111111111111111111111111111111111";
    const STOCK: &str = "0x2222222222222222222222222222222222222222";
    const EXTENSION: &str = "0x3333333333333333333333333333333333333333";
    const ORPHAN: &str = "0x4444444444444444444444444444444444444444";
    const TEST_TOKEN: &str = "0x05a31b34a1bf8edb08cbfa2e9d860e6d0939313e";

    fn token(
        address: &str,
        symbol: &str,
        source: MetadataSourceKind,
        checksum_suffix: &str,
    ) -> NormalizedTokenMetadata {
        token_on_network("robinhood", 4663, address, symbol, source, checksum_suffix)
    }

    fn token_on_network(
        network: &str,
        chain_id: u64,
        address: &str,
        symbol: &str,
        source: MetadataSourceKind,
        checksum_suffix: &str,
    ) -> NormalizedTokenMetadata {
        NormalizedTokenMetadata {
            source,
            source_locator: format!("{}:{network}:{chain_id}:{address}", source.as_str()),
            token_key_id: TokenKeyId::new(network, chain_id, address).unwrap(),
            v4_currency_key: address.to_owned(),
            token_address: address.to_owned(),
            token_symbol: symbol.to_owned(),
            token_name: Some(symbol.to_owned()),
            token_decimals: 18,
            token_kind: "erc20".to_owned(),
            is_native: false,
            is_wrapped_native: false,
            is_stable: false,
            admission_source: format!("bootstrap:{}", source.as_str()),
            metadata_source: "fixture".to_owned(),
            metadata_checksum: format!("sha256:{checksum_suffix:0>64}"),
            source_json: "{}".to_owned(),
            first_seen_at: None,
        }
    }

    fn pool(
        network: &str,
        chain_id: u64,
        token0: &str,
        token1: &str,
        pool_id: &str,
    ) -> Vec<V4PoolCurrencyReference> {
        vec![
            V4PoolCurrencyReference {
                network: network.to_owned(),
                chain_id,
                pool_id: pool_id.to_owned(),
                side: "token0",
                token_address: Some(token0.to_owned()),
                source_locator: format!("pool:{pool_id}:token0"),
            },
            V4PoolCurrencyReference {
                network: network.to_owned(),
                chain_id,
                pool_id: pool_id.to_owned(),
                side: "token1",
                token_address: Some(token1.to_owned()),
                source_locator: format!("pool:{pool_id}:token1"),
            },
        ]
    }

    fn liquidity(
        pool_id: &str,
        depth_status: &str,
        has_liquidity: bool,
    ) -> V4PoolLiquidityEvidence {
        V4PoolLiquidityEvidence {
            network: "robinhood".to_owned(),
            chain_id: 4663,
            protocol: "uniswap_v4".to_owned(),
            pool_id: pool_id.to_owned(),
            fee: Some(3000),
            dynamic_fee: 0,
            special_fee: 0,
            hooks: Some(crate::classification::ZERO_ADDRESS.to_owned()),
            has_liquidity,
            liquidity_index: Some(1000.0),
            depth_status: Some(depth_status.to_owned()),
            evidence_source: Some("pool_manager_extsload_live_state_batch".to_owned()),
        }
    }

    fn mainnet_policy(core: &[&str], test: &[&str]) -> ClassificationPolicy {
        ClassificationPolicy::new(
            "robinhood",
            4663,
            core.iter().map(|address| (*address).to_owned()),
            test.iter().map(|address| (*address).to_owned()),
        )
    }

    fn membership_libraries(token: &super::ClassifiedToken) -> Vec<LibraryKey> {
        token
            .memberships
            .iter()
            .map(|membership| membership.library)
            .collect()
    }

    #[test]
    fn source_precedence_merges_one_address_and_extension_can_overlap() {
        let records = vec![
            token(CORE, "USDG", MetadataSourceKind::ChainTokens, "chain"),
            token(CORE, "USDG", MetadataSourceKind::StockRegistry, "stock"),
            token(CORE, "USDG", MetadataSourceKind::CoreAllowlist, "core"),
        ];
        let result = classify_tokens(
            &records,
            &pool("robinhood", 4663, CORE, CORE, "pool-core"),
            &mainnet_policy(&[CORE], &[]),
        );
        assert_eq!(result.tokens.len(), 1);
        assert_eq!(result.tokens[0].primary_library, Some(LibraryKey::Core));
        assert_eq!(
            membership_libraries(&result.tokens[0]),
            vec![LibraryKey::Core, LibraryKey::Extension]
        );
        assert_eq!(
            result.tokens[0].token.source,
            MetadataSourceKind::CoreAllowlist
        );
        assert_eq!(result.source_overrides, 2);
    }

    #[test]
    fn stock_membership_uses_stock_metadata_and_extension_overlap() {
        let records = vec![
            token(
                STOCK,
                "CHAIN_NAME",
                MetadataSourceKind::ChainTokens,
                "chain",
            ),
            token(STOCK, "AAPL", MetadataSourceKind::StockRegistry, "stock"),
        ];
        let result = classify_tokens(
            &records,
            &pool("robinhood", 4663, STOCK, CORE, "pool-stock"),
            &mainnet_policy(&[CORE], &[]),
        );
        assert_eq!(result.tokens[0].primary_library, Some(LibraryKey::Stocks));
        assert_eq!(result.tokens[0].token.token_symbol, "AAPL");
        assert_eq!(
            membership_libraries(&result.tokens[0]),
            vec![LibraryKey::Stocks, LibraryKey::Extension]
        );
        assert_eq!(
            result.tokens[0].extension_anchors,
            vec![ExtensionAnchor::CoreToken, ExtensionAnchor::StockToken]
        );
    }

    #[test]
    fn duplicate_symbol_never_drives_primary_classification() {
        let test_address = MAINNET_TEST_TOKEN_CANDIDATES[0].1;
        let records = vec![
            token(test_address, "TZ", MetadataSourceKind::ChainTokens, "tz"),
            token(ORPHAN, "TZ", MetadataSourceKind::ChainTokens, "other"),
        ];
        let result = classify_tokens(&records, &[], &mainnet_policy(&[CORE], &[test_address]));
        let by_address = result
            .tokens
            .iter()
            .map(|token| (token.token.token_address.as_str(), token))
            .collect::<BTreeMap<_, _>>();
        assert_eq!(
            by_address[test_address].primary_library,
            Some(LibraryKey::Test)
        );
        assert_eq!(by_address[ORPHAN].primary_library, None);
        assert!(result.tokens.iter().all(|token| {
            token.primary_library != Some(LibraryKey::Test)
                || token.token.token_address == test_address
        }));
    }

    #[test]
    fn testnet_address_enters_test_without_symbol_lookup() {
        let record = token_on_network(
            "robinhood-testnet",
            46630,
            TEST_TOKEN,
            "ANY",
            MetadataSourceKind::ChainTokens,
            "test",
        );
        let result = classify_tokens(&[record], &[], &ClassificationPolicy::testnet_test_policy());
        assert_eq!(result.tokens[0].primary_library, Some(LibraryKey::Test));
        assert_eq!(testnet_test_token_addresses().len(), 1);
    }

    #[test]
    fn extension_membership_uses_core_stock_and_tz_test_anchors() {
        let test_address = MAINNET_TEST_TOKEN_CANDIDATES[0].1;
        let ta_address = MAINNET_TEST_TOKEN_CANDIDATES[1].1;
        let tb_address = MAINNET_TEST_TOKEN_CANDIDATES[2].1;
        let records = vec![
            token(CORE, "CORE", MetadataSourceKind::CoreAllowlist, "core"),
            token(STOCK, "AAPL", MetadataSourceKind::StockRegistry, "stock"),
            token(EXTENSION, "EXT", MetadataSourceKind::ChainTokens, "ext"),
            token(ORPHAN, "ORPHAN", MetadataSourceKind::ChainTokens, "orphan"),
            token(test_address, "TZ", MetadataSourceKind::ChainTokens, "tz"),
            token(ta_address, "TA", MetadataSourceKind::ChainTokens, "ta"),
            token(tb_address, "TB", MetadataSourceKind::ChainTokens, "tb"),
        ];
        let pools = [
            pool("robinhood", 4663, CORE, EXTENSION, "pool-core"),
            pool("robinhood", 4663, STOCK, EXTENSION, "pool-stock"),
            pool("robinhood", 4663, test_address, ta_address, "pool-tz-ta"),
            pool("robinhood", 4663, test_address, tb_address, "pool-tz-tb"),
        ]
        .concat();
        let result = classify_tokens(&records, &pools, &mainnet_policy(&[CORE], &[test_address]));
        let by_address = result
            .tokens
            .iter()
            .map(|token| (token.token.token_address.as_str(), token))
            .collect::<BTreeMap<_, _>>();

        assert_eq!(by_address[CORE].primary_library, Some(LibraryKey::Core));
        assert!(by_address[CORE].extension_member());
        assert_eq!(by_address[STOCK].primary_library, Some(LibraryKey::Stocks));
        assert!(by_address[STOCK].extension_member());
        assert_eq!(by_address[EXTENSION].primary_library, None);
        assert!(by_address[EXTENSION].extension_member());
        assert_eq!(
            by_address[test_address].primary_library,
            Some(LibraryKey::Test)
        );
        assert!(!by_address[test_address].extension_member());
        assert_eq!(by_address[ta_address].primary_library, None);
        assert!(by_address[ta_address].extension_member());
        assert_eq!(by_address[tb_address].primary_library, None);
        assert!(by_address[tb_address].extension_member());
        assert!(!by_address[ORPHAN].extension_member());
        assert_eq!(result.primary_count_for_library(LibraryKey::Test), 1);
    }

    #[test]
    fn extension_only_tokens_are_not_unclassified() {
        let records = vec![token(
            EXTENSION,
            "EXT",
            MetadataSourceKind::ChainTokens,
            "ext",
        )];
        let result = classify_tokens(
            &records,
            &pool("robinhood", 4663, EXTENSION, CORE, "pool-2"),
            &mainnet_policy(&[CORE], &[]),
        );
        assert_eq!(result.tokens[0].primary_library, None);
        assert!(result.tokens[0].extension_member());
        assert_eq!(result.unclassified_count(), 0);
        assert_eq!(
            result.tokens[0].memberships[0].admission_status,
            AdmissionStatus::CandidateOnly
        );
        assert_eq!(
            result.tokens[0].memberships[0].reason,
            ClassificationReason::ExtensionMember
        );
    }

    #[test]
    fn same_priority_disagreement_is_reported_and_not_classified() {
        let records = vec![
            token(STOCK, "AAA", MetadataSourceKind::StockRegistry, "one"),
            token(STOCK, "BBB", MetadataSourceKind::StockRegistry, "two"),
        ];
        let result = classify_tokens(&records, &[], &mainnet_policy(&[CORE], &[]));
        assert_eq!(result.tokens[0].primary_library, None);
        assert!(result.tokens[0].memberships.is_empty());
        assert_eq!(result.issues.len(), 1);
    }

    #[test]
    fn duplicate_symbols_are_reported_after_membership_assignment() {
        let records = vec![
            token(EXTENSION, "SAME", MetadataSourceKind::ChainTokens, "one"),
            token(
                "0x5555555555555555555555555555555555555555",
                "SAME",
                MetadataSourceKind::ChainTokens,
                "two",
            ),
        ];
        let pools = [
            pool("robinhood", 4663, EXTENSION, CORE, "pool-a"),
            pool(
                "robinhood",
                4663,
                "0x5555555555555555555555555555555555555555",
                CORE,
                "pool-b",
            ),
        ]
        .concat();
        let result = classify_tokens(&records, &pools, &mainnet_policy(&[CORE], &[]));
        assert_eq!(result.duplicate_symbols.len(), 1);
        assert_eq!(result.duplicate_symbols[0].symbol, "SAME");
        assert_eq!(result.duplicate_symbols[0].token_key_ids.len(), 2);
    }

    #[test]
    fn extension_requires_a_liquidity_qualified_direct_pool() {
        let records = vec![
            token(CORE, "USDG", MetadataSourceKind::CoreAllowlist, "core"),
            token(EXTENSION, "EXT", MetadataSourceKind::ChainTokens, "ext"),
        ];
        let pools = pool("robinhood", 4663, CORE, EXTENSION, "pool-core");

        let no_evidence =
            classify_tokens_with_liquidity(&records, &pools, &[], &mainnet_policy(&[CORE], &[]));
        let by_address = no_evidence
            .tokens
            .iter()
            .map(|token| (token.token.token_address.as_str(), token))
            .collect::<BTreeMap<_, _>>();
        assert!(!by_address[CORE].extension_member());
        assert!(!by_address[EXTENSION].extension_member());

        let qualified = classify_tokens_with_liquidity(
            &records,
            &pools,
            &[liquidity("pool-core", "deep", true)],
            &mainnet_policy(&[CORE], &[]),
        );
        let by_address = qualified
            .tokens
            .iter()
            .map(|token| (token.token.token_address.as_str(), token))
            .collect::<BTreeMap<_, _>>();
        assert!(by_address[EXTENSION].extension_member());
    }

    #[test]
    fn liquidity_gate_rejects_unsafe_or_missing_pool_evidence() {
        let mut dynamic_fee = liquidity("dynamic-fee", "deep", true);
        dynamic_fee.dynamic_fee = 1;
        let mut special_fee = liquidity("special-fee", "deep", true);
        special_fee.special_fee = 1;
        let mut non_zero_hook = liquidity("non-zero-hook", "deep", true);
        non_zero_hook.hooks = Some("0x1111111111111111111111111111111111111111".to_owned());
        let mut missing_liquidity = liquidity("missing-liquidity", "deep", false);
        missing_liquidity.liquidity_index = Some(0.0);
        for pool in [dynamic_fee, special_fee, non_zero_hook, missing_liquidity] {
            assert!(!pool_is_liquidity_qualified(&pool), "{pool:?}");
        }
    }

    #[test]
    fn liquidity_gate_ignores_usd_depth_labels() {
        for pool in [
            liquidity("deep", "deep", true),
            liquidity("thin", "thin", true),
            liquidity("unpriced", "unpriced", true),
            liquidity("missing-depth-label", "", true),
        ] {
            assert!(pool_is_liquidity_qualified(&pool), "{pool:?}");
        }
        let mut stale_thin = liquidity("stale-thin", "thin", true);
        stale_thin.evidence_source = Some("stale_snapshot".to_owned());
        assert!(pool_is_liquidity_qualified(&stale_thin));
    }
}
