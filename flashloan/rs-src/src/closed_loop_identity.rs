//! The identity of a closed loop, read from the closed-loop Manager.
//!
//! Everything the execution path needs to know about a closed loop is
//! **already published** by the closed-loop Manager: the loop key that
//! identifies it, the pools it trades through, and the folded
//! single-tick capacity that bounds the amount it may carry. This module
//! reads that published state out of the Manager library and hands it to
//! the worker.
//!
//! The point is that **no other layer has to reconstruct it**. A route
//! that is identified by its token path is not enough: the Manager may
//! publish several loops for one symbol path, and only the Manager knows
//! which pools and which capacity belong together. Rebuilding the identity
//! somewhere else therefore risks attaching the capacity of one loop to
//! the route of another, which would bound the amount with a number that
//! was never proved for that route.

use std::collections::BTreeMap;
use std::collections::BTreeSet;

/// The published identity and bound of one closed loop.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ClosedLoopIdentity {
    /// The key the Manager files the loop under.
    pub loop_key: String,
    /// The pools the loop trades through, in hop order.
    pub pool_ids: Vec<String>,
    /// The folded single-tick capacity, in the anchor token's raw units.
    pub single_tick_capacity_anchor_raw: Option<u128>,
    /// The hop whose folded capacity is the smallest.
    pub single_tick_capacity_binding_pool_id: Option<String>,
    /// The USDG value of **one whole** borrow token, in raw USDG units.
    ///
    /// The value is what makes the cross-tick threshold expressible
    /// without an external price feed: every pool token has a USDG
    /// conversion route by convention.
    pub borrow_token_usdg_value_raw: Option<u128>,
    /// The decimals of the borrow token.
    pub borrow_token_decimals: Option<u32>,
    /// The shared cross-tick review threshold, in raw USDG units.
    pub cross_tick_review_threshold_usdg_raw: u128,
    /// The ceiling of the cross-tick search, in anchor-token raw units.
    pub cross_tick_capacity_anchor_raw: Option<u128>,
    /// The version of the candidate edge the loop was built against.
    ///
    /// The edge names the pools the loop may trade through.  A review
    /// that was measured under one edge describes that edge, so the edge
    /// travels with the review instead of being read again later.
    pub candidate_edge_version: Option<i64>,
    /// The checksum of the candidate edge the loop was built against.
    pub candidate_edge_checksum: Option<String>,
    /// The version of the market observation the loop was built against.
    pub observation_version: Option<i64>,
    /// The checksum of the market observation the loop was built against.
    pub observation_checksum: Option<String>,
}

impl ClosedLoopIdentity {
    /// Whether two identities name the **same** closed loop.
    ///
    /// Two identities name the same loop when they walk the same pools in
    /// the same order and bound the loop with the same numbers: the
    /// folded capacity, the hop that binds it, the value of the borrow
    /// token and the cross-tick threshold the loop was measured with.
    ///
    /// The generation the loop was built against is **not** part of the
    /// comparison.  A loop that was rebuilt from a newer observation is
    /// still the same loop, and the review of it stays the review of that
    /// loop: only the numbers that describe the loop decide whether it is
    /// the same one.
    pub fn is_same_loop(&self, other: &Self) -> bool {
        self.loop_key.trim() == other.loop_key.trim()
            && self.pool_ids == other.pool_ids
            && self.single_tick_capacity_anchor_raw == other.single_tick_capacity_anchor_raw
            && self.single_tick_capacity_binding_pool_id
                == other.single_tick_capacity_binding_pool_id
            && self.borrow_token_usdg_value_raw == other.borrow_token_usdg_value_raw
            && self.borrow_token_decimals == other.borrow_token_decimals
            && self.cross_tick_review_threshold_usdg_raw
                == other.cross_tick_review_threshold_usdg_raw
            && self.cross_tick_capacity_anchor_raw == other.cross_tick_capacity_anchor_raw
    }
}

