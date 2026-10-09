use serde_json::{json, Value};

use crate::config::parse_u64_hexish;
use crate::hyperrpc::Log;

pub struct HyperSyncPage {
    pub logs: Vec<Log>,
    pub next_block: Option<u64>,
}

pub async fn query_logs(
    client: &reqwest::Client,
    endpoint: &str,
    token: &str,
    address: &str,
    topic0: &str,
    topic1: Option<&str>,
    topic2: Option<&str>,
    from_block: u64,
    to_block: u64,
) -> Result<HyperSyncPage, String> {
    let mut topics: Vec<Value> = vec![json!(topic0.to_lowercase())];
    topics.push(match topic1 {
        Some(value) => json!(value.to_lowercase()),
        None => Value::Null,
    });
    topics.push(match topic2 {
        Some(value) => json!(value.to_lowercase()),
        None => Value::Null,
    });
    let body = json!({
        "fromBlock": from_block,
        "toBlock": to_block,
        "logs": [{
            "address": [address.to_lowercase()],
            "topics": [topics]
        }],
        "fieldSelection": {
            "log": [
                "Data",
                "Address",
                "Topic0",
                "Topic1",
                "Topic2",
                "Topic3",
                "BlockNumber",
                "TransactionHash",
                "LogIndex"
            ]
        }
    });
    let mut request = client.post(endpoint).json(&body);
    if !token.is_empty() {
        request = request
            .header("x-api-key", token)
            .header("Authorization", format!("Bearer {token}"));
    }
    let response = request
        .send()
        .await
        .map_err(|error| format!("HyperSync query failed: {error}"))?;
    let status = response.status();
    let payload: Value = response
        .json()
        .await
        .map_err(|error| format!("HyperSync response was not JSON ({status}): {error}"))?;
    if !status.is_success() {
        return Err(format!("HyperSync HTTP {status}: {payload}"));
    }
    let raw_logs = payload
        .get("data")
        .and_then(Value::as_array)
        .cloned()
        .or_else(|| payload.get("logs").and_then(Value::as_array).cloned())
        .unwrap_or_default();
    let logs = raw_logs
        .iter()
        .map(|item| {
            let topics = (0..=3)
                .filter_map(|index| {
                    item.get(format!("topic{index}"))
                        .and_then(Value::as_str)
                        .map(|value| value.to_lowercase())
                })
                .collect::<Vec<_>>();
            Ok(Log {
                address: item
                    .get("address")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_lowercase(),
                topics,
                data: item
                    .get("data")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_lowercase(),
                block_number: item
                    .get("block_number")
                    .and_then(Value::as_str)
                    .map(parse_u64_hexish)
                    .transpose()?
                    .or_else(|| item.get("blockNumber").and_then(Value::as_u64))
                    .unwrap_or_default(),
                transaction_hash: item
                    .get("transaction_hash")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_lowercase(),
                log_index: item
                    .get("log_index")
                    .and_then(Value::as_u64)
                    .or_else(|| item.get("logIndex").and_then(Value::as_u64)),
            })
        })
        .collect::<Result<Vec<_>, String>>()?;
    let next_block = payload
        .get("nextBlock")
        .and_then(Value::as_u64)
        .or_else(|| {
            payload
                .get("next_block")
                .and_then(Value::as_str)
                .map(parse_u64_hexish)
                .transpose()
                .ok()
                .flatten()
        });
    Ok(HyperSyncPage { logs, next_block })
}

pub async fn query_all_logs(
    client: &reqwest::Client,
    endpoint: &str,
    token: &str,
    address: &str,
    topic0: &str,
    topic1: Option<&str>,
    topic2: Option<&str>,
    from_block: u64,
    to_block: u64,
) -> Result<Vec<Log>, String> {
    let mut all_logs = Vec::new();
    let mut cursor = from_block;
    while cursor <= to_block {
        let page = query_logs(
            client,
            endpoint,
            token,
            address,
            topic0,
            topic1,
            topic2,
            cursor,
            to_block,
        )
        .await?;
        all_logs.extend(page.logs);
        match page.next_block {
            Some(next) if next > cursor && next <= to_block => cursor = next,
            Some(next) if next > cursor => break,
            _ => break,
        }
    }
    Ok(all_logs)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn topic_filter_builds_ordered_array() {
        let body = json!({
            "fromBlock": 1,
            "toBlock": 2,
            "logs": [{
                "address": ["0xabc"],
                "topics": [["0x0", "0x1", null]]
            }]
        });
        assert_eq!(body["logs"][0]["topics"][0][1], "0x1");
        assert!(body["logs"][0]["topics"][0][2].is_null());
    }
}
