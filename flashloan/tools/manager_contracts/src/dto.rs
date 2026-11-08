use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct V4PoolKey {
    pub currency0: String,
    pub currency1: String,
    pub fee: u32,
    pub tick_spacing: i32,
    pub hooks: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ManagerResponse<T> {
    pub ok: bool,
    pub version: String,
    pub generated_at: String,
    pub chain_id: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub request_id: Option<String>,
    pub data: Option<T>,
    pub error: Option<crate::error::ManagerError>,
}

impl<T> ManagerResponse<T> {
    pub fn success(chain_id: Option<u64>, generated_at: impl Into<String>, data: T) -> Self {
        Self {
            ok: true,
            version: crate::MANAGER_DTO_VERSION.to_owned(),
            generated_at: generated_at.into(),
            chain_id,
            request_id: None,
            data: Some(data),
            error: None,
        }
    }

    pub fn failure(
        chain_id: Option<u64>,
        generated_at: impl Into<String>,
        error: crate::error::ManagerError,
    ) -> Self {
        Self {
            ok: false,
            version: crate::MANAGER_DTO_VERSION.to_owned(),
            generated_at: generated_at.into(),
            chain_id,
            request_id: None,
            data: None,
            error: Some(error),
        }
    }

    pub fn with_request_id(mut self, request_id: impl Into<String>) -> Self {
        self.request_id = Some(request_id.into());
        self
    }
}

#[cfg(test)]
mod tests {
    use super::V4PoolKey;

    #[test]
    fn pool_key_serialization_is_camel_case() {
        let key = V4PoolKey {
            currency0: "0x0000000000000000000000000000000000000000".to_owned(),
            currency1: "0x2222222222222222222222222222222222222222".to_owned(),
            fee: 3000,
            tick_spacing: 60,
            hooks: "0x0000000000000000000000000000000000000000".to_owned(),
        };
        let value = serde_json::to_value(key).unwrap();
        assert_eq!(value["tickSpacing"], 60);
        assert!(value.get("tick_spacing").is_none());
    }
}
