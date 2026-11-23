//! Direction-aware projection of a Uniswap V4 pool.
//!
//! A **directional pool** is a base V4 pool projected onto one of its two
//! swap directions. The projection adds **no new on-chain fact**:
//!
//! - the base pool carries the identity and the fixed facts;
//! - the direction is a pure function of the token order;
//! - the directional quantities are recomputed from the snapshot tables.
//!
//! Direction **0** trades the higher address token into the lower one
//! (`token1 -> token0`), direction **1** trades the lower address token into
//! the higher one (`token0 -> token1`). A base pool therefore projects onto
//! **exactly two** directional pools, and the two rows differ only in the
//! direction-dependent fields.

use serde::{Deserialize, Deserializer, Serialize, Serializer};
use std::fmt;

use crate::ids::{DirectionalPoolId, IdError, PairKeyId};

/// The two swap directions of a V4 pool.
///
/// The numeric values are the ones stored in the database
/// (`direction INTEGER CHECK (direction IN (0, 1))`), so the type and the
/// column never drift apart.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub enum PoolDirection {
    /// Direction **0**: `token1 -> token0`, the higher address token is the
    /// input, so the swap is **not** zero-for-one.
    HighToLow = 0,
    /// Direction **1**: `token0 -> token1`, the lower address token is the
    /// input, so the swap **is** zero-for-one.
    LowToHigh = 1,
}

impl PoolDirection {
    /// Both directions, in ascending order.
    pub const ALL: [PoolDirection; 2] = [PoolDirection::HighToLow, PoolDirection::LowToHigh];

    /// The direction stored as `0` or `1`.
    pub fn from_raw(value: i64) -> Option<Self> {
        match value {
            0 => Some(Self::HighToLow),
            1 => Some(Self::LowToHigh),
            _ => None,
        }
    }

    /// The stored value of this direction.
    pub fn raw(self) -> i64 {
        match self {
            Self::HighToLow => 0,
            Self::LowToHigh => 1,
        }
    }

    /// Whether the swap spends `token0` (the V4 `zeroForOne` flag).
    ///
    /// This is **not** a stored column: it is derived from the direction, so
    /// it cannot contradict it.
    pub fn zero_for_one(self) -> bool {
        match self {
            // The input is token1.
            Self::HighToLow => false,
            // The input is token0.
            Self::LowToHigh => true,
        }
    }

    /// The index of the input token within the base pool (`0` or `1`).
    pub fn input_token_index(self) -> usize {
        match self {
            Self::HighToLow => 1,
            Self::LowToHigh => 0,
        }
    }
}

impl fmt::Display for PoolDirection {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(formatter, "{}", self.raw())
    }
}

impl Serialize for PoolDirection {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        serializer.serialize_i64(self.raw())
    }
}

impl<'de> Deserialize<'de> for PoolDirection {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        let raw = i64::deserialize(deserializer)?;
        Self::from_raw(raw).ok_or_else(|| {
            serde::de::Error::custom(format!("pool direction must be 0 or 1, got {raw}"))
        })
    }
}

/// The base V4 pool: identity plus the fixed on-chain facts.
///
/// This is the **sub-object** of a directional pool. It carries nothing that
/// the direction changes.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct V4Pool {
    pub base_pool_id: String,
    pub pool_manager: String,
    pub pool_key_json: String,
    pub token0: String,
    pub token1: String,
    pub token0_key_id: String,
    pub token1_key_id: String,
    pub fee: i64,
    pub tick_spacing: i64,
    pub hooks: String,
}

impl V4Pool {
    /// The token that this direction spends.
    pub fn input_token_key_id(&self, direction: PoolDirection) -> &str {
        match direction.input_token_index() {
            0 => &self.token0_key_id,
            _ => &self.token1_key_id,
        }
    }

    /// The token that this direction receives.
    pub fn output_token_key_id(&self, direction: PoolDirection) -> &str {
        match direction.input_token_index() {
            0 => &self.token1_key_id,
            _ => &self.token0_key_id,
        }
    }

    /// The pair this pool belongs to.
    ///
    /// The pair is **direction independent**: a physical pool belongs to one
    /// pair no matter which way it is traded.
    pub fn pair_key_id(&self, chain_id: u64) -> Result<PairKeyId, IdError> {
        PairKeyId::new(chain_id, &self.token0, &self.token1)
    }

    /// Whether the base pool carries the canonical V4 token order.
    ///
    /// V4 sorts the two currencies by address, so `token0` is the **lower**
    /// address. The direction values are defined against that order, so a
    /// pool whose tokens are stored the other way round cannot be projected
    /// and must not be published as a directional pool.
    pub fn token_order_is_canonical(&self) -> bool {
        self.token0.to_ascii_lowercase() < self.token1.to_ascii_lowercase()
    }
}

/// A base V4 pool projected onto one direction.
///
/// The directional quantities are stored **separately** from their
/// "measured" flags, because "not measured" and "measured as zero" are
/// different facts: an unmeasured capacity must never be read as a zero
/// capacity.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct DirectionalPool {
    pub base: V4Pool,
    pub direction: PoolDirection,
    /// The amount of the **input** token the pool can carry, in raw units.
    ///
    /// Direction 0 takes `single_tick_max_token1_raw`, direction 1 takes
