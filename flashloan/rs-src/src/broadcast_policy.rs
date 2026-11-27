//! The broadcast admission the Rust runtime owns.
//!
//! Python is legacy code.  The runtime does not take its word for whether a
//! broadcast may happen: the **review worker thread** produces the verdict
//! and the **broadcast worker thread** sends the transaction, and both live
//! on this side of the boundary.  What is left of the Python configuration is
//! the environment description of the route, and the runtime reads the two
//! authorization fields it carries as *legacy input* — recorded for the
//! operator, never a gate.
//!
//! The decision has three parts.
//!
//! * **the signer** — a private key the runtime can sign with.  It comes
//!   from the task configuration when the configuration carries one, and
//!   from the environment otherwise.  Without a signer there is nothing to
//!   send, so the broadcast is refused.
//! * **the switch** — the chain-scoped `*_ALLOW_OFFICIAL_V4_ARB_BROADCAST`
//!   flag together with `DEX_AUTO_EXECUTE_VERIFIED_OPPORTUNITIES`.  **Both
//!   default to open.**  The mainnet system does not ask for a second
//!   authorization: a trading opportunity is a live phenomenon and a
//!   historical record of it does not reproduce, so waiting for an operator
//!   to re-authorize a route that is profitable *now* loses the trade.  An
//!   operator can still close the switch by setting the flag to `false`.
//! * **the profit** — judged by the runtime itself, not by the reviewer: the
//!   review has to report the route as profitable and the runtime gates
//!   (gas, capacity, economic floor, hop price) have to pass.  That part
//!   lives with the broadcast path and is not repeated here.
//!
//! The runtime normalizes the task configuration **once**, at the boundary
//! where a task is accepted, so that every later reader — the review
//! worker, the routing decision, the broadcast worker and the record
//! writer — sees the same admission and cannot disagree about it.

use serde_json::{json, Value};

/// The chain that is allowed to broadcast, and the signer that signs it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct BroadcastPolicy {
    /// Whether the runtime may broadcast at all.
    pub broadcast_enabled: bool,
    /// The private key the runtime signs a broadcast with, if there is one.
    pub signer_private_key: Option<String>,
    /// Where the signer came from: `task_config` or the name of the
    /// environment variable it was read from.
    pub signer_source: Option<String>,
    /// Whether the authorization switch is open.
    pub switch_open: bool,
    /// The environment variable that decided the switch, if one did.
    pub switch_source: Option<String>,
    /// What the legacy Python configuration said.
    pub legacy_broadcast_enabled: bool,
    /// Why the decision is what it is.
    pub reasons: Vec<String>,
}

impl BroadcastPolicy {
    /// The decision as it is written into the task configuration.
    pub fn to_json(&self, chain_id: Option<i64>) -> Value {
        json!({
            "decidedBy": "rust_runtime",
            "chainId": chain_id,
            "broadcastEnabled": self.broadcast_enabled,
            "switchOpen": self.switch_open,
            "switchSource": self.switch_source,
            "signerConfigured": self.signer_private_key.is_some(),
            "signerSource": self.signer_source,
            "legacyBroadcastEnabled": self.legacy_broadcast_enabled,
            "reasons": self.reasons,
        })
    }
}

/// The chain-scoped prefix of the environment names.
fn chain_prefix(chain_id: Option<i64>) -> &'static str {
    match chain_id {
        // The Robinhood mainnet chain.  Every other chain — the testnet
        // included — uses the testnet prefix, which is what the legacy
        // configuration did as well.
        Some(4663) => "ROBINHOOD_MAINNET",
        _ => "ROBINHOOD_TESTNET",
    }
}

/// The environment names that carry the authorization switch.
fn switch_env_names(chain_id: Option<i64>) -> Vec<String> {
    vec![
        format!("{}_ALLOW_OFFICIAL_V4_ARB_BROADCAST", chain_prefix(chain_id)),
        "OFFICIAL_V4_ALLOW_ARB_BROADCAST".to_string(),
        "DEX_AUTO_EXECUTE_VERIFIED_OPPORTUNITIES".to_string(),
    ]
}

/// The environment names that carry the signer, in the order they are tried.
fn signer_env_names(chain_id: Option<i64>) -> Vec<String> {
    let prefix = chain_prefix(chain_id);
    vec![
        format!("{prefix}_DEX_PRIVATE_KEY"),
        format!("{prefix}_EXECUTION_PRIVATE_KEY"),
        "DEX_EXECUTION_PRIVATE_KEY".to_string(),
        "OFFICIAL_V4_PRIVATE_KEY".to_string(),
        "DEX_OFFICIAL_V4_PRIVATE_KEY".to_string(),
        // The liquidation signer stays the last resort: the operator has
        // instructed that the mainnet suite signs with the same key.
        "LIQUIDATION_EXECUTION_PRIVATE_KEY".to_string(),
    ]
}