/// The closed loops of one chain, indexed by their full ordered address path.
#[derive(Clone, Debug, Default)]
pub struct ClosedLoopCatalog {
    by_address_path: BTreeMap<Vec<String>, ClosedLoopIdentity>,
    /// Every published loop, indexed by its own key.
    ///
    /// The address index drops a path that two rows disagree on, because
    /// such a path cannot name one loop.  The key index keeps every
    /// published loop: the review publish gate compares the evidence with
    /// the **whole** catalog, so a loop that is missing from this index
    /// would keep the gate from ever being satisfied.
    by_loop_key: BTreeMap<String, ClosedLoopIdentity>,
    /// The generation the catalog was read with.
    ///
    /// The catalog is read from the Manager library, so every loop in it
    /// belongs to **one** generation.  A review that was measured against
    /// one generation may not be filed against another, so the catalog
    /// carries the generation it was read with and the publish gate
    /// compares it with the generation the Manager publishes now.
    upstream: Option<ClosedLoopUpstream>,
}

/// The upstream generation a closed-loop catalog was materialized from.
///
/// The version names the generation and the checksum names its content,
/// so a generation that was rebuilt with the same version but different
/// content is still told apart from the one before it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ClosedLoopUpstream {
    /// The version of the closed-loop library.
    pub closed_loop_version: i64,
    /// The checksum of the materialization the version belongs to.
    pub materialized_checksum: Option<String>,
}

/// The generation a library-state row names.
///
/// The row is the one the Manager library holds for the chain, so the
/// version and the checksum are the Manager's own numbers and the runtime
/// never derives a generation of its own.
pub fn closed_loop_upstream(row: serde_json::Value) -> Option<ClosedLoopUpstream> {
    let closed_loop_version = row
        .get("closed_loop_version")
        .and_then(serde_json::Value::as_i64)?;
    let materialized_checksum = row
        .get("materialized_checksum")
        .and_then(serde_json::Value::as_str)
        .map(str::to_owned);
    Some(ClosedLoopUpstream {
        closed_loop_version,
        materialized_checksum,
    })
}

impl ClosedLoopCatalog {
    /// Resolve the exact ordered three-address identity published by Manager.
    ///
    /// The closed-loop key is the funding anchor plus the directed pool
    /// path.  A route may carry that path closed with its anchor; a path
    /// that starts at an intermediate token names a different closed loop
    /// and resolves to nothing here.
    pub fn identity_for(&self, addresses: &[String]) -> Option<&ClosedLoopIdentity> {
        let identity_path = match addresses {
            [_, _, _] => addresses,
            [first, _, _, last] if first == last => &addresses[..3],
            _ => return None,
        };
        self.by_address_path.get(identity_path)
    }

    /// Whether the catalog holds no loop at all.
    #[cfg(test)]
    pub fn is_empty(&self) -> bool {
        self.by_address_path.is_empty()
    }

    /// How many closed loops the catalog holds.
    ///
    /// The count is published with every group review, so a review that
    /// carries no identity can be told apart from a catalog that was never
    /// loaded: an empty catalog and a catalog that was never read are
    /// different failures, and only one of them is a Manager problem.
    pub fn len(&self) -> usize {
        self.by_loop_key.len()
    }

    /// The keys of every closed loop the catalog holds.
    ///
    /// The keys are normalized the way the Review Manager normalizes them,
    /// so the publish gate compares them without a second spelling.
    pub fn loop_keys(&self) -> BTreeSet<String> {
        self.by_loop_key
            .keys()
            .map(|key| key.trim().to_lowercase())
            .filter(|key| !key.is_empty())
            .collect()
    }

    /// The identity the Manager published under one loop key.
    pub fn identity_by_key(&self, loop_key: &str) -> Option<&ClosedLoopIdentity> {
        let key = loop_key.trim();
        if key.is_empty() {
            return None;
        }
        self.by_loop_key.get(key)
    }

    /// The generation the catalog was read with.
    ///
    /// A catalog that was built without a generation - a fixture, or a
    /// library that holds no closed-loop state at all - has none, and the
    /// publish gate then falls back to the generation it reads itself.
    pub fn upstream(&self) -> Option<&ClosedLoopUpstream> {
        self.upstream.as_ref()
    }

    /// The catalog, stamped with the generation it was read with.
    pub fn with_upstream(mut self, upstream: Option<ClosedLoopUpstream>) -> Self {
        self.upstream = upstream;
        self
    }
}

