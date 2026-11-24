use manager_api::{serve, serve_with_state, AppState};
use manager_contracts::TokenAuthorityMode;
use broadcast_manager::BroadcastGuardPolicy;
use std::env;
use std::net::SocketAddr;
use std::sync::Arc;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let bind = env::var("MANAGER_API_BIND").unwrap_or_else(|_| "127.0.0.1:8091".to_owned());
    let address: SocketAddr = bind.parse()?;
    let token_authority = TokenAuthorityMode::from_env()?;
    let candidate_authority = authority_from_env("CANDIDATE_MANAGER_AUTHORITY")?;
    let exchange_authority = authority_from_env("CANDIDATE_EXCHANGE_MANAGER_AUTHORITY")?;
    let closed_loop_authority = authority_from_env("CLOSED_LOOP_MANAGER_AUTHORITY")?;
    let review_authority = authority_from_env("REVIEW_MANAGER_AUTHORITY")?;
    let broadcast_authority = authority_from_env("BROADCAST_MANAGER_AUTHORITY")?;
    let composite_authority = authority_from_env("COMPOSITE_MANAGER_AUTHORITY")?;
    let directional_pool_authority = authority_from_env("DIRECTIONAL_POOL_MANAGER_AUTHORITY")?;
    if let Ok(database_path) = env::var("MANAGER_DB_PATH") {
        let mut state = AppState::with_token_database(database_path)
            .with_token_authority(token_authority)
            .with_candidate_authority(candidate_authority)
            .with_exchange_authority(exchange_authority)
            .with_closed_loop_authority(closed_loop_authority)
            .with_review_authority(review_authority)
            .with_broadcast_authority(broadcast_authority)
            .with_composite_authority(composite_authority)
            .with_directional_pool_authority(directional_pool_authority)
            .with_broadcast_guard_policy(broadcast_guard_policy_from_env()?);
        // The network namespace is part of the row identity of every chain's
        // library. It is resolved per request from the chain id by default,
        // which keeps one process chain-safe. The operator can pin it when
        // the namespace is not known to the chain-id table.
        if let Ok(network) = env::var("MANAGER_NETWORK") {
            state = state.with_network_override(network);
        }
        serve_with_state(address, Arc::new(state)).await?;
    } else {
        serve(address).await?;
    }
    Ok(())
}

fn authority_from_env(name: &str) -> Result<TokenAuthorityMode, Box<dyn std::error::Error>> {
    match env::var(name) {
        Ok(value) => Ok(value.parse()?),
        Err(env::VarError::NotPresent) => Ok(TokenAuthorityMode::Legacy),
        Err(error) => Err(Box::new(error)),
    }
}

/// Read the broadcast guardrails from the environment.
///
/// These are **deployment configuration**, not an approval: they say
/// which chain this process serves, which account it signs with, how
/// much it may move in one round, and when it is allowed to move it.
///
/// A variable that is absent is a **missing guardrail**, not a passing
/// one. The broadcast layer refuses everything it cannot prove, so an
/// incomplete configuration makes the deployment refuse rather than
/// broadcast.
fn broadcast_guard_policy_from_env() -> Result<Option<BroadcastGuardPolicy>, Box<dyn std::error::Error>> {
    let chain_id = match env::var("BROADCAST_GUARD_CHAIN_ID") {
        Ok(value) => Some(value.parse::<i64>()?),
        Err(env::VarError::NotPresent) => None,
        Err(error) => return Err(Box::new(error)),
    };
    let signer_address = env::var("BROADCAST_GUARD_SIGNER_ADDRESS").ok();
    let max_amount_raw = match env::var("BROADCAST_GUARD_MAX_AMOUNT_RAW") {
        Ok(value) => Some(value.parse::<u128>()?),
        Err(env::VarError::NotPresent) => None,
        Err(error) => return Err(Box::new(error)),
    };
    let window_start = env::var("BROADCAST_GUARD_WINDOW_START").ok();
    let window_end = env::var("BROADCAST_GUARD_WINDOW_END").ok();
    if chain_id.is_none()
        && signer_address.is_none()
        && max_amount_raw.is_none()
        && window_start.is_none()
        && window_end.is_none()
    {
        // Nothing configured at all: the deployment has no guardrails,
        // so it has no broadcast either. The policy stays absent and
        // the broadcast layer refuses every submission with a reason.
        return Ok(None);
    }
    Ok(Some(BroadcastGuardPolicy {
        chain_id,
        signer_address,
        max_amount_raw,
        window_start,
        window_end,
    }))
}
