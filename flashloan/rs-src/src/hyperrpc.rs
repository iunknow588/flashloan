use serde::Deserialize;
use serde_json::{json, Value};

use crate::config::parse_u64_hexish;

#[derive(Clone, Debug, Deserialize)]
pub struct Log {
    pub address: String,
    pub topics: Vec<String>,
    pub data: String,
    pub block_number: u64,
    pub transaction_hash: String,
    pub log_index: Option<u64>,
}

async fn json_rpc(
    client: &reqwest::Client,
    endpoint: &str,
    method: &str,
    params: Value,
) -> Result<Value, String> {
    let response = client
        .post(endpoint)
        .json(&json!({
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params
        }))
        .send()
        .await
        .map_err(|error| format!("HyperRPC {method} request failed: {error}"))?;
    let payload: Value = response
        .json()
        .await
        .map_err(|error| format!("HyperRPC {method} response was not JSON: {error}"))?;
    if let Some(error) = payload.get("error") {
        return Err(format!("HyperRPC {method} error: {error}"));
    }
    payload
        .get("result")
        .cloned()
        .ok_or_else(|| format!("HyperRPC {method} response has no result"))
}

fn block_hex(value: u64) -> String {
    format!("0x{value:x}")
}

fn parse_log(value: &Value) -> Result<Log, String> {
    let topics = value
        .get("topics")
        .and_then(Value::as_array)
        .ok_or_else(|| "log missing topics".to_string())?
        .iter()
        .map(|topic| {
            topic
                .as_str()
                .map(|item| item.to_lowercase())
                .ok_or_else(|| "log topic is not a string".to_string())
        })
        .collect::<Result<Vec<_>, _>>()?;
    Ok(Log {
        address: value
            .get("address")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_lowercase(),
        topics,
        data: value
            .get("data")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_lowercase(),
        block_number: value
            .get("blockNumber")
            .and_then(Value::as_str)
            .map(parse_u64_hexish)
            .transpose()?
            .unwrap_or_default(),
        transaction_hash: value
            .get("transactionHash")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_lowercase(),
        log_index: value
            .get("logIndex")
            .and_then(Value::as_str)
            .map(parse_u64_hexish)
            .transpose()?,
    })
}

pub async fn get_logs(
    client: &reqwest::Client,
    endpoint: &str,
    address: &str,
    topics: &[Option<String>],
    from_block: u64,
    to_block: u64,
) -> Result<Vec<Log>, String> {
    let mut topic_filters: Vec<Value> = Vec::new();
    for topic in topics {
        topic_filters.push(match topic {
            Some(value) => json!(value.to_lowercase()),
            None => Value::Null,
        });
    }
    let params = json!([{
        "address": address.to_lowercase(),
        "fromBlock": block_hex(from_block),
        "toBlock": block_hex(to_block),
        "topics": topic_filters,
    }]);
    let result = json_rpc(client, endpoint, "eth_getLogs", params).await?;
    let logs = result
        .as_array()
        .ok_or_else(|| "eth_getLogs result is not an array".to_string())?;
    logs.iter().map(parse_log).collect()
}

pub async fn get_logs_filtered(
    client: &reqwest::Client,
    endpoint: &str,
    address: &str,
    topic0: &str,
    currency0: Option<&str>,
    currency1: Option<&str>,
    from_block: u64,
    to_block: u64,
) -> Result<Vec<Log>, String> {
    let mut topics: Vec<Option<String>> = vec![Some(topic0.to_lowercase())];
    topics.push(currency0.map(|value| value.to_lowercase()));
    topics.push(currency1.map(|value| value.to_lowercase()));
    get_logs(client, endpoint, address, &topics, from_block, to_block).await
}

pub async fn get_block_number(client: &reqwest::Client, endpoint: &str) -> Result<u64, String> {
    let result = json_rpc(client, endpoint, "eth_blockNumber", json!([])).await?;
    result
        .as_str()
        .map(parse_u64_hexish)
        .transpose()?
        .ok_or_else(|| "eth_blockNumber result is not a hex string".to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn block_hex_formats_as_json_rpc_quantity() {
        assert_eq!(block_hex(0), "0x0");
        assert_eq!(block_hex(4663), "0x1237");
    }

    #[test]
    fn parse_log_accepts_lowercase_hex_fields() {
        let value = json!({
            "address": "0x8366a39cc670b4001a1121b8f6a443a643e40951",
            "topics": [
                "0xabcd",
                "0x0000000000000000000000005fc5360d0400a0fd4f2af552add042d716f1d168"
            ],
            "data": "0x1234",
            "blockNumber": "0x1237",
            "transactionHash": "0xabcdef",
            "logIndex": "0x0"
        });
        let log = parse_log(&value).unwrap();
        assert_eq!(log.block_number, 4663);
        assert_eq!(log.topics[1], "0x0000000000000000000000005fc5360d0400a0fd4f2af552add042d716f1d168");
        assert_eq!(log.data, "0x1234");
    }
}
