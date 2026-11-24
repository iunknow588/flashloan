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
        let capacity = row
            .get("single_tick_capacity_anchor_raw")
            .and_then(serde_json::Value::as_str)
            .and_then(|text| text.trim().parse::<u128>().ok())
            .filter(|value| *value > 0);
        let binding = row
            .get("single_tick_capacity_binding_pool_id")
            .and_then(serde_json::Value::as_str)
            .map(|value| value.trim().to_owned())
            .filter(|value| !value.is_empty());
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
        let identity = ClosedLoopIdentity {
                loop_key: loop_key.clone(),
                pool_ids,
                single_tick_capacity_anchor_raw: capacity,
                single_tick_capacity_binding_pool_id: binding,
                borrow_token_usdg_value_raw,
                borrow_token_decimals,
                cross_tick_review_threshold_usdg_raw,
                cross_tick_capacity_anchor_raw,
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
}