/// Whether a value names an enabled flag.
fn is_enabled(value: &str) -> bool {
    matches!(
        value.trim().to_lowercase().as_str(),
        "1" | "true" | "yes" | "on"
    )
}

/// Read the switch.
///
/// The switch is **open by default**: an operator who wants the broadcasts
/// to stop has to say so.  A name that is set to `false` closes the switch;
/// a name that is set to `true` opens it whatever the others say.  A name
/// that is not set at all says nothing.
fn read_switch<F>(chain_id: Option<i64>, env: &F) -> (bool, Option<String>)
where
    F: Fn(&str) -> Option<String>,
{
    let mut closed_by: Option<String> = None;
    for name in switch_env_names(chain_id) {
        let Some(value) = env(&name) else {
            continue;
        };
        if value.trim().is_empty() {
            continue;
        }
        if is_enabled(&value) {
            return (true, Some(name));
        }
        if closed_by.is_none() {
            closed_by = Some(name);
        }
    }
    match closed_by {
        Some(name) => (false, Some(name)),
        None => (true, None),
    }
}

/// Read the signer.
fn read_signer<F>(config: &Value, chain_id: Option<i64>, env: &F) -> (Option<String>, Option<String>)
where
    F: Fn(&str) -> Option<String>,
{
    if let Some(key) = config
        .get("broadcastPrivateKey")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
    {
        return (Some(key.to_string()), Some("task_config".to_string()));
    }
    for name in signer_env_names(chain_id) {
        if let Some(key) = env(&name) {
            let key = key.trim().to_string();
            if !key.is_empty() {
                return (Some(key), Some(name));
            }
        }
    }
    (None, None)
}

/// Decide the admission for one task.
pub fn resolve<F>(chain_id: Option<i64>, config: &Value, env: &F) -> BroadcastPolicy
where
    F: Fn(&str) -> Option<String>,
{
    let legacy_broadcast_enabled = config
        .get("broadcastEnabled")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let (switch_open, switch_source) = read_switch(chain_id, env);
    let (signer_private_key, signer_source) = read_signer(config, chain_id, env);
    let mut reasons: Vec<String> = Vec::new();
    if !switch_open {
        reasons.push(format!(
            "the broadcast switch is closed by {}",
            switch_source.clone().unwrap_or_else(|| "the environment".to_string())
        ));
    }
    if signer_private_key.is_none() {
        reasons.push("no signer private key is available".to_string());
    }
    let broadcast_enabled = switch_open && signer_private_key.is_some();
    BroadcastPolicy {
        broadcast_enabled,
        signer_private_key,
        signer_source,
        switch_open,
        switch_source,
        legacy_broadcast_enabled,
        reasons,
    }
}

/// Decide the admission with the real environment.
pub fn resolve_in_environment(chain_id: Option<i64>, config: &Value) -> BroadcastPolicy {
    resolve(chain_id, config, &|name| std::env::var(name).ok())
}

/// Normalize a task configuration so that the admission is decided once.
///
/// The returned configuration is what every later stage reads: the review
/// worker, the routing decision, the broadcast worker and the record
/// writer.  A configuration that is not valid JSON is returned unchanged —
/// the worker that owns it reports the parse failure with its own words,
/// which is more useful than a second, vaguer error from here.
pub fn normalize_config(
    config_json: &str,
    chain_id: Option<i64>,
    execution_queue_enabled: bool,
) -> (String, BroadcastPolicy) {
    let Ok(mut config) = serde_json::from_str::<Value>(config_json) else {
        let policy = BroadcastPolicy {
            broadcast_enabled: false,
            signer_private_key: None,
            signer_source: None,
            switch_open: false,
            switch_source: None,
            legacy_broadcast_enabled: false,
            reasons: vec!["the task configuration is not valid JSON".to_string()],
        };
        return (config_json.to_string(), policy);
    };
    let policy = resolve_in_environment(chain_id, &config);
    if !config.is_object() {
        return (config_json.to_string(), policy);
    }
    config["broadcastEnabled"] = json!(policy.broadcast_enabled);
    match policy.signer_private_key.as_deref() {
        Some(key) => config["broadcastPrivateKey"] = json!(key),
        None => config["broadcastPrivateKey"] = Value::Null,
    }
    // The execution queue is the **only** broadcast path: the review worker
    // thread never broadcasts by itself, so a task that cannot reach the
    // queue cannot be broadcast at all.
    config["deferBroadcast"] = json!(execution_queue_enabled);
    config["broadcastAdmission"] = policy.to_json(chain_id);
    (
        serde_json::to_string(&config).unwrap_or_else(|_| config_json.to_string()),
        policy,
    )
}

