//! Replays an attributable officer example as a separate scenario overlay.

use std::collections::HashSet;
use std::fs;
use std::path::Path;

use serde::{Deserialize, Serialize};

use crate::compiler::CompileReport;
use crate::error::{Result, SatnError};
use crate::midend::{MidendRun, TypedOperation, validate_officer_candidate};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct OfficerDecisionLedger {
    pub decisions: Vec<OfficerDecision>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct OfficerDecision {
    pub decision_id: String,
    pub connection_id: String,
    #[serde(default)]
    pub candidate_id: Option<String>,
    pub source_refs: Vec<String>,
    pub attribution: String,
    pub rationale: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct OfficerScenario {
    pub authority: String,
    pub base_id: String,
    pub baseline_branch: String,
    pub outcomes: Vec<OfficerOutcome>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct OfficerOutcome {
    pub decision_id: String,
    pub connection_id: String,
    pub baseline_candidate_id: Option<String>,
    pub officer_candidate_id: Option<String>,
    pub effective_candidate_id: Option<String>,
    pub status: OfficerOutcomeStatus,
    pub source_refs: Vec<String>,
    pub attribution: String,
    pub rationale: String,
    pub baseline_decision_id: Option<String>,
    pub baseline_decision_class: Option<String>,
    pub baseline_operation: Option<TypedOperation>,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum OfficerOutcomeStatus {
    Agreement,
    Divergence,
    Unavailable,
}

pub fn load_officer_decisions(path: &Path) -> Result<OfficerDecisionLedger> {
    Ok(serde_json::from_str(&fs::read_to_string(path)?)?)
}

pub fn apply_officer_decisions(
    report: &CompileReport,
    baseline: &MidendRun,
    ledger: &OfficerDecisionLedger,
) -> Result<(MidendRun, OfficerScenario)> {
    validate_ledger(ledger)?;

    let mut effective = baseline.clone();
    let mut outcomes = Vec::with_capacity(ledger.decisions.len());
    for decision in &ledger.decisions {
        let baseline_operation = baseline_operation_for(baseline, &decision.connection_id)?;
        let baseline_candidate_id = baseline_operation
            .as_ref()
            .and_then(TypedOperation::candidate_id)
            .map(str::to_string);
        let baseline_decision_id = baseline_operation.as_ref().map(operation_id);
        let baseline_decision_class = baseline_operation
            .as_ref()
            .map(|operation| operation.decision_class().to_string());

        let officer_candidate = match &decision.candidate_id {
            Some(candidate_id) => match report
                .candidates
                .iter()
                .find(|candidate| candidate.id == *candidate_id)
            {
                Some(_) => Some(
                    validate_officer_candidate(report, &decision.connection_id, candidate_id)
                        .map_err(|error| SatnError::InvalidInput(error.to_string()))?,
                ),
                None => None,
            },
            None => None,
        };
        let target_exists = report
            .connections
            .iter()
            .any(|connection| connection.id == decision.connection_id);

        let status = match (target_exists, officer_candidate) {
            (true, Some(candidate)) => {
                let status = if baseline_candidate_id.as_deref() == Some(&candidate.id) {
                    OfficerOutcomeStatus::Agreement
                } else {
                    OfficerOutcomeStatus::Divergence
                };
                replace_effective_selection(&mut effective, decision);
                status
            }
            _ => OfficerOutcomeStatus::Unavailable,
        };
        let effective_candidate_id = if status == OfficerOutcomeStatus::Unavailable {
            baseline_candidate_id.clone()
        } else {
            decision.candidate_id.clone()
        };

        outcomes.push(OfficerOutcome {
            decision_id: decision.decision_id.clone(),
            connection_id: decision.connection_id.clone(),
            baseline_candidate_id,
            officer_candidate_id: decision.candidate_id.clone(),
            effective_candidate_id,
            status,
            source_refs: decision.source_refs.clone(),
            attribution: decision.attribution.clone(),
            rationale: decision.rationale.clone(),
            baseline_decision_id,
            baseline_decision_class,
            baseline_operation,
        });
    }

    effective.status = if effective
        .operations
        .iter()
        .any(TypedOperation::is_unresolved)
    {
        "unresolved".to_string()
    } else {
        "replayed".to_string()
    };

    Ok((
        effective,
        OfficerScenario {
            authority: "officer-example".to_string(),
            base_id: baseline.base_id.clone(),
            baseline_branch: baseline.branch.clone(),
            outcomes,
        },
    ))
}

fn validate_ledger(ledger: &OfficerDecisionLedger) -> Result<()> {
    let mut decision_ids = HashSet::new();
    let mut connection_ids = HashSet::new();
    for decision in &ledger.decisions {
        if decision.decision_id.trim().is_empty()
            || decision.connection_id.trim().is_empty()
            || decision.attribution.trim().is_empty()
            || decision.rationale.trim().is_empty()
            || decision.source_refs.is_empty()
            || decision
                .source_refs
                .iter()
                .any(|reference| reference.trim().is_empty())
            || decision
                .candidate_id
                .as_ref()
                .is_some_and(|candidate_id| candidate_id.trim().is_empty())
        {
            return Err(invalid(
                "officer decisions require nonempty identities and attribution",
            ));
        }
        if !decision_ids.insert(&decision.decision_id) {
            return Err(invalid(format!(
                "duplicate officer decision id {}",
                decision.decision_id
            )));
        }
        if !connection_ids.insert(&decision.connection_id) {
            return Err(invalid(format!(
                "multiple officer decisions target connection {}",
                decision.connection_id
            )));
        }
    }
    Ok(())
}

fn baseline_operation_for(
    baseline: &MidendRun,
    connection_id: &str,
) -> Result<Option<TypedOperation>> {
    let mut matching = baseline
        .operations
        .iter()
        .filter(|operation| match operation {
            TypedOperation::SelectAlignment {
                connection_id: id, ..
            }
            | TypedOperation::Unresolved {
                connection_id: id, ..
            } => id == connection_id,
            TypedOperation::SelectCommunityAccess { .. }
            | TypedOperation::UnresolvedCommunityAccess { .. } => false,
        });
    let operation = matching.next().cloned();
    if matching.next().is_some() {
        return Err(invalid(format!(
            "baseline has multiple alignment decisions for connection {connection_id}"
        )));
    }
    Ok(operation)
}

fn operation_id(operation: &TypedOperation) -> String {
    match operation {
        TypedOperation::SelectAlignment { id, .. } | TypedOperation::Unresolved { id, .. } => {
            id.clone()
        }
        TypedOperation::SelectCommunityAccess { id, .. }
        | TypedOperation::UnresolvedCommunityAccess { id, .. } => id.clone(),
    }
}

fn replace_effective_selection(
    effective: &mut MidendRun,
    decision: &OfficerDecision,
) {
    let operation_id = format!("officer-ledger:{}", decision.decision_id);
    let operation = TypedOperation::SelectAlignment {
        id: operation_id.clone(),
        task_id: operation_id.clone(),
        attempt_id: operation_id,
        connection_id: decision.connection_id.clone(),
        candidate_id: decision
            .candidate_id
            .as_ref()
            .expect("only applicable decisions reach operation replacement")
            .clone(),
        decision_class: "mechanical".to_string(),
        provisional: false,
        reason: Some(format!(
            "Binding illustrative officer decision {} applied from ledger",
            decision.decision_id
        )),
        uncertainties: Vec::new(),
    };

    let index = effective
        .operations
        .iter()
        .position(|existing| match existing {
            TypedOperation::SelectAlignment { connection_id, .. }
            | TypedOperation::Unresolved { connection_id, .. } => {
                connection_id == &decision.connection_id
            }
            TypedOperation::SelectCommunityAccess { .. }
            | TypedOperation::UnresolvedCommunityAccess { .. } => false,
        });
    match index {
        Some(index) => effective.operations[index] = operation,
        None => effective.operations.push(operation),
    }
}

fn invalid(message: impl Into<String>) -> SatnError {
    SatnError::InvalidInput(message.into())
}
