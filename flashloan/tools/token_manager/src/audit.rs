use crate::classification::ClassificationResult;
use crate::model::{AdmissionStatus, LibraryKey};
use rusqlite::{params, Connection, OpenFlags};
use serde::Serialize;
use std::collections::{BTreeMap, BTreeSet};
use std::fmt;
use std::path::Path;

const PRIMARY_LIBRARIES: [LibraryKey; 3] = [LibraryKey::Core, LibraryKey::Stocks, LibraryKey::Test];

#[derive(Debug)]
pub enum AuditError {
    Sqlite(rusqlite::Error),
    PartialTargetSchema(Vec<String>),
    InvalidExistingStatus {
        token_key_id: String,
        library: String,
        status: String,
    },
}

impl fmt::Display for AuditError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Sqlite(error) => write!(formatter, "library audit SQLite error: {error}"),
            Self::PartialTargetSchema(missing) => write!(
                formatter,
                "target manager schema is partial; missing {}",
                missing.join(", ")
            ),
            Self::InvalidExistingStatus {
                token_key_id,
                library,
                status,
            } => write!(
                formatter,
                "token {token_key_id} has invalid existing status {status} in {library}"
            ),
        }
    }
}

impl std::error::Error for AuditError {}

impl From<rusqlite::Error> for AuditError {
    fn from(error: rusqlite::Error) -> Self {
        Self::Sqlite(error)
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TargetSchemaInfo {
    pub state: String,
    pub token_core: bool,
    pub token_stocks: bool,
    pub token_extension: bool,
    pub token_test: bool,
    pub token_library_catalog: bool,
}

impl TargetSchemaInfo {
    pub fn is_complete(&self) -> bool {
        self.token_core
            && self.token_stocks
            && self.token_extension
            && self.token_test
            && self.token_library_catalog
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProposedMembership {
    pub library: LibraryKey,
    pub token_key_id: String,
    pub admission_status: AdmissionStatus,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ExistingMembership {
    pub library: LibraryKey,
    pub token_key_id: String,
    pub admission_status: AdmissionStatus,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct MembershipIdentity {
    library: LibraryKey,
    token_key_id: String,
}

impl Ord for MembershipIdentity {
    fn cmp(&self, other: &Self) -> std::cmp::Ordering {
        self.library
            .as_str()
            .cmp(other.library.as_str())
            .then_with(|| self.token_key_id.cmp(&other.token_key_id))
    }
}

impl PartialOrd for MembershipIdentity {
    fn partial_cmp(&self, other: &Self) -> Option<std::cmp::Ordering> {
        Some(self.cmp(other))
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct MembershipChangeSample {
    pub library: String,
    pub token_key_id: String,
    pub previous_status: Option<String>,
    pub proposed_status: String,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct MembershipDiff {
    pub inserted: usize,
    pub status_updated: usize,
    pub removed: usize,
    pub insertion_samples: Vec<MembershipChangeSample>,
    pub status_update_samples: Vec<MembershipChangeSample>,
    pub removal_samples: Vec<MembershipChangeSample>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PrimaryLibraryConflict {
    pub token_key_id: String,
    pub libraries: Vec<String>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ConflictAudit {
    pub proposed_primary_conflicts: Vec<PrimaryLibraryConflict>,
    pub existing_primary_conflicts: Vec<PrimaryLibraryConflict>,
    pub proposal_vs_existing_primary_conflicts: Vec<PrimaryLibraryConflict>,
    pub extension_overlap_count: usize,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LibraryAdmissionAudit {
    pub proposed_by_status: BTreeMap<String, usize>,
    pub proposed_by_library: BTreeMap<String, BTreeMap<String, usize>>,
    pub existing_by_status: BTreeMap<String, usize>,
    pub existing_by_library: BTreeMap<String, BTreeMap<String, usize>>,
    pub pending_review_equivalent: usize,
    pub diff: MembershipDiff,
    pub conflicts: ConflictAudit,
}

pub fn proposed_memberships_from_classification(
    classification: &ClassificationResult,
) -> Vec<ProposedMembership> {
    classification
        .tokens
        .iter()
        .flat_map(|token| {
            token
                .memberships
                .iter()
                .map(|membership| ProposedMembership {
                    library: membership.library,
                    token_key_id: token.token.token_key_id.to_string(),
                    admission_status: membership.admission_status,
                })
        })
        .collect()
}

pub fn read_target_schema_and_memberships_read_only(
    path: impl AsRef<Path>,
    network: &str,
    chain_id: u64,
) -> Result<(TargetSchemaInfo, Vec<ExistingMembership>), AuditError> {
    let connection = Connection::open_with_flags(
        path,
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )?;
    let schema = read_target_schema(&connection)?;
    if schema.state == "absent" {
        return Ok((schema, Vec::new()));
    }
    if !schema.is_complete() {
        let missing = missing_schema_parts(&schema);
        return Err(AuditError::PartialTargetSchema(missing));
    }

    let mut statement = connection.prepare(
        "SELECT library_key, token_key_id, admission_status
         FROM token_library_catalog
         WHERE network = ?1 AND chain_id = ?2
         ORDER BY library_key, token_key_id",
    )?;
    let rows = statement.query_map(params![network, chain_id], |row| {
        Ok((
            row.get::<_, String>(0)?,
            row.get::<_, String>(1)?,
            row.get::<_, String>(2)?,
        ))
    })?;
    let mut memberships = Vec::new();
    for row in rows {
        let (library, token_key_id, status) = row?;
        let library_key =
            LibraryKey::from_key(&library).ok_or_else(|| AuditError::InvalidExistingStatus {
                token_key_id: token_key_id.clone(),
                library: library.clone(),
                status: status.clone(),
            })?;
        let admission_status = AdmissionStatus::from_storage(&status).ok_or_else(|| {
            AuditError::InvalidExistingStatus {
                token_key_id: token_key_id.clone(),
                library: library.clone(),
                status: status.clone(),
            }
        })?;
        memberships.push(ExistingMembership {
            library: library_key,
            token_key_id,
            admission_status,
        });
    }
    Ok((schema, memberships))
}

pub fn audit_library_memberships(
    proposed: &[ProposedMembership],
    existing: &[ExistingMembership],
) -> LibraryAdmissionAudit {
    let proposed_by_status = status_counts(
        proposed
            .iter()
            .map(|membership| membership.admission_status),
    );
    let existing_by_status = status_counts(
        existing
            .iter()
            .map(|membership| membership.admission_status),
    );
    let proposed_by_library = library_status_counts(
        proposed
            .iter()
            .map(|membership| (membership.library, membership.admission_status)),
    );
    let existing_by_library = library_status_counts(
        existing
            .iter()
            .map(|membership| (membership.library, membership.admission_status)),
    );

    let pending_review_equivalent = count_status(proposed, AdmissionStatus::CandidateOnly)
        + count_status(proposed, AdmissionStatus::PendingReview);
    let diff = diff_memberships(proposed, existing);
    let conflicts = conflict_audit(proposed, existing);

    LibraryAdmissionAudit {
        proposed_by_status,
        proposed_by_library,
        existing_by_status,
        existing_by_library,
        pending_review_equivalent,
        diff,
        conflicts,
    }
}

fn read_target_schema(connection: &Connection) -> Result<TargetSchemaInfo, AuditError> {
    let names = {
        let mut statement =
            connection.prepare("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")?;
        let rows = statement.query_map([], |row| row.get::<_, String>(0))?;
        rows.collect::<Result<BTreeSet<_>, _>>()?
    };
    let token_core = names.contains("token_core");
    let token_stocks = names.contains("token_stocks");
    let token_extension = names.contains("token_extension");
    let token_test = names.contains("token_test");
    let token_library_catalog = names.contains("token_library_catalog");
    let state = if !token_core
        && !token_stocks
        && !token_extension
        && !token_test
        && !token_library_catalog
    {
        "absent"
    } else if token_core && token_stocks && token_extension && token_test && token_library_catalog {
        "complete"
    } else {
        "partial"
    };
    Ok(TargetSchemaInfo {
        state: state.to_owned(),
        token_core,
        token_stocks,
        token_extension,
        token_test,
        token_library_catalog,
    })
}

fn missing_schema_parts(schema: &TargetSchemaInfo) -> Vec<String> {
    [
        ("token_core", schema.token_core),
        ("token_stocks", schema.token_stocks),
        ("token_extension", schema.token_extension),
        ("token_test", schema.token_test),
        ("token_library_catalog", schema.token_library_catalog),
    ]
    .into_iter()
    .filter(|(_, present)| !present)
    .map(|(name, _)| name.to_owned())
    .collect()
}

fn status_counts(statuses: impl IntoIterator<Item = AdmissionStatus>) -> BTreeMap<String, usize> {
    let mut counts = BTreeMap::new();
    for status in statuses {
        *counts.entry(status.as_str().to_owned()).or_default() += 1;
    }
    counts
}

fn library_status_counts(
    memberships: impl IntoIterator<Item = (LibraryKey, AdmissionStatus)>,
) -> BTreeMap<String, BTreeMap<String, usize>> {
    let mut counts = BTreeMap::<String, BTreeMap<String, usize>>::new();
    for (library, status) in memberships {
        *counts
            .entry(library.as_str().to_owned())
            .or_default()
            .entry(status.as_str().to_owned())
            .or_default() += 1;
    }
    counts
}

fn count_status(memberships: &[ProposedMembership], status: AdmissionStatus) -> usize {
    memberships
        .iter()
        .filter(|membership| membership.admission_status == status)
        .count()
}

fn diff_memberships(
    proposed: &[ProposedMembership],
    existing: &[ExistingMembership],
) -> MembershipDiff {
    let proposed_by_key = proposed
        .iter()
        .map(|membership| {
            (
                MembershipIdentity {
                    library: membership.library,
                    token_key_id: membership.token_key_id.clone(),
                },
                membership.admission_status,
            )
        })
        .collect::<BTreeMap<_, _>>();
    let existing_by_key = existing
        .iter()
        .map(|membership| {
            (
                MembershipIdentity {
                    library: membership.library,
                    token_key_id: membership.token_key_id.clone(),
                },
                membership.admission_status,
            )
        })
        .collect::<BTreeMap<_, _>>();

    let mut diff = MembershipDiff::default();
    for (identity, proposed_status) in &proposed_by_key {
        match existing_by_key.get(identity) {
            None => {
                diff.inserted += 1;
                push_sample(
                    &mut diff.insertion_samples,
                    identity,
                    None,
                    *proposed_status,
                );
            }
            Some(existing_status) if existing_status != proposed_status => {
                diff.status_updated += 1;
                push_sample(
                    &mut diff.status_update_samples,
                    identity,
                    Some(*existing_status),
                    *proposed_status,
                );
            }
            Some(_) => {}
        }
    }
    for (identity, existing_status) in &existing_by_key {
        if !proposed_by_key.contains_key(identity) {
            diff.removed += 1;
            push_sample(
                &mut diff.removal_samples,
                identity,
                Some(*existing_status),
                AdmissionStatus::Blocked,
            );
        }
    }
    diff
}

fn push_sample(
    samples: &mut Vec<MembershipChangeSample>,
    identity: &MembershipIdentity,
    previous_status: Option<AdmissionStatus>,
    proposed_status: AdmissionStatus,
) {
    if samples.len() >= 20 {
        return;
    }
    samples.push(MembershipChangeSample {
        library: identity.library.as_str().to_owned(),
        token_key_id: identity.token_key_id.clone(),
        previous_status: previous_status.map(|status| status.as_str().to_owned()),
        proposed_status: proposed_status.as_str().to_owned(),
    });
}

fn conflict_audit(
    proposed: &[ProposedMembership],
    existing: &[ExistingMembership],
) -> ConflictAudit {
    let proposed_primary = primary_by_token(
        proposed
            .iter()
            .map(|membership| (membership.library, membership.token_key_id.as_str())),
    );
    let existing_primary = primary_by_token(
        existing
            .iter()
            .map(|membership| (membership.library, membership.token_key_id.as_str())),
    );
    let proposed_primary_conflicts = to_conflicts(&proposed_primary);
    let existing_primary_conflicts = to_conflicts(&existing_primary);
    let proposal_vs_existing_primary_conflicts = proposed_primary
        .iter()
        .filter_map(|(token_key_id, proposed_libraries)| {
            let existing_libraries = existing_primary.get(token_key_id)?;
            (proposed_libraries != existing_libraries).then(|| {
                let mut libraries = proposed_libraries
                    .iter()
                    .map(|library| format!("proposed:{}", library.as_str()))
                    .chain(
                        existing_libraries
                            .iter()
                            .map(|library| format!("existing:{}", library.as_str())),
                    )
                    .collect::<Vec<_>>();
                libraries.sort();
                PrimaryLibraryConflict {
                    token_key_id: token_key_id.clone(),
                    libraries,
                }
            })
        })
        .collect();

    let extension_tokens = proposed
        .iter()
        .filter(|membership| membership.library == LibraryKey::Extension)
        .map(|membership| membership.token_key_id.as_str())
        .collect::<BTreeSet<_>>();
    let extension_overlap_count = proposed
        .iter()
        .filter(|membership| {
            membership.library != LibraryKey::Extension
                && extension_tokens.contains(membership.token_key_id.as_str())
        })
        .map(|membership| membership.token_key_id.as_str())
        .collect::<BTreeSet<_>>()
        .len();

    ConflictAudit {
        proposed_primary_conflicts,
        existing_primary_conflicts,
        proposal_vs_existing_primary_conflicts,
        extension_overlap_count,
    }
}

fn primary_by_token<'a>(
    memberships: impl IntoIterator<Item = (LibraryKey, &'a str)>,
) -> BTreeMap<String, BTreeSet<LibraryKey>> {
    let mut by_token = BTreeMap::<String, BTreeSet<LibraryKey>>::new();
    for (library, token_key_id) in memberships {
        if PRIMARY_LIBRARIES.contains(&library) {
            by_token
                .entry(token_key_id.to_owned())
                .or_default()
                .insert(library);
        }
    }
    by_token
}

fn to_conflicts(
    primary_by_token: &BTreeMap<String, BTreeSet<LibraryKey>>,
) -> Vec<PrimaryLibraryConflict> {
    primary_by_token
        .iter()
        .filter(|(_, libraries)| libraries.len() > 1)
        .map(|(token_key_id, libraries)| PrimaryLibraryConflict {
            token_key_id: token_key_id.clone(),
            libraries: libraries
                .iter()
                .map(|library| library.as_str().to_owned())
                .collect(),
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::{
        audit_library_memberships, read_target_schema_and_memberships_read_only,
        ExistingMembership, ProposedMembership,
    };
    use crate::model::{AdmissionStatus, LibraryKey, TokenWrite};
    use crate::store::write_library;
    use manager_contracts::TokenKeyId;
    use std::str::FromStr;

    const TOKEN_A: &str = "robinhood:4663:0x1111111111111111111111111111111111111111";
    const TOKEN_B: &str = "robinhood:4663:0x2222222222222222222222222222222222222222";

    fn proposed(
        library: LibraryKey,
        token_key_id: &str,
        admission_status: AdmissionStatus,
    ) -> ProposedMembership {
        ProposedMembership {
            library,
            token_key_id: token_key_id.to_owned(),
            admission_status,
        }
    }

    fn token_write(
        token_key_id: &str,
        library: LibraryKey,
        admission_status: AdmissionStatus,
    ) -> TokenWrite {
        TokenWrite {
            network: "robinhood".to_owned(),
            chain_id: 4663,
            token_key_id: TokenKeyId::from_str(token_key_id).unwrap(),
            v4_currency_key: token_key_id.rsplit(':').next().unwrap().to_owned(),
            token_address: token_key_id.rsplit(':').next().unwrap().to_owned(),
            token_symbol: if library == LibraryKey::Core {
                "USDG".to_owned()
            } else {
                "EXT".to_owned()
            },
            token_name: None,
            token_decimals: 18,
            token_kind: "erc20".to_owned(),
            is_native: false,
            is_wrapped_native: false,
            is_stable: false,
            admission_status,
            admission_source: "unit-test".to_owned(),
            metadata_source: "unit-test".to_owned(),
            metadata_checksum: format!("sha256:{token_key_id}"),
            source_json: Some(r#"{"fixture":"unit-test"}"#.to_owned()),
            first_seen_at: None,
            blocked_reason: None,
        }
    }

    #[test]
    fn status_summary_and_diff_are_deterministic() {
        let proposed_memberships = vec![
            proposed(LibraryKey::Core, TOKEN_A, AdmissionStatus::Admitted),
            proposed(
                LibraryKey::Extension,
                TOKEN_A,
                AdmissionStatus::CandidateOnly,
            ),
            proposed(LibraryKey::Stocks, TOKEN_B, AdmissionStatus::Admitted),
        ];
        let audit = audit_library_memberships(&proposed_memberships, &[]);

        assert_eq!(audit.proposed_by_status["admitted"], 2);
        assert_eq!(audit.proposed_by_status["candidate_only"], 1);
        assert_eq!(audit.pending_review_equivalent, 1);
        assert_eq!(audit.diff.inserted, 3);
        assert_eq!(audit.conflicts.extension_overlap_count, 1);
        assert!(audit.conflicts.proposed_primary_conflicts.is_empty());
    }

    #[test]
    fn primary_conflicts_are_reported_while_extension_overlap_is_allowed() {
        let proposed_memberships = vec![
            proposed(LibraryKey::Core, TOKEN_A, AdmissionStatus::Admitted),
            proposed(LibraryKey::Stocks, TOKEN_A, AdmissionStatus::Admitted),
            proposed(
                LibraryKey::Extension,
                TOKEN_A,
                AdmissionStatus::CandidateOnly,
            ),
        ];
        let audit = audit_library_memberships(&proposed_memberships, &[]);

        assert_eq!(audit.conflicts.proposed_primary_conflicts.len(), 1);
        assert_eq!(
            audit.conflicts.proposed_primary_conflicts[0].libraries,
            vec!["core", "stocks"]
        );
        assert_eq!(audit.conflicts.extension_overlap_count, 1);
    }

    #[test]
    fn target_schema_absent_is_a_safe_empty_baseline() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("legacy.sqlite3");
        rusqlite::Connection::open(&path).unwrap();

        let (schema, memberships) =
            read_target_schema_and_memberships_read_only(&path, "robinhood", 4663).unwrap();
        assert_eq!(schema.state, "absent");
        assert!(memberships.is_empty());
    }

    #[test]
    fn migrated_target_schema_is_detected_read_only() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("manager.sqlite3");
        manager_db::migrate(&path).unwrap();

        let (schema, memberships) =
            read_target_schema_and_memberships_read_only(&path, "robinhood", 4663).unwrap();
        assert_eq!(schema.state, "complete");
        assert!(schema.is_complete());
        assert!(memberships.is_empty());
    }

    #[test]
    fn migrated_target_rows_are_read_without_duplicating_existing_rows() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("manager.sqlite3");
        manager_db::migrate(&path).unwrap();
        let mut connection = rusqlite::Connection::open(&path).unwrap();
        write_library(
            &mut connection,
            LibraryKey::Core,
            "robinhood",
            4663,
            vec![token_write(
                TOKEN_A,
                LibraryKey::Core,
                AdmissionStatus::Admitted,
            )],
            Some("fixture"),
            "unit-test",
            "bootstrap",
        )
        .unwrap();
        write_library(
            &mut connection,
            LibraryKey::Extension,
            "robinhood",
            4663,
            vec![token_write(
                TOKEN_A,
                LibraryKey::Extension,
                AdmissionStatus::CandidateOnly,
            )],
            Some("fixture"),
            "unit-test",
            "bootstrap",
        )
        .unwrap();
        drop(connection);

        let (schema, memberships) =
            read_target_schema_and_memberships_read_only(&path, "robinhood", 4663).unwrap();
        assert_eq!(schema.state, "complete");
        assert_eq!(memberships.len(), 2);
        assert_eq!(memberships[0].admission_status, AdmissionStatus::Admitted);
        assert_eq!(
            memberships[1].admission_status,
            AdmissionStatus::CandidateOnly
        );
    }

    #[test]
    fn existing_status_changes_and_removals_are_reported() {
        let proposed_memberships = vec![proposed(
            LibraryKey::Core,
            TOKEN_A,
            AdmissionStatus::Admitted,
        )];
        let existing_memberships = vec![
            ExistingMembership {
                library: LibraryKey::Core,
                token_key_id: TOKEN_A.to_owned(),
                admission_status: AdmissionStatus::PendingReview,
            },
            ExistingMembership {
                library: LibraryKey::Stocks,
                token_key_id: TOKEN_B.to_owned(),
                admission_status: AdmissionStatus::Admitted,
            },
        ];

        let audit = audit_library_memberships(&proposed_memberships, &existing_memberships);
        assert_eq!(audit.diff.status_updated, 1);
        assert_eq!(audit.diff.removed, 1);
        assert_eq!(
            audit.diff.status_update_samples[0].previous_status,
            Some("pending_review".to_owned())
        );
        assert_eq!(
            audit.diff.removal_samples[0].proposed_status,
            "blocked".to_owned()
        );
    }
}
