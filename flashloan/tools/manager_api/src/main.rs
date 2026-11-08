use manager_api::{serve, serve_with_state, AppState};
use manager_contracts::TokenAuthorityMode;
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
    if let Ok(database_path) = env::var("MANAGER_DB_PATH") {
        serve_with_state(
            address,
            Arc::new(
                AppState::with_token_database(database_path)
                    .with_token_authority(token_authority)
                    .with_candidate_authority(candidate_authority)
                    .with_exchange_authority(exchange_authority)
                    .with_closed_loop_authority(closed_loop_authority)
                    .with_review_authority(review_authority)
                    .with_broadcast_authority(broadcast_authority)
                    .with_composite_authority(composite_authority),
            ),
        )
        .await?;
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
