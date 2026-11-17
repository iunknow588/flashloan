pub mod authority;
pub mod dto;
pub mod error;
pub mod ids;
pub mod pool;

/// Serialization helpers for amounts that do not fit a JSON number.
///
/// A folded single-tick capacity routinely exceeds `u64::MAX`, and
/// `serde_json` refuses to write it as a number: the failure is a hard
/// "number out of range" in the middle of building a whole Manager view,
/// which takes the view down rather than one field. The decimal string is
/// lossless, and the database and the consumer already use it.
pub mod amount;

pub use authority::{
    AuthorityModeError, TokenAuthorityMode, TOKEN_MANAGER_AUTHORITY_ENV,
    TOKEN_MANAGER_ROLLBACK_REASON_ENV,
};
pub use dto::{ManagerResponse, V4PoolKey};
pub use error::{ErrorCode, ManagerError};
pub use ids::{normalize_address, PairKeyId, RouteKeyId, TokenKeyId};
pub use pool::{
    keccak256, PoolKeyError, V4_DYNAMIC_FEE_FLAG, V4_MAX_FEE, V4_SPECIAL_FEE_THRESHOLD,
};

pub const MANAGER_DTO_VERSION: &str = "manager_dto_v1";