/// Build the catalog out of the rows the Manager library holds.
///
/// The rows are read **once**, when the runtime refreshes its in-memory
/// object. Every later processing step works on that object, so the
/// execution path never reads the Manager library per review.
pub fn catalog_from_rows(rows: &[serde_json::Value]) -> ClosedLoopCatalog {
    let mut by_address_path: BTreeMap<Vec<String>, ClosedLoopIdentity> = BTreeMap::new();
    let mut by_loop_key: BTreeMap<String, ClosedLoopIdentity> = BTreeMap::new();
    for row in rows {
        let Some(loop_key) = row
            .get("loop_key")
            .and_then(serde_json::Value::as_str)
            .map(|value| value.trim().to_owned())
            .filter(|value| !value.is_empty())
        else {
            continue;
        };
        let address_path: Vec<String> = row
            .get("path_addresses_json")
            .and_then(serde_json::Value::as_str)
            .and_then(|text| serde_json::from_str::<Vec<String>>(text).ok())
            .unwrap_or_default()
            .iter()
            .map(|item| item.trim().to_ascii_lowercase())
            .filter(|item| !item.is_empty())
            .collect();
        let pool_ids: Vec<String> = row
            .get("pool_ids_json")
            .and_then(serde_json::Value::as_str)
            .and_then(|text| serde_json::from_str::<Vec<String>>(text).ok())
            .unwrap_or_default();
        // The closed loop publishes the **directional** pools it walks, and
        // the base pool is inside the directional pool id.  The identity
        // the route is matched against is still the pool identity, so the
        // base pool of every published directional pool is resolved here.
        let pool_ids: Vec<String> = pool_ids
            .iter()
            .map(|directional_pool_id| base_pool_of(directional_pool_id))
            .collect();
        let capacity = row
            .get("single_tick_capacity_anchor_raw")
            .and_then(serde_json::Value::as_str)
            .and_then(|text| text.trim().parse::<u128>().ok())
            .filter(|value| *value > 0);
        let binding = row
            .get("single_tick_capacity_binding_pool_id")
            .and_then(serde_json::Value::as_str)
            .map(|value| value.trim().to_owned())
            .filter(|value| !value.is_empty())
            .map(|value| base_pool_of(&value));
        let borrow_token_usdg_value_raw = row
            .get("borrow_token_usdg_value_raw")
            .and_then(serde_json::Value::as_str)
            .and_then(|text| text.trim().parse::<u128>().ok())
            .filter(|value| *value > 0);
        let borrow_token_decimals = row
            .get("borrow_token_decimals")
            .and_then(serde_json::Value::as_i64)
            .and_then(|value| u32::try_from(value).ok());
        let cross_tick_review_threshold_usdg_raw = row
            .get("cross_tick_review_threshold_usdg_raw")
            .and_then(serde_json::Value::as_str)
            .and_then(|text| text.trim().parse::<u128>().ok())
            .unwrap_or(0);
        let cross_tick_capacity_anchor_raw = row
            .get("cross_tick_capacity_anchor_raw")
            .and_then(serde_json::Value::as_str)
            .and_then(|text| text.trim().parse::<u128>().ok())
            .filter(|value| *value > 0);
        let candidate_edge_version = row
            .get("candidate_edge_version")
            .and_then(serde_json::Value::as_i64);
        let candidate_edge_checksum = row
            .get("candidate_edge_checksum")
            .and_then(serde_json::Value::as_str)
            .map(|value| value.trim().to_owned())
            .filter(|value| !value.is_empty());
        let observation_version = row
            .get("observation_version")
            .and_then(serde_json::Value::as_i64);
        let observation_checksum = row
            .get("observation_checksum")
            .and_then(serde_json::Value::as_str)
            .map(|value| value.trim().to_owned())
            .filter(|value| !value.is_empty());
        let identity = ClosedLoopIdentity {
                loop_key: loop_key.clone(),
                pool_ids,
                single_tick_capacity_anchor_raw: capacity,
                single_tick_capacity_binding_pool_id: binding,
                borrow_token_usdg_value_raw,
                borrow_token_decimals,
                cross_tick_review_threshold_usdg_raw,
                cross_tick_capacity_anchor_raw,
                candidate_edge_version,
                candidate_edge_checksum,
                observation_version,
                observation_checksum,
            };
        // The key index keeps every published loop, even one whose address
            // path cannot name it.  The publish gate compares the evidence
            // with the whole catalog, so a loop that is missing here would
            // keep the gate from ever being satisfied.
        by_loop_key.insert(loop_key.clone(), identity.clone());
        if address_path.len() != 4 || address_path.first() != address_path.last() {
            continue;
        }
        let identity_path = address_path[..3].to_vec();
        if by_address_path
            .get(&identity_path)
            .is_some_and(|existing| existing != &identity)
        {
            by_address_path.remove(&identity_path);
            continue;
        }
        by_address_path.insert(identity_path, identity);
    }
    ClosedLoopCatalog {
        by_address_path,
        by_loop_key,
        upstream: None,
    }
}

