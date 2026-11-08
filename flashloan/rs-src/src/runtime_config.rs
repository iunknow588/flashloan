use serde_json::{json, Value};
use std::sync::Mutex;

#[derive(Clone, Debug)]
struct RuntimeIdentity {
    chain_id: u64,
    network: String,
    database_path: String,
}

static ACTIVE_RUNTIME: Mutex<Option<RuntimeIdentity>> = Mutex::new(None);

pub fn set_active_runtime(chain_id: u64, network: String, database_path: String) -> Value {
    let mut guard = ACTIVE_RUNTIME.lock().unwrap();
    *guard = Some(RuntimeIdentity {
        chain_id,
        network: network.clone(),
        database_path: database_path.clone(),
    });
    json!({
        "ok": true,
        "active": true,
        "chainId": chain_id,
        "network": network,
        "databasePath": database_path,
    })
}

pub fn clear_active_runtime() -> Value {
    let mut guard = ACTIVE_RUNTIME.lock().unwrap();
    let had_active = guard.is_some();
    *guard = None;
    json!({
        "ok": had_active,
        "active": false,
    })
}

pub fn active_runtime() -> Value {
    let guard = ACTIVE_RUNTIME.lock().unwrap();
    match guard.as_ref() {
        Some(identity) => json!({
            "active": true,
            "chainId": identity.chain_id,
            "network": identity.network,
            "databasePath": identity.database_path,
        }),
        None => json!({
            "active": false,
        }),
    }
}

pub fn resolve_runtime_database(
    chain_id: Option<u64>,
    database_path: Option<String>,
) -> Result<(String, Value), String> {
    if let Some(path) = database_path {
        let trimmed = path.trim();
        if !trimmed.is_empty() {
            return Ok((
                trimmed.to_string(),
                json!({
                    "source": "explicit",
                    "databasePath": trimmed,
                }),
            ));
        }
    }

    let guard = ACTIVE_RUNTIME.lock().unwrap();
    let identity = guard
        .as_ref()
        .ok_or_else(|| "database_scope_required: no active rust runtime".to_string())?;
    if let Some(requested_chain_id) = chain_id {
        if identity.chain_id != requested_chain_id {
            return Err(format!(
                "database_scope_required: active chain {} does not match requested {}",
                identity.chain_id, requested_chain_id
            ));
        }
    }
    Ok((
        identity.database_path.clone(),
        json!({
            "source": "active_runtime",
            "chainId": identity.chain_id,
            "network": identity.network,
            "databasePath": identity.database_path,
        }),
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;

    static TEST_LOCK: Mutex<()> = Mutex::new(());

    #[test]
    fn explicit_path_wins_over_active_runtime() {
        let _guard = TEST_LOCK.lock().unwrap();
        set_active_runtime(
            4663,
            "robinhood".to_string(),
            "/mainnet.sqlite3".to_string(),
        );
        let (path, meta) =
            resolve_runtime_database(Some(4663), Some("/explicit.sqlite3".to_string())).unwrap();
        assert_eq!(path, "/explicit.sqlite3");
        assert_eq!(meta["source"], "explicit");
    }

    #[test]
    fn active_runtime_is_used_when_explicit_path_is_missing() {
        let _guard = TEST_LOCK.lock().unwrap();
        set_active_runtime(
            4663,
            "robinhood".to_string(),
            "/mainnet.sqlite3".to_string(),
        );
        let (path, meta) = resolve_runtime_database(Some(4663), None).unwrap();
        assert_eq!(path, "/mainnet.sqlite3");
        assert_eq!(meta["source"], "active_runtime");
    }

    #[test]
    fn missing_active_runtime_returns_scope_required() {
        let _guard = TEST_LOCK.lock().unwrap();
        clear_active_runtime();
        let error = resolve_runtime_database(None, None).unwrap_err();
        assert!(error.contains("database_scope_required"));
    }

    #[test]
    fn chain_mismatch_is_rejected() {
        let _guard = TEST_LOCK.lock().unwrap();
        set_active_runtime(
            4663,
            "robinhood".to_string(),
            "/mainnet.sqlite3".to_string(),
        );
        let error = resolve_runtime_database(Some(46630), None).unwrap_err();
        assert!(error.contains("does not match"));
    }

    #[test]
    fn clear_removes_active_runtime() {
        let _guard = TEST_LOCK.lock().unwrap();
        set_active_runtime(
            4663,
            "robinhood".to_string(),
            "/mainnet.sqlite3".to_string(),
        );
        let payload = clear_active_runtime();
        assert_eq!(payload["active"], false);
        assert_eq!(active_runtime()["active"], false);
    }
}