/// `single_tick_max_token0_raw`.
    pub input_capacity_raw: Option<String>,
    pub input_capacity_measured: bool,
    /// The exact single-hop output in the current tick range, decimal
/// normalized. This is the **ordering** quantity of the main sequence.
    pub directional_output: Option<f64>,
    pub directional_output_measured: bool,
    /// The 1-based rank inside (`pair_key_id`, `direction`). Unmeasured rows
    /// have no rank.
    pub pair_direction_rank: Option<i64>,
    pub pool_status: String,
}

impl DirectionalPool {
    /// Project a base pool onto one direction.
    ///
    /// The direction is chosen, never inferred: a base pool projects onto
    /// both directions, and the two rows differ only in the direction
    /// dependent fields.
    pub fn of_pool(base: V4Pool, direction: PoolDirection) -> Self {
        Self {
            base,
            direction,
            input_capacity_raw: None,
            input_capacity_measured: false,
            directional_output: None,
            directional_output_measured: false,
            pair_direction_rank: None,
            pool_status: "active".to_owned(),
        }
    }

    /// The token this direction spends.
    pub fn input_token_key_id(&self) -> &str {
        self.base.input_token_key_id(self.direction)
    }

    /// The token this direction receives.
    pub fn output_token_key_id(&self) -> &str {
        self.base.output_token_key_id(self.direction)
    }

    /// The identity of this directional pool.
    pub fn directional_pool_id(
        &self,
        network: impl AsRef<str>,
        chain_id: u64,
        scope_key: impl AsRef<str>,
    ) -> Result<DirectionalPoolId, IdError> {
        DirectionalPoolId::new(
            network,
            chain_id,
            scope_key,
            &self.base.base_pool_id,
            self.direction.raw() as u8,
        )
    }
}

#[cfg(test)]
mod tests {
    use super::{DirectionalPool, PoolDirection, V4Pool};

    const TOKEN0: &str = "0x1111111111111111111111111111111111111111";
    const TOKEN1: &str = "0x2222222222222222222222222222222222222222";

    fn base() -> V4Pool {
        V4Pool {
            base_pool_id:
                "0x002eaee43a6c1daa7c29484ee0d44a9abcd65827e72f883a1feb42ab8618ccba".to_owned(),
            pool_manager: "0x8366a39cc670b4001a1121b6f6a443a643e40951".to_owned(),
            pool_key_json: "{}".to_owned(),
            token0: TOKEN0.to_owned(),
            token1: TOKEN1.to_owned(),
            token0_key_id: format!("robinhood:4663:{TOKEN0}"),
            token1_key_id: format!("robinhood:4663:{TOKEN1}"),
            fee: 3000,
            tick_spacing: 60,
            hooks: "0x0000000000000000000000000000000000000000".to_owned(),
        }
    }

    #[test]
    fn the_direction_mapping_cannot_flip() {
        let pool = base();

        // Direction 0 trades the higher address token into the lower one.
        let high_to_low = DirectionalPool::of_pool(pool.clone(), PoolDirection::HighToLow);
        assert_eq!(high_to_low.input_token_key_id(), pool.token1_key_id.as_str());
        assert_eq!(high_to_low.output_token_key_id(), pool.token0_key_id.as_str());
        assert!(!high_to_low.direction.zero_for_one());

        // Direction 1 trades the lower address token into the higher one.
        let low_to_high = DirectionalPool::of_pool(pool.clone(), PoolDirection::LowToHigh);
        assert_eq!(low_to_high.input_token_key_id(), pool.token0_key_id.as_str());
        assert_eq!(low_to_high.output_token_key_id(), pool.token1_key_id.as_str());
        assert!(low_to_high.direction.zero_for_one());

        // The two directions are genuinely different projections.
        assert_ne!(high_to_low.input_token_key_id(), low_to_high.input_token_key_id());
        assert_ne!(
            high_to_low.directional_pool_id("robinhood", 4663, "candidate_exchange").unwrap(),
            low_to_high
                .directional_pool_id("robinhood", 4663, "candidate_exchange")
                .unwrap()
        );
    }

    #[test]
    fn the_two_rows_of_one_base_pool_differ_only_in_the_direction_fields() {
        let pool = base();
        let high_to_low = DirectionalPool::of_pool(pool.clone(), PoolDirection::HighToLow);
        let low_to_high = DirectionalPool::of_pool(pool, PoolDirection::LowToHigh);

        assert_eq!(high_to_low.base, low_to_high.base);
        assert_ne!(high_to_low.direction, low_to_high.direction);
        // An unmeasured quantity is not a measured zero.
        assert!(!high_to_low.input_capacity_measured);
        assert_eq!(high_to_low.input_capacity_raw, None);
        assert!(!low_to_high.directional_output_measured);
        assert_eq!(low_to_high.directional_output, None);
    }

    #[test]
    fn a_pool_with_an_inverted_token_order_is_not_projectable() {
        let mut pool = base();
        assert!(pool.token_order_is_canonical());
        pool.token0 = TOKEN1.to_owned();
        pool.token1 = TOKEN0.to_owned();
        assert!(!pool.token_order_is_canonical());
    }
}