/// The base pool a published pool identity names.
///
/// The closed loop publishes the **directional** pools it walks, and a
/// directional pool id carries its base pool inside it:
/// `dpool:v1:{network}:{chain_id}:{scope}:{base_pool_id}:{direction}`.
/// The identity the route is matched against is still the pool identity,
/// so the base pool is resolved here, once, at the boundary.
///
/// An identity that is not a directional pool id is already a pool id and
/// is returned unchanged: the resolution never invents a pool.
fn base_pool_of(published_pool_id: &str) -> String {
    let trimmed = published_pool_id.trim();
    let Some(rest) = trimmed.strip_prefix("dpool:v1:") else {
        return trimmed.to_owned();
    };
    let mut parts = rest.split(':');
    // network, chain id, scope key, base pool, direction.
    let (_network, _chain_id, _scope_key, base_pool_id, direction) = match (
        parts.next(),
        parts.next(),
        parts.next(),
        parts.next(),
        parts.next(),
    ) {
        (Some(network), Some(chain_id), Some(scope_key), Some(base_pool_id), Some(direction)) => {
            (network, chain_id, scope_key, base_pool_id, direction)
        }
        _ => return trimmed.to_owned(),
    };
    if parts.next().is_some() {
        return trimmed.to_owned();
    }
    if direction != "0" && direction != "1" {
        return trimmed.to_owned();
    }
    if base_pool_id.is_empty() {
        return trimmed.to_owned();
    }
    base_pool_id.to_owned()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn addresses(a: &str, b: &str, c: &str, close: &str) -> Vec<String> {
        [a, b, c, close]
            .into_iter()
            .map(str::to_owned)
            .collect()
    }

    #[test]
    fn an_empty_catalog_never_matches() {
        let catalog = ClosedLoopCatalog::default();
        assert!(catalog.is_empty());
        assert!(catalog
            .identity_for(&addresses("a", "b", "c", "a"))
            .is_none());
    }

    #[test]
    fn a_row_without_a_closed_address_path_is_ignored() {
        let catalog = catalog_from_rows(&[serde_json::json!({
            "loop_key": "a:b:c",
            "path_addresses_json": "[]",
            "pool_ids_json": "[]",
        })]);
        assert!(catalog.is_empty());
    }

    #[test]
    fn only_the_exact_manager_address_path_matches() {
        let catalog = catalog_from_rows(&[serde_json::json!({
            "loop_key": "a:b:c",
            "path_addresses_json": "[\"A\",\"B\",\"C\",\"A\"]",
            "pool_ids_json": "[\"p1\",\"p2\",\"p3\"]",
            "single_tick_capacity_anchor_raw": "10888919926",
        })]);
        assert!(catalog.identity_for(&addresses("B", "C", "A", "B")).is_none());
        assert!(catalog.identity_for(&addresses("C", "A", "B", "C")).is_none());
        let identity = catalog
            .identity_for(&addresses("a", "b", "c", "a"))
            .expect("exact Manager address path matches");
        assert_eq!(identity.loop_key, "a:b:c");
        assert_eq!(identity.single_tick_capacity_anchor_raw, Some(10_888_919_926));
        assert_eq!(identity.pool_ids, vec!["p1", "p2", "p3"]);
    }

    /// One funding anchor may carry two directed closed loops: X-Y-Z-X and
    /// X-Z-Y-X.  They share the anchor but not the direction, so they are
    /// **two independent closed loops**: each has its own published key, its
    /// own pool set and its own folded capacity.  The catalog has to keep
    /// them apart, because a route that runs X-Y-Z-X may never be bounded by
    /// the capacity that was proved for X-Z-Y-X.
    #[test]
    fn one_anchor_carries_two_independent_directed_closed_loops() {
        let catalog = catalog_from_rows(&[
            serde_json::json!({
                "loop_key": "anchor:middle:other",
                "path_addresses_json": "[\"A\",\"B\",\"C\",\"A\"]",
                "pool_ids_json": "[\"p1\",\"p2\",\"p3\"]",
                "single_tick_capacity_anchor_raw": "100",
            }),
            serde_json::json!({
                "loop_key": "anchor:other:middle",
                "path_addresses_json": "[\"A\",\"C\",\"B\",\"A\"]",
                "pool_ids_json": "[\"q1\",\"q2\",\"q3\"]",
                "single_tick_capacity_anchor_raw": "900",
            }),
        ]);
        assert_eq!(catalog.by_address_path.len(), 2);

        // The forward direction resolves to its own identity ...
        let forward = catalog
            .identity_for(&addresses("a", "b", "c", "a"))
            .expect("X-Y-Z-X resolves");
        assert_eq!(forward.loop_key, "anchor:middle:other");
        assert_eq!(forward.pool_ids, vec!["p1", "p2", "p3"]);
        assert_eq!(forward.single_tick_capacity_anchor_raw, Some(100));

        // ... and the reverse direction resolves to the **other** identity.
        let reverse = catalog
            .identity_for(&addresses("a", "c", "b", "a"))
            .expect("X-Z-Y-X resolves");
        assert_eq!(reverse.loop_key, "anchor:other:middle");
        assert_eq!(reverse.pool_ids, vec!["q1", "q2", "q3"]);
        assert_eq!(reverse.single_tick_capacity_anchor_raw, Some(900));

        // The two directions are never the same closed loop.
        assert_ne!(forward.loop_key, reverse.loop_key);
        assert_ne!(forward.pool_ids, reverse.pool_ids);
        assert_ne!(
            forward.single_tick_capacity_anchor_raw,
            reverse.single_tick_capacity_anchor_raw
        );
    }

    #[test]
    fn conflicting_rows_for_one_address_path_are_not_selectable() {
        let rows = [
            serde_json::json!({
                "loop_key": "first",
                "path_addresses_json": "[\"A\",\"B\",\"C\",\"A\"]",
                "pool_ids_json": "[\"p1\",\"p2\",\"p3\"]",
                "single_tick_capacity_anchor_raw": "100",
            }),
            serde_json::json!({
                "loop_key": "second",
                "path_addresses_json": "[\"a\",\"b\",\"c\",\"a\"]",
                "pool_ids_json": "[\"q1\",\"q2\",\"q3\"]",
                "single_tick_capacity_anchor_raw": "200",
            }),
        ];
        let catalog = catalog_from_rows(&rows);
        assert!(catalog.identity_for(&addresses("A", "B", "C", "A")).is_none());
    }

    #[test]
    fn a_published_capacity_is_read_as_a_decimal_string() {
        let catalog = catalog_from_rows(&[serde_json::json!({
            "loop_key": "a:b:c",
            "path_addresses_json": "[\"A\",\"B\",\"C\",\"A\"]",
            "pool_ids_json": "[\"p1\",\"p2\",\"p3\"]",
            "single_tick_capacity_anchor_raw": "10888919926",
            "single_tick_capacity_binding_pool_id": "p1",
        })]);
        let identity = catalog
            .identity_for(&addresses("a", "b", "c", "a"))
            .expect("the loop is published");
        assert_eq!(identity.loop_key, "a:b:c");
        assert_eq!(identity.single_tick_capacity_anchor_raw, Some(10_888_919_926));
        assert_eq!(identity.single_tick_capacity_binding_pool_id.as_deref(), Some("p1"));
    }

    #[test]
    fn cross_tick_inputs_are_read_from_the_manager_row() {
        let catalog = catalog_from_rows(&[serde_json::json!({
            "loop_key": "a:b:c",
            "path_addresses_json": "[\"A\",\"B\",\"C\",\"A\"]",
            "pool_ids_json": "[\"p1\",\"p2\",\"p3\"]",
            "borrow_token_usdg_value_raw": "2500000",
            "borrow_token_decimals": 18,
            "cross_tick_review_threshold_usdg_raw": "10000000",
            "cross_tick_capacity_anchor_raw": "9000000000000000000"
        })]);
        let identity = catalog
            .identity_for(&addresses("a", "b", "c", "a"))
            .expect("Manager row is loaded");
        assert_eq!(identity.borrow_token_usdg_value_raw, Some(2_500_000));
        assert_eq!(identity.borrow_token_decimals, Some(18));
        assert_eq!(identity.cross_tick_review_threshold_usdg_raw, 10_000_000);
        assert_eq!(identity.cross_tick_capacity_anchor_raw, Some(9_000_000_000_000_000_000));
    }

    #[test]
    fn missing_cross_tick_inputs_remain_missing() {
        let catalog = catalog_from_rows(&[serde_json::json!({
            "loop_key": "a:b:c",
            "path_addresses_json": "[\"A\",\"B\",\"C\",\"A\"]",
            "pool_ids_json": "[]"
        })]);
        let identity = catalog
            .identity_for(&addresses("a", "b", "c", "a"))
            .expect("Manager row is loaded");
        assert_eq!(identity.borrow_token_usdg_value_raw, None);
        assert_eq!(identity.borrow_token_decimals, None);
        assert_eq!(identity.cross_tick_review_threshold_usdg_raw, 0);
        assert_eq!(identity.cross_tick_capacity_anchor_raw, None);
    }

    #[test]
    fn the_same_closed_loop_is_recognized_by_its_own_numbers() {
        let row = serde_json::json!({
            "loop_key": "a:b:c",
            "path_addresses_json": "[\"A\",\"B\",\"C\",\"A\"]",
            "pool_ids_json": "[\"dpool:v1:robinhood:4663:core4:p1:1\",\"dpool:v1:robinhood:4663:core4:p2:1\",\"dpool:v1:robinhood:4663:core4:p3:0\"]",
            "single_tick_capacity_anchor_raw": "1000",
            "single_tick_capacity_binding_pool_id": "dpool:v1:robinhood:4663:core4:p2:1",
            "borrow_token_usdg_value_raw": "2500000",
            "borrow_token_decimals": 18,
            "cross_tick_review_threshold_usdg_raw": "10000000",
            "cross_tick_capacity_anchor_raw": "9000000000000000000",
            "candidate_edge_version": 7,
            "candidate_edge_checksum": "sha256:candidate-edge",
            "observation_version": 4,
            "observation_checksum": "sha256:observation"
        });
        let catalog = catalog_from_rows(&[row.clone()]);
        let identity = catalog
            .identity_by_key("a:b:c")
            .expect("the published loop is indexed by its key");

        // A second read of the same row is the same closed loop.
        let again = catalog_from_rows(&[row]);
        let same = again
            .identity_by_key("a:b:c")
            .expect("the published loop is indexed by its key");
        assert!(identity.is_same_loop(same));

        // The generation the loop was built against is **not** part of the
        // comparison: a loop that was rebuilt from a newer observation is
        // still the same loop.
        let mut rebuilt = identity.clone();
        rebuilt.candidate_edge_version = Some(8);
        rebuilt.observation_version = Some(5);
        assert!(identity.is_same_loop(&rebuilt));

        // The pools the loop walks **are** part of it: a loop that walks
        // other pools is another closed loop.
        let mut repathed = identity.clone();
        repathed.pool_ids = vec!["p9".to_owned(), "p8".to_owned(), "p7".to_owned()];
        assert!(!identity.is_same_loop(&repathed));

        // The bound the loop carries is part of it as well: a loop with
        // another capacity bounds another amount.
        let mut recapped = identity.clone();
        recapped.single_tick_capacity_anchor_raw = Some(2_000);
        assert!(!identity.is_same_loop(&recapped));
    }
}
