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
}

/// The closed loops of one chain, indexed by the token path of the route.
#[derive(Clone, Debug, Default)]
pub struct ClosedLoopCatalog {
    by_symbol_path: BTreeMap<Vec<String>, ClosedLoopIdentity>,
}

impl ClosedLoopCatalog {
    /// The identity of the loop that trades the given token path.
    ///
    /// The path is the **ordered** token path of the route. A route that
    /// trades the same symbols in the other direction is a different loop
    /// and therefore never matches.
    pub fn identity_for(&self, symbols: &[String]) -> Option<&ClosedLoopIdentity> {
        if symbols.len() < 2 {
            return None;
        }
        // The loop path repeats the anchor at the end, so the key is the
        // first three symbols of the path.
        let key: Vec<String> = symbols.iter().take(3).cloned().collect();
        self.by_symbol_path.get(&key)
    }

    /// The number of loops the catalog holds.
    #[cfg(test)]
    pub fn len(&self) -> usize {
        self.by_symbol_path.len()
    }

    /// Whether the catalog holds no loop at all.
    #[cfg(test)]
    pub fn is_empty(&self) -> bool {
        self.by_symbol_path.is_empty()
    }
}

/// Build the catalog out of the rows the Manager library holds.
///
/// The rows are read **once**, when the runtime refreshes its in-memory
/// object. Every later processing step works on that object, so the
/// execution path never reads the Manager library per review.
pub fn catalog_from_rows(rows: &[serde_json::Value]) -> ClosedLoopCatalog {
    let mut by_symbol_path: BTreeMap<Vec<String>, ClosedLoopIdentity> = BTreeMap::new();
    for row in rows {
        let Some(loop_key) = row
            .get("loop_key")
            .and_then(serde_json::Value::as_str)
            .map(|value| value.trim().to_owned())
            .filter(|value| !value.is_empty())
        else {
            continue;
        };
        let symbols: Vec<String> = row
            .get("path_symbols_json")
            .and_then(serde_json::Value::as_str)
            .and_then(|text| serde_json::from_str::<Vec<String>>(text).ok())
            .unwrap_or_default()
            .iter()
            .map(|item| item.trim().to_uppercase())
            .filter(|item| !item.is_empty())
            .take(3)
            .collect();
        if symbols.len() < 3 {
            continue;
        }
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
        by_symbol_path.insert(
            symbols,
            ClosedLoopIdentity {
                loop_key,
                pool_ids,
                single_tick_capacity_anchor_raw: capacity,
                single_tick_capacity_binding_pool_id: binding,
            },
        );
    }
    ClosedLoopCatalog { by_symbol_path }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn an_empty_catalog_never_matches() {
        let catalog = ClosedLoopCatalog::default();
        assert!(catalog.is_empty());
        assert!(catalog
            .identity_for(&["ETH".to_owned(), "AMZN".to_owned(), "AMD".to_owned()])
            .is_none());
    }

    #[test]
    fn the_reverse_path_never_borrows_the_other_loops_identity() {
        let mut catalog = ClosedLoopCatalog::default();
        catalog.by_symbol_path.insert(
            vec!["ETH".to_owned(), "AMZN".to_owned(), "AMD".to_owned()],
            ClosedLoopIdentity {
                loop_key: "a:b:c".to_owned(),
                pool_ids: vec!["p1".to_owned()],
                single_tick_capacity_anchor_raw: Some(42),
                single_tick_capacity_binding_pool_id: Some("p1".to_owned()),
            },
        );
        assert!(catalog
            .identity_for(&["ETH".to_owned(), "AMD".to_owned(), "AMZN".to_owned()])
            .is_none());
        assert_eq!(
            catalog
                .identity_for(&["ETH".to_owned(), "AMZN".to_owned(), "AMD".to_owned()])
                .map(|item| item.single_tick_capacity_anchor_raw),
            Some(Some(42))
        );
    }

    #[test]
    fn a_row_without_a_symbol_path_is_ignored() {
        let catalog = catalog_from_rows(&[serde_json::json!({
            "loop_key": "a:b:c",
            "path_symbols_json": "[]",
            "pool_ids_json": "[]",
        })]);
        assert!(catalog.is_empty());
    }

    #[test]
    fn a_published_capacity_is_read_as_a_decimal_string() {
        let catalog = catalog_from_rows(&[serde_json::json!({
            "loop_key": "a:b:c",
            "path_symbols_json": "[\"ETH\",\"AMZN\",\"AMD\",\"ETH\"]",
            "pool_ids_json": "[\"p1\"]",
            "single_tick_capacity_anchor_raw": "10888919926",
            "single_tick_capacity_binding_pool_id": "p1",
        })]);
        let identity = catalog
            .identity_for(&["ETH".to_owned(), "AMZN".to_owned(), "AMD".to_owned()])
            .expect("the loop is published");
        assert_eq!(identity.loop_key, "a:b:c");
        assert_eq!(identity.single_tick_capacity_anchor_raw, Some(10_888_919_926));
        assert_eq!(identity.single_tick_capacity_binding_pool_id.as_deref(), Some("p1"));
    }
}