#[cfg(test)]
mod tests {
    use super::{normalize_config, resolve};
    use serde_json::json;
    use std::collections::HashMap;

    fn env_of<'a>(pairs: &'a [(&'a str, &'a str)]) -> impl Fn(&str) -> Option<String> + 'a {
        let map: HashMap<String, String> = pairs
            .iter()
            .map(|(name, value)| ((*name).to_string(), (*value).to_string()))
            .collect();
        move |name: &str| map.get(name).cloned()
    }

    #[test]
    fn the_switch_is_open_when_no_environment_name_says_otherwise() {
        let policy = resolve(Some(4663), &json!({}), &env_of(&[]));
        assert!(policy.switch_open);
        assert!(policy.switch_source.is_none());
    }

    #[test]
    fn the_switch_is_closed_by_an_explicit_false() {
        let policy = resolve(
            Some(4663),
            &json!({}),
            &env_of(&[("ROBINHOOD_MAINNET_ALLOW_OFFICIAL_V4_ARB_BROADCAST", "false")]),
        );
        assert!(!policy.switch_open);
        assert_eq!(
            policy.switch_source.as_deref(),
            Some("ROBINHOOD_MAINNET_ALLOW_OFFICIAL_V4_ARB_BROADCAST")
        );
    }

    #[test]
    fn an_explicit_true_opens_the_switch_whatever_the_other_names_say() {
        let policy = resolve(
            Some(4663),
            &json!({}),
            &env_of(&[
                ("ROBINHOOD_MAINNET_ALLOW_OFFICIAL_V4_ARB_BROADCAST", "false"),
                ("OFFICIAL_V4_ALLOW_ARB_BROADCAST", "true"),
            ]),
        );
        assert!(policy.switch_open);
    }

    #[test]
    fn the_signer_comes_from_the_environment_when_the_task_has_none() {
        let policy = resolve(
            Some(4663),
            &json!({"broadcastPrivateKey": null}),
            &env_of(&[("LIQUIDATION_EXECUTION_PRIVATE_KEY", "0xabc")]),
        );
        assert_eq!(policy.signer_private_key.as_deref(), Some("0xabc"));
        assert_eq!(
            policy.signer_source.as_deref(),
            Some("LIQUIDATION_EXECUTION_PRIVATE_KEY")
        );
    }

    #[test]
    fn the_task_configuration_wins_over_the_environment() {
        let policy = resolve(
            Some(4663),
            &json!({"broadcastPrivateKey": "0xfromconfig"}),
            &env_of(&[("LIQUIDATION_EXECUTION_PRIVATE_KEY", "0xabc")]),
        );
        assert_eq!(policy.signer_private_key.as_deref(), Some("0xfromconfig"));
        assert_eq!(policy.signer_source.as_deref(), Some("task_config"));
    }

    #[test]
    fn without_a_signer_the_broadcast_is_refused() {
        let policy = resolve(Some(4663), &json!({}), &env_of(&[]));
        assert!(!policy.broadcast_enabled);
        assert!(policy
            .reasons
            .iter()
            .any(|reason| reason.contains("no signer private key")));
    }

    #[test]
    fn the_legacy_flag_is_recorded_but_never_a_gate() {
        let policy = resolve(
            Some(4663),
            &json!({"broadcastEnabled": false}),
            &env_of(&[("LIQUIDATION_EXECUTION_PRIVATE_KEY", "0xabc")]),
        );
        assert!(!policy.legacy_broadcast_enabled);
        assert!(policy.broadcast_enabled);
    }

    #[test]
    fn the_normalization_writes_the_decision_back_into_the_configuration() {
        let config = json!({
            "rpcUrl": "http://127.0.0.1:1",
            "broadcastEnabled": false,
            "broadcastPrivateKey": null,
        });
        let (normalized, _policy) = normalize_config(
            &serde_json::to_string(&config).expect("the configuration serializes"),
            Some(4663),
            true,
        );
        let parsed: serde_json::Value =
            serde_json::from_str(&normalized).expect("the normalization is valid JSON");
        assert_eq!(parsed["deferBroadcast"], json!(true));
        assert_eq!(parsed["broadcastAdmission"]["decidedBy"], json!("rust_runtime"));
        assert_eq!(
            parsed["broadcastAdmission"]["legacyBroadcastEnabled"],
            json!(false)
        );
    }

    #[test]
    fn a_configuration_that_is_not_json_is_returned_unchanged() {
        let (normalized, policy) = normalize_config("not json", Some(4663), true);
        assert_eq!(normalized, "not json");
        assert!(!policy.broadcast_enabled);
    }
}
