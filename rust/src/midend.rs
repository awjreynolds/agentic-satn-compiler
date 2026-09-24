//! Compact decision orchestration over one mechanically prepared planning base.
//!
//! The module deliberately keeps provider calls at one small seam.  The base is
//! written once, task/attempt/result records are append-only, and replay only
//! applies retained typed operations.

use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};
use std::fmt::{Display, Formatter};
use std::fs::{self, File, OpenOptions};
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::time::Instant;

use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

use crate::compiler::{
    AccessObligation, Candidate, CommunityAccess, CompileReport, Connection, PreparedCompilation,
    RuralAccessOffer, RuralAccessPlanner, SourceCorridor,
};
pub use crate::judgment::{ChoiceAttempt, SpecialistAttempt};
use crate::judgment::{ChoiceRequest, ChoiceResult, CodexConfig, ProviderReceipt, TypeSafeConfig};
use crate::topography::TopographyAvailability;

const UNKNOWN: &str = "__unknown__";
const NEEDS_EVIDENCE: &str = "__needs_evidence__";
const NONE: &str = "__none__";
const PLANNING_BRIEF: &str = "Choose the strategic active-travel alignment connecting the named places from the supplied route alternatives. Preserve A-road corridor importance and existing cycle-alignment evidence. Weigh source-supported directness and continuity without invented numeric weights. A role's search cost is not a cross-role quality score. Route selection does not establish provision, safety, access, or adoption. If required evidence is missing, choose an explicit unresolved option.";
const RURAL_PLANNING_BRIEF: &str = "Choose one admitted rural community access path to the currently accepted frontier, which may be an accepted strategic spine or a useful qualified sourced urban entry. Apply the owner goals to minimise unnecessary added feeder network and prefer a comfortable complete child-to-accepted-terminal journey, using the supplied measured new-link and complete journey evidence, including destination journeys as diagnostic evidence rather than a mandatory city-centre objective, ordered elevation variation and sustained gradient where supported, and the two separately named moving-time scenarios where supplied (the BRouter Trekking estimate and the hill-neutral sensitivity, which is not an e-bike ETA). A qualified OSM place extent is a source-backed urban-entry terminal, not an authority boundary or proof of provision. Make a focused qualitative comparison: missing numeric weights alone do not require abstention. If unknown facts or route coverage remain material, or no defensible preference can be stated, the evidence is unsupported and you must choose an explicit unresolved option; do not force a choice. Do not invent numeric weights, facts, safety, provision, access or adoption claims, treat missing evidence as zero, or select a path outside this retained offer.";

#[derive(Debug)]
pub enum MidendError {
    Io(std::io::Error),
    Json(serde_json::Error),
    Invalid(String),
    UnfinishedAttempt { task_id: String },
}

impl Display for MidendError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Io(error) => write!(formatter, "history I/O failed: {error}"),
            Self::Json(error) => write!(formatter, "history JSON failed: {error}"),
            Self::Invalid(message) => write!(formatter, "invalid planning decision: {message}"),
            Self::UnfinishedAttempt { task_id } => write!(
                formatter,
                "unfinished planning provider attempt for task {task_id}; resume requires review"
            ),
        }
    }
}

impl std::error::Error for MidendError {}

impl From<std::io::Error> for MidendError {
    fn from(error: std::io::Error) -> Self {
        Self::Io(error)
    }
}

impl From<serde_json::Error> for MidendError {
    fn from(error: serde_json::Error) -> Self {
        Self::Json(error)
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct MidendConfig {
    pub branch: String,
    pub mode: String,
    pub jev_model: String,
    pub allow_provisional: bool,
}

impl MidendConfig {
    pub fn live(allow_provisional: bool) -> Self {
        Self {
            branch: "main".to_string(),
            mode: "live".to_string(),
            jev_model: "jev-latest".to_string(),
            allow_provisional,
        }
    }

    pub fn deterministic(allow_provisional: bool) -> Self {
        Self {
            branch: "main".to_string(),
            mode: "deterministic".to_string(),
            jev_model: "jev-latest".to_string(),
            allow_provisional,
        }
    }

    pub fn with_branch(mut self, branch: impl Into<String>) -> Self {
        self.branch = branch.into();
        self
    }

    pub fn with_jev_model(mut self, model: impl Into<String>) -> Self {
        self.jev_model = model.into();
        self
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct MidendProgress {
    pub stage: String,
    pub branch: String,
    pub task_id: Option<String>,
    pub provider: Option<String>,
    pub status: String,
    pub elapsed_ms: u128,
}

pub trait ChoiceProvider {
    fn classify_choice(&mut self, request: &ChoiceRequest) -> ChoiceAttempt;
}

impl ChoiceProvider for TypeSafeConfig {
    fn classify_choice(&mut self, request: &ChoiceRequest) -> ChoiceAttempt {
        self.classify_choice_attempt(request)
    }
}

pub trait SpecialistProvider {
    fn propose(&mut self, prompt: &str) -> SpecialistAttempt;
}

impl SpecialistProvider for CodexConfig {
    fn propose(&mut self, prompt: &str) -> SpecialistAttempt {
        crate::judgment::run_codex_attempt(self, prompt)
    }
}

pub struct ProviderSet<'a> {
    pub classifier: Option<&'a mut dyn ChoiceProvider>,
    pub specialist: Option<&'a mut dyn SpecialistProvider>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PlanningBase {
    pub base_id: String,
    pub report: CompileReport,
}

impl PlanningBase {
    pub fn from_report(report: CompileReport) -> Self {
        let base_id = format!("base:{}:{}", report.area_id, report.snapshot_id);
        Self { base_id, report }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct DecisionTask {
    pub task_id: String,
    pub base_id: String,
    pub connection_id: String,
    pub question_id: String,
    pub state: Value,
    pub options: BTreeMap<String, Value>,
}

impl DecisionTask {
    fn choice_request(&self) -> ChoiceRequest {
        if self.state.get("schema") == Some(&json!("satn-rust-rural-task/v1")) {
            let mut state = self.state.clone();
            if let Some(object) = state.as_object_mut() {
                object.remove("offer");
                object.remove("prior_decisions");
            }
            return ChoiceRequest::new(
                state,
                self.question_id.clone(),
                json!(RURAL_PLANNING_BRIEF),
                self.options.clone(),
            );
        }
        ChoiceRequest::new(
            self.state.clone(),
            self.question_id.clone(),
            json!(PLANNING_BRIEF),
            self.options.clone(),
        )
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(tag = "kind")]
pub enum TypedOperation {
    #[serde(rename = "select-alignment")]
    SelectAlignment {
        id: String,
        task_id: String,
        attempt_id: String,
        connection_id: String,
        candidate_id: String,
        decision_class: String,
        provisional: bool,
        reason: Option<String>,
        uncertainties: Vec<String>,
    },
    #[serde(rename = "unresolved")]
    Unresolved {
        id: String,
        task_id: String,
        attempt_id: String,
        connection_id: String,
        decision_class: String,
        marker: Option<String>,
        reason: String,
        uncertainties: Vec<String>,
    },
    #[serde(rename = "select-community-access")]
    SelectCommunityAccess {
        id: String,
        task_id: String,
        attempt_id: String,
        community_id: String,
        candidate_id: String,
        decision_class: String,
        provisional: bool,
        reason: Option<String>,
        uncertainties: Vec<String>,
        parent_community_id: Option<String>,
        root_spine_id: Option<String>,
        new_link_length_m: Option<f64>,
        full_access_length_m: Option<f64>,
    },
    #[serde(rename = "unresolved-community-access")]
    UnresolvedCommunityAccess {
        id: String,
        task_id: String,
        attempt_id: String,
        community_id: String,
        candidate_id: String,
        decision_class: String,
        marker: Option<String>,
        reason: String,
        uncertainties: Vec<String>,
        parent_community_id: Option<String>,
        root_spine_id: Option<String>,
        new_link_length_m: Option<f64>,
        full_access_length_m: Option<f64>,
    },
}

impl TypedOperation {
    pub fn candidate_id(&self) -> Option<&str> {
        match self {
            Self::SelectAlignment { candidate_id, .. }
            | Self::SelectCommunityAccess { candidate_id, .. } => Some(candidate_id),
            Self::Unresolved { .. } | Self::UnresolvedCommunityAccess { .. } => None,
        }
    }

    pub fn rural_candidate_id(&self) -> Option<&str> {
        match self {
            Self::SelectCommunityAccess { candidate_id, .. }
            | Self::UnresolvedCommunityAccess { candidate_id, .. } => Some(candidate_id),
            Self::SelectAlignment { .. } | Self::Unresolved { .. } => None,
        }
    }

    pub fn community_id(&self) -> Option<&str> {
        match self {
            Self::SelectCommunityAccess { community_id, .. }
            | Self::UnresolvedCommunityAccess { community_id, .. } => Some(community_id),
            Self::SelectAlignment { .. } | Self::Unresolved { .. } => None,
        }
    }

    pub fn decision_class(&self) -> &str {
        match self {
            Self::SelectAlignment { decision_class, .. }
            | Self::Unresolved { decision_class, .. }
            | Self::SelectCommunityAccess { decision_class, .. }
            | Self::UnresolvedCommunityAccess { decision_class, .. } => decision_class,
        }
    }

    pub fn is_provisional(&self) -> bool {
        matches!(
            self,
            Self::SelectAlignment {
                provisional: true,
                ..
            } | Self::SelectCommunityAccess {
                provisional: true,
                ..
            }
        )
    }

    pub fn is_unresolved(&self) -> bool {
        matches!(
            self,
            Self::Unresolved { .. } | Self::UnresolvedCommunityAccess { .. }
        )
    }

    pub fn is_rural(&self) -> bool {
        matches!(
            self,
            Self::SelectCommunityAccess { .. } | Self::UnresolvedCommunityAccess { .. }
        )
    }

    fn connection_id(&self) -> &str {
        match self {
            Self::SelectAlignment { connection_id, .. }
            | Self::Unresolved { connection_id, .. } => connection_id,
            Self::SelectCommunityAccess { community_id, .. }
            | Self::UnresolvedCommunityAccess { community_id, .. } => community_id,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
struct AttemptRecord {
    pub id: String,
    pub task_id: String,
    pub actor: String,
    pub status: String,
    pub choice: Option<ChoiceResult>,
    pub proposal: Option<Value>,
    pub receipt: ProviderReceipt,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
struct HistoryEvent {
    pub id: String,
    pub branch: String,
    pub parent: Option<String>,
    pub kind: String,
    pub task_id: Option<String>,
    pub task: Option<DecisionTask>,
    pub attempt_id: Option<String>,
    pub attempt: Option<AttemptRecord>,
    pub operation: Option<TypedOperation>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
struct BranchState {
    head: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct MidendRun {
    pub branch: String,
    pub base_id: String,
    pub status: String,
    pub task_ids: Vec<String>,
    pub operations: Vec<TypedOperation>,
    #[serde(default)]
    pub community_access: Vec<CommunityAccess>,
}

struct HistoryStore {
    base_path: PathBuf,
    events_path: PathBuf,
    branches_path: PathBuf,
}

impl HistoryStore {
    fn open(root: &Path) -> Result<Self, MidendError> {
        fs::create_dir_all(root)?;
        let store = Self {
            base_path: root.join("base.json"),
            events_path: root.join("events.jsonl"),
            branches_path: root.join("branches.json"),
        };
        if !store.branches_path.exists() {
            store.write_branches(&BTreeMap::from([(
                "main".to_string(),
                BranchState::default(),
            )]))?;
        }
        Ok(store)
    }

    fn ensure_base(&self, base: &PlanningBase) -> Result<PlanningBase, MidendError> {
        if self.base_path.exists() {
            let existing: PlanningBase =
                serde_json::from_str(&fs::read_to_string(&self.base_path)?)?;
            if existing.base_id != base.base_id {
                return Err(MidendError::Invalid(format!(
                    "history base {} does not match {}",
                    existing.base_id, base.base_id
                )));
            }
            // Compare the retained report with the exact representation that
            // would be written for this incoming report.  Derived floating
            // point fields can be one ULP away in memory from their persisted
            // JSON spelling; comparing the in-memory value would reject a
            // valid resume while comparing a text round-trip preserves strict
            // binding to the retained report.
            let incoming_persisted: CompileReport =
                serde_json::from_str(&serde_json::to_string(&base.report)?)?;
            if serde_json::to_value(&existing.report)? != serde_json::to_value(&incoming_persisted)?
            {
                return Err(MidendError::Invalid(
                    "incoming prepared report differs from the retained planning base".to_string(),
                ));
            }
            return Ok(existing);
        }
        write_json_atomic(&self.base_path, base)?;
        Ok(base.clone())
    }

    fn base(&self) -> Result<PlanningBase, MidendError> {
        if !self.base_path.exists() {
            return Err(MidendError::Invalid(
                "history has no retained base".to_string(),
            ));
        }
        Ok(serde_json::from_str(&fs::read_to_string(&self.base_path)?)?)
    }

    fn events(&self) -> Result<Vec<HistoryEvent>, MidendError> {
        if !self.events_path.exists() {
            return Ok(Vec::new());
        }
        let file = File::open(&self.events_path)?;
        BufReader::new(file)
            .lines()
            .filter(|line| line.as_ref().is_ok_and(|line| !line.trim().is_empty()))
            .map(|line| Ok(serde_json::from_str(&line?)?))
            .collect()
    }

    fn branches(&self) -> Result<BTreeMap<String, BranchState>, MidendError> {
        if !self.branches_path.exists() {
            return Ok(BTreeMap::from([(
                "main".to_string(),
                BranchState::default(),
            )]));
        }
        Ok(serde_json::from_str(&fs::read_to_string(
            &self.branches_path,
        )?)?)
    }

    fn write_branches(&self, branches: &BTreeMap<String, BranchState>) -> Result<(), MidendError> {
        write_json_atomic(&self.branches_path, branches)
    }

    fn head(&self, branch: &str) -> Result<Option<String>, MidendError> {
        self.branches()?
            .get(branch)
            .map(|state| state.head.clone())
            .ok_or_else(|| MidendError::Invalid(format!("unknown branch {branch}")))
    }

    fn chain(&self, branch: &str) -> Result<Vec<HistoryEvent>, MidendError> {
        let events = self.events()?;
        let by_id: HashMap<String, HistoryEvent> = events
            .iter()
            .cloned()
            .map(|event| (event.id.clone(), event))
            .collect();
        let mut current = self.head(branch)?;
        let mut chain = Vec::new();
        let mut seen = HashSet::new();
        while let Some(id) = current {
            if !seen.insert(id.clone()) {
                return Err(MidendError::Invalid("history event cycle".to_string()));
            }
            let event = by_id
                .get(&id)
                .cloned()
                .ok_or_else(|| MidendError::Invalid(format!("history event {id} is missing")))?;
            current = event.parent.clone();
            chain.push(event);
        }
        chain.reverse();
        Ok(chain)
    }

    fn append(&self, branch: &str, mut event: HistoryEvent) -> Result<String, MidendError> {
        let mut branches = self.branches()?;
        let state = branches
            .get(branch)
            .cloned()
            .ok_or_else(|| MidendError::Invalid(format!("unknown branch {branch}")))?;
        let event_id = format!("event:{}:{}", branch, self.events()?.len() + 1);
        event.id = event_id.clone();
        event.branch = branch.to_string();
        event.parent = state.head;
        let mut file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.events_path)?;
        serde_json::to_writer(&mut file, &event)?;
        file.write_all(b"\n")?;
        file.sync_data()?;
        branches.get_mut(branch).expect("branch checked above").head = Some(event_id.clone());
        self.write_branches(&branches)?;
        Ok(event_id)
    }

    fn pending_attempt(&self, branch: &str) -> Result<Option<String>, MidendError> {
        let mut started = BTreeMap::<String, String>::new();
        for event in self.chain(branch)? {
            match event.kind.as_str() {
                "attempt-started" => {
                    if let (Some(attempt_id), Some(task_id)) = (event.attempt_id, event.task_id) {
                        started.insert(attempt_id, task_id);
                    }
                }
                "attempt-result" => {
                    if let Some(attempt_id) = event.attempt_id {
                        started.remove(&attempt_id);
                    }
                }
                _ => {}
            }
        }
        Ok(started.into_values().next())
    }

    fn operations(&self, branch: &str) -> Result<Vec<TypedOperation>, MidendError> {
        Ok(self
            .chain(branch)?
            .into_iter()
            .filter_map(|event| event.operation)
            .collect())
    }

    fn task(&self, branch: &str, task_id: &str) -> Result<Option<DecisionTask>, MidendError> {
        Ok(self
            .chain(branch)?
            .into_iter()
            .find(|event| event.kind == "task" && event.task_id.as_deref() == Some(task_id))
            .and_then(|event| event.task))
    }

    fn attempt_result(
        &self,
        branch: &str,
        attempt_id: &str,
    ) -> Result<Option<AttemptRecord>, MidendError> {
        Ok(self
            .chain(branch)?
            .into_iter()
            .find(|event| {
                event.kind == "attempt-result" && event.attempt_id.as_deref() == Some(attempt_id)
            })
            .and_then(|event| event.attempt))
    }

    fn append_task(&self, branch: &str, task: DecisionTask) -> Result<String, MidendError> {
        self.append(
            branch,
            HistoryEvent {
                id: String::new(),
                branch: String::new(),
                parent: None,
                kind: "task".to_string(),
                task_id: Some(task.task_id.clone()),
                task: Some(task),
                attempt_id: None,
                attempt: None,
                operation: None,
            },
        )
    }

    fn append_attempt_started(
        &self,
        branch: &str,
        task_id: &str,
        attempt_id: &str,
        actor: &str,
        provider: &str,
        requested_model: &str,
    ) -> Result<String, MidendError> {
        self.append(
            branch,
            HistoryEvent {
                id: String::new(),
                branch: String::new(),
                parent: None,
                kind: "attempt-started".to_string(),
                task_id: Some(task_id.to_string()),
                task: None,
                attempt_id: Some(attempt_id.to_string()),
                attempt: Some(AttemptRecord {
                    id: attempt_id.to_string(),
                    task_id: task_id.to_string(),
                    actor: actor.to_string(),
                    status: "started".to_string(),
                    choice: None,
                    proposal: None,
                    receipt: ProviderReceipt {
                        provider: provider.to_string(),
                        requested_model: requested_model.to_string(),
                        observed_model: None,
                        status: "started".to_string(),
                        request_body: String::new(),
                        response_body: None,
                        error: None,
                        usage: None,
                    },
                }),
                operation: None,
            },
        )
    }

    fn append_attempt_result(
        &self,
        branch: &str,
        attempt: AttemptRecord,
    ) -> Result<String, MidendError> {
        self.append(
            branch,
            HistoryEvent {
                id: String::new(),
                branch: String::new(),
                parent: None,
                kind: "attempt-result".to_string(),
                task_id: Some(attempt.task_id.clone()),
                task: None,
                attempt_id: Some(attempt.id.clone()),
                attempt: Some(attempt),
                operation: None,
            },
        )
    }

    fn append_operation(
        &self,
        branch: &str,
        operation: TypedOperation,
    ) -> Result<String, MidendError> {
        self.append(
            branch,
            HistoryEvent {
                id: String::new(),
                branch: String::new(),
                parent: None,
                kind: "decision".to_string(),
                task_id: Some(operation_task_id(&operation).to_string()),
                task: None,
                attempt_id: Some(operation_attempt_id(&operation).to_string()),
                attempt: None,
                operation: Some(operation),
            },
        )
    }
}

fn write_json_atomic<T: Serialize>(path: &Path, value: &T) -> Result<(), MidendError> {
    let temporary = path.with_extension("tmp");
    fs::write(&temporary, serde_json::to_vec_pretty(value)?)?;
    fs::rename(temporary, path)?;
    Ok(())
}

fn operation_task_id(operation: &TypedOperation) -> &str {
    match operation {
        TypedOperation::SelectAlignment { task_id, .. }
        | TypedOperation::Unresolved { task_id, .. }
        | TypedOperation::SelectCommunityAccess { task_id, .. }
        | TypedOperation::UnresolvedCommunityAccess { task_id, .. } => task_id,
    }
}

fn operation_attempt_id(operation: &TypedOperation) -> &str {
    match operation {
        TypedOperation::SelectAlignment { attempt_id, .. }
        | TypedOperation::Unresolved { attempt_id, .. }
        | TypedOperation::SelectCommunityAccess { attempt_id, .. }
        | TypedOperation::UnresolvedCommunityAccess { attempt_id, .. } => attempt_id,
    }
}

fn choice_attempt_from_record(record: AttemptRecord) -> ChoiceAttempt {
    ChoiceAttempt {
        result: record.choice,
        receipt: record.receipt,
    }
}

fn specialist_attempt_from_record(record: AttemptRecord) -> SpecialistAttempt {
    SpecialistAttempt {
        response: record.proposal,
        receipt: record.receipt,
    }
}

pub fn run(
    root: &Path,
    report: CompileReport,
    config: MidendConfig,
    providers: ProviderSet<'_>,
    progress: &mut dyn FnMut(MidendProgress),
) -> Result<MidendRun, MidendError> {
    run_internal(root, report, config, providers, progress, None)
}

/// Run the prepared interurban and rural decision sequence while borrowing the
/// graph/elevation state held by one `PreparedCompilation`.
pub fn run_prepared(
    root: &Path,
    prepared: &PreparedCompilation,
    config: MidendConfig,
    providers: ProviderSet<'_>,
    progress: &mut dyn FnMut(MidendProgress),
) -> Result<MidendRun, MidendError> {
    let report = prepared.report.clone();
    let planner = prepared.rural_planner();
    run_internal(root, report, config, providers, progress, Some(planner))
}

fn run_internal(
    root: &Path,
    report: CompileReport,
    config: MidendConfig,
    mut providers: ProviderSet<'_>,
    progress: &mut dyn FnMut(MidendProgress),
    rural_planner: Option<RuralAccessPlanner<'_>>,
) -> Result<MidendRun, MidendError> {
    let started = Instant::now();
    let store = HistoryStore::open(root)?;
    let requested_base = PlanningBase::from_report(report);
    let base = store.ensure_base(&requested_base)?;
    if let Some(task_id) = store.pending_attempt(&config.branch)? {
        emit(
            progress,
            &config,
            started,
            Some(task_id.clone()),
            None,
            "checkpoint",
            "unfinished",
        );
        return Err(MidendError::UnfinishedAttempt { task_id });
    }
    let mut operations = store.operations(&config.branch)?;
    for operation in &operations {
        let task = store.task(&config.branch, operation_task_id(operation))?;
        validate_operation(
            &base.report,
            operation,
            config.allow_provisional,
            task.as_ref(),
        )?;
    }
    let mut completed_connections: BTreeSet<String> = operations
        .iter()
        .filter(|operation| !operation.is_rural())
        .map(|operation| operation.connection_id().to_string())
        .collect();
    let mut task_ids = Vec::new();
    let mut connections = base.report.connections.clone();
    connections.sort_by(|left, right| left.id.cmp(&right.id));

    for connection in connections {
        let generated_task = make_task(&base, &connection, &operations, config.allow_provisional);
        let retained_task = store.task(&config.branch, &generated_task.task_id)?;
        let task = retained_task.clone().unwrap_or(generated_task);
        task_ids.push(task.task_id.clone());
        if completed_connections.contains(&task.connection_id) {
            continue;
        }
        if retained_task.is_none() {
            store.append_task(&config.branch, task.clone())?;
        }
        let attempt_id = format!("attempt:{}:jev", task.task_id);
        let classifier_attempt =
            if let Some(record) = store.attempt_result(&config.branch, &attempt_id)? {
                let attempt = choice_attempt_from_record(record);
                emit(
                    progress,
                    &config,
                    started,
                    Some(task.task_id.clone()),
                    Some(attempt.receipt.provider.clone()),
                    "provider",
                    "resumed",
                );
                attempt
            } else {
                store.append_attempt_started(
                    &config.branch,
                    &task.task_id,
                    &attempt_id,
                    "classifier",
                    "typesafe",
                    &config.jev_model,
                )?;
                emit(
                    progress,
                    &config,
                    started,
                    Some(task.task_id.clone()),
                    Some("typesafe".to_string()),
                    "provider",
                    "started",
                );
                let attempt = match providers.classifier.as_deref_mut() {
                    Some(provider) if config.mode == "live" => {
                        provider.classify_choice(&task.choice_request())
                    }
                    Some(_) => skipped_attempt("typesafe", &config.jev_model, "deterministic mode"),
                    None => {
                        skipped_attempt("typesafe", &config.jev_model, "classifier is unconfigured")
                    }
                };
                let classifier_record = AttemptRecord {
                    id: attempt_id.clone(),
                    task_id: task.task_id.clone(),
                    actor: "classifier".to_string(),
                    status: attempt.receipt.status.clone(),
                    choice: attempt.result.clone(),
                    proposal: None,
                    receipt: attempt.receipt.clone(),
                };
                store.append_attempt_result(&config.branch, classifier_record)?;
                emit(
                    progress,
                    &config,
                    started,
                    Some(task.task_id.clone()),
                    Some(attempt.receipt.provider.clone()),
                    "provider",
                    &attempt.receipt.status,
                );
                attempt
            };

        let mut operation =
            classifier_operation(&base.report, &task, &attempt_id, &classifier_attempt);
        if operation.is_none()
            && should_escalate(&classifier_attempt)
            && config.mode == "live"
            && providers.specialist.is_some()
        {
            let specialist_attempt_id = format!("attempt:{}:specialist", task.task_id);
            let specialist_attempt = if let Some(record) =
                store.attempt_result(&config.branch, &specialist_attempt_id)?
            {
                let attempt = specialist_attempt_from_record(record);
                emit(
                    progress,
                    &config,
                    started,
                    Some(task.task_id.clone()),
                    Some(attempt.receipt.provider.clone()),
                    "provider",
                    "resumed",
                );
                attempt
            } else {
                store.append_attempt_started(
                    &config.branch,
                    &task.task_id,
                    &specialist_attempt_id,
                    "agent",
                    "codex-exec",
                    "configured",
                )?;
                emit(
                    progress,
                    &config,
                    started,
                    Some(task.task_id.clone()),
                    Some("codex-exec".to_string()),
                    "provider",
                    "started",
                );
                let attempt = providers
                    .specialist
                    .as_deref_mut()
                    .expect("specialist presence checked above")
                    .propose(&specialist_prompt(&task)?);
                let specialist_record = AttemptRecord {
                    id: specialist_attempt_id.clone(),
                    task_id: task.task_id.clone(),
                    actor: "agent".to_string(),
                    status: attempt.receipt.status.clone(),
                    choice: None,
                    proposal: attempt.response.clone(),
                    receipt: attempt.receipt.clone(),
                };
                store.append_attempt_result(&config.branch, specialist_record)?;
                emit(
                    progress,
                    &config,
                    started,
                    Some(task.task_id.clone()),
                    Some(attempt.receipt.provider.clone()),
                    "provider",
                    &attempt.receipt.status,
                );
                attempt
            };
            operation = specialist_operation(
                &base.report,
                &task,
                &specialist_attempt_id,
                &specialist_attempt,
                config.allow_provisional,
                classifier_marker(&classifier_attempt),
            );
            if operation.is_none() {
                operation = Some(unresolved_operation(
                    &task,
                    &specialist_attempt_id,
                    "mechanical",
                    "specialist did not produce a valid typed operation",
                    classifier_marker(&classifier_attempt),
                    Vec::new(),
                ));
            }
        }
        let operation = operation.unwrap_or_else(|| {
            unresolved_operation(
                &task,
                &attempt_id,
                classifier_decision_class(&classifier_attempt),
                "the admitted alignment remains unresolved",
                classifier_marker(&classifier_attempt),
                Vec::new(),
            )
        });
        validate_operation(
            &base.report,
            &operation,
            config.allow_provisional,
            Some(&task),
        )?;
        store.append_operation(&config.branch, operation.clone())?;
        completed_connections.insert(operation.connection_id().to_string());
        operations.push(operation);
        emit(
            progress,
            &config,
            started,
            Some(task.task_id),
            None,
            "decision",
            "recorded",
        );
    }

    let community_access = if let Some(planner) = rural_planner {
        run_rural_sequence(
            &store,
            &base,
            &config,
            &mut providers,
            progress,
            planner,
            &mut operations,
            &mut task_ids,
        )?
    } else if operations.iter().any(TypedOperation::is_rural) {
        records_from_history(&store, &config.branch, &operations)?
    } else {
        base.report.community_access.clone()
    };

    let status = if operations.iter().any(TypedOperation::is_unresolved) {
        "unresolved"
    } else {
        "completed"
    };
    Ok(MidendRun {
        branch: config.branch,
        base_id: base.base_id,
        status: status.to_string(),
        task_ids,
        operations,
        community_access,
    })
}

fn run_rural_sequence(
    store: &HistoryStore,
    base: &PlanningBase,
    config: &MidendConfig,
    providers: &mut ProviderSet<'_>,
    progress: &mut dyn FnMut(MidendProgress),
    planner: RuralAccessPlanner<'_>,
    operations: &mut Vec<TypedOperation>,
    task_ids: &mut Vec<String>,
) -> Result<Vec<CommunityAccess>, MidendError> {
    let started = Instant::now();
    let mut planner = planner;
    let selected_candidates = operations
        .iter()
        .filter_map(|operation| match operation {
            TypedOperation::SelectAlignment { candidate_id, .. } => base
                .report
                .candidates
                .iter()
                .find(|candidate| candidate.id == *candidate_id),
            _ => None,
        })
        .collect::<Vec<_>>();
    planner.add_selected_alignment_targets(&selected_candidates);
    let mut completed = operations
        .iter()
        .filter_map(|operation| operation.community_id().map(str::to_string))
        .collect::<BTreeSet<_>>();

    // Rebuild the accepted frontier from retained operations. This is the only
    // graph interaction on resume; provider receipts and task offers stay in
    // history and are never regenerated for a completed decision.
    for operation in operations.iter().filter(|operation| operation.is_rural()) {
        let task = store
            .task(&config.branch, operation_task_id(operation))?
            .ok_or_else(|| {
                MidendError::Invalid(format!(
                    "rural operation {} has no retained task",
                    operation_task_id(operation)
                ))
            })?;
        let candidate_id = operation.rural_candidate_id().ok_or_else(|| {
            MidendError::Invalid(format!(
                "rural operation {} has no offered candidate",
                operation_task_id(operation)
            ))
        })?;
        let retained_candidate = rural_candidate_from_task(&task, candidate_id)?;
        // A genuine graph gap is recorded after the planner has exhausted its
        // frontier. It has no pending offer to consume on resume, so replay
        // the retained typed operation without asking the planner to serve it.
        if retained_candidate.criterion == "unresolved-gap" {
            continue;
        }
        let offer = rural_offer_from_task(&task)?;
        let current = planner
            .offer_next()
            .map_err(|error| MidendError::Invalid(error.to_string()))?
            .ok_or_else(|| {
                MidendError::Invalid(format!(
                    "retained rural task {} has no current offer",
                    task.task_id
                ))
            })?;
        if current.community_id != offer.community_id {
            return Err(MidendError::Invalid(format!(
                "retained rural task {} does not match the accepted frontier offer {}",
                task.task_id, current.community_id
            )));
        }
        if retained_candidate
            .access
            .parent_community_id
            .as_ref()
            .is_some_and(|parent| {
                !operations
                    .iter()
                    .take_while(|prior| *prior != operation)
                    .any(|prior| {
                        prior.is_rural()
                            && prior.community_id() == Some(parent.as_str())
                            && !prior.is_unresolved()
                    })
            })
        {
            return Err(MidendError::Invalid(format!(
                "rural candidate {candidate_id} binds to an unresolved parent"
            )));
        }
        match operation {
            TypedOperation::SelectCommunityAccess { .. } => {
                let accepted = planner
                    .accept(candidate_id)
                    .map_err(|error| MidendError::Invalid(error.to_string()))?;
                if accepted.path_edge_ids != retained_candidate.access.path_edge_ids
                    || accepted.parent_community_id != retained_candidate.access.parent_community_id
                    || accepted.urban_entry != retained_candidate.access.urban_entry
                {
                    return Err(MidendError::Invalid(format!(
                        "rural candidate {candidate_id} changed while replaying task {}",
                        task.task_id
                    )));
                }
            }
            TypedOperation::UnresolvedCommunityAccess { .. } => {
                planner
                    .reject(&unresolved_reason(operation))
                    .map_err(|error| MidendError::Invalid(error.to_string()))?;
            }
            TypedOperation::SelectAlignment { .. } | TypedOperation::Unresolved { .. } => {
                return Err(MidendError::Invalid(
                    "urban operation entered rural frontier replay".to_string(),
                ));
            }
        }
    }

    loop {
        let Some(offer) = planner
            .offer_next()
            .map_err(|error| MidendError::Invalid(error.to_string()))?
        else {
            break;
        };
        let task = make_rural_task(base, &offer, config.allow_provisional)?;
        let retained_task = store.task(&config.branch, &task.task_id)?;
        let task = retained_task.clone().unwrap_or(task);
        if !task_ids.iter().any(|id| id == &task.task_id) {
            task_ids.push(task.task_id.clone());
        }
        if completed.contains(&offer.community_id) {
            return Err(MidendError::Invalid(format!(
                "rural community {} was already completed before its task",
                offer.community_id
            )));
        }
        if retained_task.is_none() {
            store.append_task(&config.branch, task.clone())?;
        }

        let has_retained_attempt = store
            .attempt_result(&config.branch, &format!("attempt:{}:jev", task.task_id))?
            .is_some()
            || store
                .attempt_result(
                    &config.branch,
                    &format!("attempt:{}:specialist", task.task_id),
                )?
                .is_some();
        let mechanical_candidate = (!has_retained_attempt)
            .then(|| mechanical_rural_candidate(&offer))
            .flatten();
        let operation = if let Some(candidate) = mechanical_candidate {
            rural_select_operation(
                &task,
                "mechanical:rural",
                candidate,
                "mechanically admissible rural candidate",
                Vec::new(),
                false,
            )
        } else {
            let attempt_id = format!("attempt:{}:jev", task.task_id);
            let classifier_attempt = classifier_attempt_for_task(
                store,
                config,
                providers,
                progress,
                &started,
                &task,
                &attempt_id,
            )?;
            let mut operation = rural_classifier_operation(&task, &attempt_id, &classifier_attempt);
            if operation.is_none()
                && should_escalate(&classifier_attempt)
                && config.mode == "live"
                && providers.specialist.is_some()
            {
                let specialist_attempt_id = format!("attempt:{}:specialist", task.task_id);
                let specialist_attempt = specialist_attempt_for_task(
                    store,
                    config,
                    providers,
                    progress,
                    &started,
                    &task,
                    &specialist_attempt_id,
                )?;
                operation = rural_specialist_operation(
                    &task,
                    &specialist_attempt_id,
                    &specialist_attempt,
                    config.allow_provisional,
                    classifier_marker(&classifier_attempt),
                );
            }
            operation.unwrap_or_else(|| {
                rural_unresolved_operation(
                    &task,
                    &attempt_id,
                    classifier_decision_class(&classifier_attempt),
                    "the admitted rural access choice remains unresolved",
                    classifier_marker(&classifier_attempt),
                    Vec::new(),
                )
            })
        };
        validate_operation(
            &base.report,
            &operation,
            config.allow_provisional,
            Some(&task),
        )?;
        let candidate_id = operation.rural_candidate_id().ok_or_else(|| {
            MidendError::Invalid("rural operation did not retain an offered candidate".to_string())
        })?;
        match &operation {
            TypedOperation::SelectCommunityAccess {
                parent_community_id,
                root_spine_id,
                new_link_length_m,
                full_access_length_m,
                ..
            } => {
                let accepted = planner
                    .accept(candidate_id)
                    .map_err(|error| MidendError::Invalid(error.to_string()))?;
                if accepted.path_edge_ids
                    != rural_candidate_from_task(&task, candidate_id)?
                        .access
                        .path_edge_ids
                    || accepted.parent_community_id.as_ref() != parent_community_id.as_ref()
                    || accepted.root_spine_id.as_ref() != root_spine_id.as_ref()
                    || accepted.new_link_length_m != *new_link_length_m
                    || accepted.full_access_length_m != *full_access_length_m
                    || accepted.urban_entry
                        != rural_candidate_from_task(&task, candidate_id)?
                            .access
                            .urban_entry
                {
                    return Err(MidendError::Invalid(format!(
                        "rural candidate {candidate_id} changed while accepting task {}",
                        task.task_id
                    )));
                }
            }
            TypedOperation::UnresolvedCommunityAccess { reason, .. } => {
                planner
                    .reject(reason)
                    .map_err(|error| MidendError::Invalid(error.to_string()))?;
            }
            TypedOperation::SelectAlignment { .. } | TypedOperation::Unresolved { .. } => {
                return Err(MidendError::Invalid(
                    "urban operation entered rural frontier processing".to_string(),
                ));
            }
        }
        store.append_operation(&config.branch, operation.clone())?;
        completed.insert(offer.community_id.clone());
        operations.push(operation);
        emit(
            progress,
            config,
            started,
            Some(task.task_id),
            None,
            "decision",
            "recorded-rural",
        );
    }

    let records = planner
        .into_records()
        .into_iter()
        .map(|mut access| {
            if let Some(operation) = operations.iter().rev().find(|operation| {
                operation.is_rural()
                    && operation.community_id() == Some(access.community_id.as_str())
            }) {
                access.decision_class = operation.decision_class().to_string();
                if let Some(reason) = operation_reason(operation) {
                    access.reason = reason.to_string();
                }
            }
            access
        })
        .collect::<Vec<_>>();

    // Persist genuine graph gaps as compact unresolved rural tasks so replay
    // can expose them without rebuilding the graph or elevation index.
    for access in &records {
        if completed.contains(&access.community_id) {
            continue;
        }
        let candidate = crate::compiler::RuralAccessCandidate {
            id: format!("rural:{}:gap", access.community_id),
            criterion: "unresolved-gap".to_string(),
            access: access.clone(),
            destination_evidence: Vec::new(),
        };
        let offer = RuralAccessOffer {
            community_id: access.community_id.clone(),
            community_name: access.name.clone(),
            candidates: vec![candidate],
        };
        let task = make_rural_task(base, &offer, config.allow_provisional)?;
        if store.task(&config.branch, &task.task_id)?.is_none() {
            store.append_task(&config.branch, task.clone())?;
        }
        let operation = rural_unresolved_operation(
            &task,
            "mechanical:rural-gap",
            "mechanical",
            &access.reason,
            Some(NEEDS_EVIDENCE.to_string()),
            vec![access.reason.clone()],
        );
        validate_operation(
            &base.report,
            &operation,
            config.allow_provisional,
            Some(&task),
        )?;
        store.append_operation(&config.branch, operation.clone())?;
        operations.push(operation);
    }
    // Return the exact compact facts retained with each accepted operation so
    // live output and offline replay share one byte-stable projection. The
    // planner remains responsible for frontier extension; history is the
    // durable public record of the accepted prefix.
    records_from_history(store, &config.branch, operations)
}

fn classifier_attempt_for_task(
    store: &HistoryStore,
    config: &MidendConfig,
    providers: &mut ProviderSet<'_>,
    progress: &mut dyn FnMut(MidendProgress),
    started: &Instant,
    task: &DecisionTask,
    attempt_id: &str,
) -> Result<ChoiceAttempt, MidendError> {
    if let Some(record) = store.attempt_result(&config.branch, attempt_id)? {
        let attempt = choice_attempt_from_record(record);
        emit(
            progress,
            config,
            *started,
            Some(task.task_id.clone()),
            Some(attempt.receipt.provider.clone()),
            "provider",
            "resumed",
        );
        return Ok(attempt);
    }
    store.append_attempt_started(
        &config.branch,
        &task.task_id,
        attempt_id,
        "classifier",
        "typesafe",
        &config.jev_model,
    )?;
    emit(
        progress,
        config,
        *started,
        Some(task.task_id.clone()),
        Some("typesafe".to_string()),
        "provider",
        "started",
    );
    let attempt = match providers.classifier.as_deref_mut() {
        Some(provider) if config.mode == "live" => provider.classify_choice(&task.choice_request()),
        Some(_) => skipped_attempt("typesafe", &config.jev_model, "deterministic mode"),
        None => skipped_attempt("typesafe", &config.jev_model, "classifier is unconfigured"),
    };
    store.append_attempt_result(
        &config.branch,
        AttemptRecord {
            id: attempt_id.to_string(),
            task_id: task.task_id.clone(),
            actor: "classifier".to_string(),
            status: attempt.receipt.status.clone(),
            choice: attempt.result.clone(),
            proposal: None,
            receipt: attempt.receipt.clone(),
        },
    )?;
    emit(
        progress,
        config,
        *started,
        Some(task.task_id.clone()),
        Some(attempt.receipt.provider.clone()),
        "provider",
        &attempt.receipt.status,
    );
    Ok(attempt)
}

fn specialist_attempt_for_task(
    store: &HistoryStore,
    config: &MidendConfig,
    providers: &mut ProviderSet<'_>,
    progress: &mut dyn FnMut(MidendProgress),
    started: &Instant,
    task: &DecisionTask,
    attempt_id: &str,
) -> Result<SpecialistAttempt, MidendError> {
    if let Some(record) = store.attempt_result(&config.branch, attempt_id)? {
        let attempt = specialist_attempt_from_record(record);
        emit(
            progress,
            config,
            *started,
            Some(task.task_id.clone()),
            Some(attempt.receipt.provider.clone()),
            "provider",
            "resumed",
        );
        return Ok(attempt);
    }
    store.append_attempt_started(
        &config.branch,
        &task.task_id,
        attempt_id,
        "agent",
        "codex-exec",
        "configured",
    )?;
    emit(
        progress,
        config,
        *started,
        Some(task.task_id.clone()),
        Some("codex-exec".to_string()),
        "provider",
        "started",
    );
    let attempt = providers
        .specialist
        .as_deref_mut()
        .ok_or_else(|| MidendError::Invalid("specialist provider is unavailable".to_string()))?
        .propose(&specialist_prompt(task)?);
    store.append_attempt_result(
        &config.branch,
        AttemptRecord {
            id: attempt_id.to_string(),
            task_id: task.task_id.clone(),
            actor: "agent".to_string(),
            status: attempt.receipt.status.clone(),
            choice: None,
            proposal: attempt.response.clone(),
            receipt: attempt.receipt.clone(),
        },
    )?;
    emit(
        progress,
        config,
        *started,
        Some(task.task_id.clone()),
        Some(attempt.receipt.provider.clone()),
        "provider",
        &attempt.receipt.status,
    );
    Ok(attempt)
}

pub fn replay(
    root: &Path,
    branch: &str,
    progress: &mut dyn FnMut(MidendProgress),
) -> Result<MidendRun, MidendError> {
    let started = Instant::now();
    let store = HistoryStore::open(root)?;
    let base = store.base()?;
    let operations = store.operations(branch)?;
    for operation in &operations {
        let task = store.task(branch, operation_task_id(operation))?;
        validate_operation(&base.report, operation, true, task.as_ref())?;
    }
    let task_ids = store
        .chain(branch)?
        .into_iter()
        .filter(|event| event.kind == "task")
        .filter_map(|event| event.task_id)
        .collect::<Vec<_>>();
    let config = MidendConfig::deterministic(false).with_branch(branch.to_string());
    let community_access = if operations.iter().any(TypedOperation::is_rural) {
        records_from_history(&store, branch, &operations)?
    } else {
        base.report.community_access.clone()
    };
    emit(
        progress,
        &config,
        started,
        None,
        None,
        "replay",
        "completed",
    );
    Ok(MidendRun {
        branch: branch.to_string(),
        base_id: base.base_id,
        status: if operations.iter().any(TypedOperation::is_unresolved) {
            "unresolved".to_string()
        } else {
            "replayed".to_string()
        },
        task_ids,
        operations,
        community_access,
    })
}

pub fn load_base(root: &Path) -> Result<PlanningBase, MidendError> {
    HistoryStore::open(root)?.base()
}

pub fn fork(
    root: &Path,
    source_branch: &str,
    target_branch: &str,
    before_task_id: &str,
) -> Result<(), MidendError> {
    let store = HistoryStore::open(root)?;
    let mut branches = store.branches()?;
    if branches.contains_key(target_branch) {
        return Err(MidendError::Invalid(format!(
            "branch {target_branch} already exists"
        )));
    }
    let chain = store.chain(source_branch)?;
    let task_event = chain
        .iter()
        .find(|event| event.kind == "task" && event.task_id.as_deref() == Some(before_task_id))
        .ok_or_else(|| MidendError::Invalid(format!("task {before_task_id} is not in branch")))?;
    branches.insert(
        target_branch.to_string(),
        BranchState {
            head: task_event.parent.clone(),
        },
    );
    store.write_branches(&branches)
}

fn make_rural_task(
    base: &PlanningBase,
    offer: &RuralAccessOffer,
    allow_provisional: bool,
) -> Result<DecisionTask, MidendError> {
    let mut options = offer
        .candidates
        .iter()
        .map(|candidate| {
            (
                candidate.id.clone(),
                json!({
                    "criterion": candidate.criterion,
                    "community_id": candidate.access.community_id,
                    "status": candidate.access.status,
                    "new_link_length_m": candidate.access.new_link_length_m,
                    "full_access_length_m": candidate.access.full_access_length_m,
                    "parent_community_id": candidate.access.parent_community_id,
                    "root_spine_id": candidate.access.root_spine_id,
                    "urban_entry": candidate.access.urban_entry,
                    "topography": topography_summary(candidate.access.full_access_topography.as_ref()),
                    "destination_evidence": candidate
                        .destination_evidence
                        .iter()
                        .map(compact_destination_evidence)
                        .collect::<Vec<_>>(),
                }),
            )
        })
        .collect::<BTreeMap<_, _>>();
    options.insert(
        UNKNOWN.to_string(),
        json!("record rural access as unknown and continue review"),
    );
    options.insert(
        NEEDS_EVIDENCE.to_string(),
        json!("request evidence before selecting rural access"),
    );
    options.insert(
        NONE.to_string(),
        json!("record that no supplied rural access option is adopted"),
    );
    Ok(DecisionTask {
        task_id: format!("task:rural:{}", offer.community_id),
        base_id: base.base_id.clone(),
        connection_id: format!("rural:{}", offer.community_id),
        question_id: format!("rural-access:{}", offer.community_id),
        state: json!({
            "schema": "satn-rust-rural-task/v1",
            "base_id": base.base_id,
            "snapshot_id": base.report.snapshot_id,
            "community": {
                "id": offer.community_id,
                "name": offer.community_name,
            },
            // The concrete offer is retained once. Options above are deliberately thin.
            "offer": offer,
            "planning_brief": RURAL_PLANNING_BRIEF,
            "policy": {"allow_provisional_choices": allow_provisional},
        }),
        options,
    })
}

fn rural_offer_from_task(task: &DecisionTask) -> Result<RuralAccessOffer, MidendError> {
    if task.state.get("schema") != Some(&json!("satn-rust-rural-task/v1")) {
        return Err(MidendError::Invalid(format!(
            "task {} is not a retained rural task",
            task.task_id
        )));
    }
    serde_json::from_value(
        task.state.get("offer").cloned().ok_or_else(|| {
            MidendError::Invalid(format!("rural task {} has no offer", task.task_id))
        })?,
    )
    .map_err(|error| {
        MidendError::Invalid(format!(
            "rural task {} has invalid offer: {error}",
            task.task_id
        ))
    })
}

fn rural_candidate_from_task(
    task: &DecisionTask,
    candidate_id: &str,
) -> Result<crate::compiler::RuralAccessCandidate, MidendError> {
    rural_offer_from_task(task)?
        .candidates
        .into_iter()
        .find(|candidate| candidate.id == candidate_id)
        .ok_or_else(|| {
            MidendError::Invalid(format!(
                "rural candidate {candidate_id} is not in task {}",
                task.task_id
            ))
        })
}

fn rural_select_operation(
    task: &DecisionTask,
    attempt_id: &str,
    candidate: &crate::compiler::RuralAccessCandidate,
    reason: &str,
    uncertainties: Vec<String>,
    provisional: bool,
) -> TypedOperation {
    let access = &candidate.access;
    TypedOperation::SelectCommunityAccess {
        id: format!("decision:{}:{}", task.task_id, candidate.id),
        task_id: task.task_id.clone(),
        attempt_id: attempt_id.to_string(),
        community_id: access.community_id.clone(),
        candidate_id: candidate.id.clone(),
        decision_class: if provisional {
            "agent".to_string()
        } else if attempt_id.starts_with("mechanical:") {
            "mechanical".to_string()
        } else {
            "classifier".to_string()
        },
        provisional,
        reason: provisional.then(|| reason.to_string()),
        uncertainties,
        parent_community_id: access.parent_community_id.clone(),
        root_spine_id: access.root_spine_id.clone(),
        new_link_length_m: access.new_link_length_m,
        full_access_length_m: access.full_access_length_m,
    }
}

fn rural_unresolved_operation(
    task: &DecisionTask,
    attempt_id: &str,
    decision_class: &str,
    reason: &str,
    marker: Option<String>,
    uncertainties: Vec<String>,
) -> TypedOperation {
    let candidate = task
        .state
        .get("offer")
        .and_then(|offer| offer.get("candidates"))
        .and_then(Value::as_array)
        .and_then(|candidates| candidates.first())
        .and_then(|candidate| {
            serde_json::from_value::<crate::compiler::RuralAccessCandidate>(candidate.clone()).ok()
        });
    let (
        community_id,
        candidate_id,
        parent_community_id,
        root_spine_id,
        new_link_length_m,
        full_access_length_m,
    ) = candidate
        .map(|candidate| {
            (
                candidate.access.community_id,
                candidate.id,
                candidate.access.parent_community_id,
                candidate.access.root_spine_id,
                candidate.access.new_link_length_m,
                candidate.access.full_access_length_m,
            )
        })
        .unwrap_or_else(|| {
            (
                task.connection_id.trim_start_matches("rural:").to_string(),
                String::new(),
                None,
                None,
                None,
                None,
            )
        });
    TypedOperation::UnresolvedCommunityAccess {
        id: format!("decision:{}:unresolved", task.task_id),
        task_id: task.task_id.clone(),
        attempt_id: attempt_id.to_string(),
        community_id,
        candidate_id,
        decision_class: decision_class.to_string(),
        marker,
        reason: reason.to_string(),
        uncertainties,
        parent_community_id,
        root_spine_id,
        new_link_length_m,
        full_access_length_m,
    }
}

fn rural_classifier_operation(
    task: &DecisionTask,
    attempt_id: &str,
    attempt: &ChoiceAttempt,
) -> Option<TypedOperation> {
    let result = attempt.result.as_ref()?;
    if matches!(result.choice.as_str(), UNKNOWN | NEEDS_EVIDENCE | NONE) {
        return None;
    }
    let candidate = rural_candidate_from_task(task, &result.choice).ok()?;
    Some(rural_select_operation(
        task,
        attempt_id,
        &candidate,
        "Classifier selected an admitted rural access alternative.",
        Vec::new(),
        false,
    ))
}

fn specialist_decision_reason(payload: &serde_json::Map<String, Value>) -> Option<&str> {
    payload
        .get("reason")
        .or_else(|| payload.get("decision_reason"))
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|reason| !reason.is_empty())
}

fn rural_specialist_operation(
    task: &DecisionTask,
    attempt_id: &str,
    attempt: &SpecialistAttempt,
    allow_provisional: bool,
    marker: Option<String>,
) -> Option<TypedOperation> {
    let response = attempt.response.as_ref()?;
    let proposal = response.get("proposal")?.as_object()?;
    let operation = proposal.get("operation")?.as_object()?;
    let kind = operation.get("kind")?.as_str()?;
    let payload = operation.get("payload")?.as_object()?;
    match kind {
        "select-community-access" => {
            if !allow_provisional || payload.get("provisional") != Some(&Value::Bool(true)) {
                return None;
            }
            let candidate_id = payload.get("candidate_id")?.as_str()?;
            let candidate = rural_candidate_from_task(task, candidate_id).ok()?;
            if payload
                .get("community_id")
                .and_then(Value::as_str)
                .is_some_and(|community_id| community_id != candidate.access.community_id)
            {
                return None;
            }
            let reason = specialist_decision_reason(payload)?;
            if reason.is_empty() {
                return None;
            }
            let uncertainties = payload
                .get("uncertainties")?
                .as_array()?
                .iter()
                .map(Value::as_str)
                .collect::<Option<Vec<_>>>()?
                .into_iter()
                .map(str::trim)
                .filter(|item| !item.is_empty())
                .map(str::to_string)
                .collect::<Vec<_>>();
            if uncertainties.is_empty() {
                return None;
            }
            Some(rural_select_operation(
                task,
                attempt_id,
                &candidate,
                reason,
                uncertainties,
                true,
            ))
        }
        "unresolved" => {
            let reason = specialist_decision_reason(payload)?;
            if reason.is_empty() {
                return None;
            }
            let uncertainties = payload
                .get("uncertainties")
                .and_then(Value::as_array)
                .map(|items| {
                    items
                        .iter()
                        .filter_map(Value::as_str)
                        .map(str::trim)
                        .filter(|item| !item.is_empty())
                        .map(str::to_string)
                        .collect::<Vec<_>>()
                })
                .unwrap_or_default();
            Some(rural_unresolved_operation(
                task,
                attempt_id,
                "agent",
                reason,
                marker,
                uncertainties,
            ))
        }
        _ => None,
    }
}

fn mechanical_rural_candidate(
    offer: &RuralAccessOffer,
) -> Option<&crate::compiler::RuralAccessCandidate> {
    if offer.candidates.len() == 1 {
        return offer.candidates.first();
    }
    offer.candidates.iter().find(|candidate| {
        offer
            .candidates
            .iter()
            .filter(|other| other.id != candidate.id)
            .all(|other| rural_dominates(candidate, other))
    })
}

fn rural_dominates(
    left: &crate::compiler::RuralAccessCandidate,
    right: &crate::compiler::RuralAccessCandidate,
) -> bool {
    let left_access = &left.access;
    let right_access = &right.access;
    let left_profile = left_access.full_access_topography.as_ref();
    let right_profile = right_access.full_access_topography.as_ref();
    let Some(base_left_metrics) = rural_metrics(left_access, left_profile) else {
        return false;
    };
    let Some(base_right_metrics) = rural_metrics(right_access, right_profile) else {
        return false;
    };
    let mut left_metrics = base_left_metrics.to_vec();
    let mut right_metrics = base_right_metrics.to_vec();
    if let (Some(left_seconds), Some(right_seconds)) = (
        rural_moving_time_seconds(left_profile),
        rural_moving_time_seconds(right_profile),
    ) {
        left_metrics.push(left_seconds);
        right_metrics.push(right_seconds);
    }
    if let (Some(left_seconds), Some(right_seconds)) = (
        rural_hill_neutral_seconds(left_profile),
        rural_hill_neutral_seconds(right_profile),
    ) {
        left_metrics.push(left_seconds);
        right_metrics.push(right_seconds);
    }
    let no_worse = left_metrics
        .iter()
        .zip(right_metrics.iter())
        .all(|(left, right)| left <= right);
    let strictly_better = left_metrics
        .iter()
        .zip(right_metrics.iter())
        .any(|(left, right)| left < right);
    no_worse && strictly_better
}

fn rural_metrics(
    access: &CommunityAccess,
    profile: Option<&crate::topography::RouteTopographyProfile>,
) -> Option<[f64; 4]> {
    let profile =
        profile.filter(|profile| profile.availability == TopographyAvailability::Available)?;
    let sustained = profile.sustained_gradient.as_ref()?.absolute_gradient_pct;
    let metrics = [
        access.new_link_length_m?,
        access.full_access_length_m?,
        profile.cumulative_elevation_variation_m?,
        sustained,
    ];
    metrics
        .iter()
        .all(|value| value.is_finite())
        .then_some(metrics)
}

fn rural_moving_time_seconds(
    profile: Option<&crate::topography::RouteTopographyProfile>,
) -> Option<f64> {
    let profile = profile?;
    let crate::travel_time::TravelTimeEstimate::Available { seconds, .. } =
        &profile.estimated_moving_time
    else {
        return None;
    };
    seconds.is_finite().then_some(*seconds)
}

fn rural_hill_neutral_seconds(
    profile: Option<&crate::topography::RouteTopographyProfile>,
) -> Option<f64> {
    let seconds = profile?.hill_neutral_moving_time.as_ref()?.seconds;
    seconds.is_finite().then_some(seconds)
}

fn topography_summary(profile: Option<&crate::topography::RouteTopographyProfile>) -> Value {
    let Some(profile) = profile else {
        return Value::Null;
    };
    json!({
        "availability": profile.availability,
        "reason": profile.reason,
        "coverage": {
            "route_length_m": profile.coverage.route_length_m,
            "start_m": profile.coverage.start_m,
            "end_m": profile.coverage.end_m,
            "maximum_gap_m": profile.coverage.maximum_gap_m,
            "sample_count": profile.coverage.sample_count,
        },
        "forward_ascent_m": profile.forward_ascent_m,
        "forward_descent_m": profile.forward_descent_m,
        "reverse_ascent_m": profile.reverse_ascent_m,
        "reverse_descent_m": profile.reverse_descent_m,
        "cumulative_elevation_variation_m": profile.cumulative_elevation_variation_m,
        "sustained_gradient": profile.sustained_gradient.as_ref().map(|gradient| json!({
            "gradient_pct": gradient.gradient_pct,
            "absolute_gradient_pct": gradient.absolute_gradient_pct,
            "interval_length_m": gradient.interval_length_m,
        })),
        "estimated_moving_time": compact_travel_time(&profile.estimated_moving_time),
        "hill_neutral_moving_time": compact_hill_neutral(profile.hill_neutral_moving_time.as_ref()),
    })
}

fn compact_travel_time(value: &crate::travel_time::TravelTimeEstimate) -> Value {
    match value {
        crate::travel_time::TravelTimeEstimate::Available {
            seconds,
            minutes,
            model,
        } => json!({
            "availability": "available",
            "seconds": seconds,
            "minutes": minutes,
            "model": model.name,
        }),
        crate::travel_time::TravelTimeEstimate::Unknown { reason, model } => json!({
            "availability": "unknown",
            "reason": reason,
            "model": model.name,
        }),
    }
}

fn compact_hill_neutral(value: Option<&crate::travel_time::HillNeutralMovingTime>) -> Value {
    let Some(value) = value else {
        return Value::Null;
    };
    json!({
        "label": value.label,
        "seconds": value.seconds,
        "minutes": value.minutes,
        "model": value.model.name,
    })
}

fn compact_destination_evidence(evidence: &crate::compiler::RuralDestinationEvidence) -> Value {
    json!({
        "destination_id": evidence.destination_id,
        "destination_name": evidence.destination_name,
        "status": evidence.status,
        "complete_route_length_m": evidence.complete_route_length_m,
        "topography": topography_summary(evidence.complete_route_topography.as_ref()),
        "reason": evidence.reason,
    })
}

fn operation_reason(operation: &TypedOperation) -> Option<&str> {
    match operation {
        TypedOperation::SelectAlignment { reason, .. }
        | TypedOperation::SelectCommunityAccess { reason, .. } => reason.as_deref(),
        TypedOperation::Unresolved { reason, .. }
        | TypedOperation::UnresolvedCommunityAccess { reason, .. } => Some(reason),
    }
}

fn unresolved_reason(operation: &TypedOperation) -> String {
    operation_reason(operation)
        .unwrap_or("rural access remains unresolved")
        .to_string()
}

fn clear_unselected_rural_projection(access: &mut CommunityAccess) {
    access.parent_community_id = None;
    access.parent_community_name = None;
    access.parent_junction_node = None;
    access.parent_junction_edge_id = None;
    access.parent_junction_fraction = None;
    access.parent_junction_remaining_m = None;
    access.root_spine_id = None;
    access.urban_entry = None;
    access.admission_order = None;
    access.attachment_depth = None;
    access.new_link_length_m = None;
    access.full_access_length_m = None;
    access.joined_spine_id = None;
    access.access_length_m = None;
    access.path_edge_ids.clear();
    access.path_start_fraction = None;
    access.path_end_fraction = None;
    access.path_geometry.clear();
    access.onward_destinations.clear();
    access.onward_benefits.clear();
    access.joined_spine_reference = None;
    access.new_link_topography = None;
    access.full_access_topography = None;
}

fn records_from_history(
    store: &HistoryStore,
    branch: &str,
    operations: &[TypedOperation],
) -> Result<Vec<CommunityAccess>, MidendError> {
    let mut records = Vec::new();
    for operation in operations.iter().filter(|operation| operation.is_rural()) {
        let task = store
            .task(branch, operation_task_id(operation))?
            .ok_or_else(|| {
                MidendError::Invalid(format!("missing task {}", operation_task_id(operation)))
            })?;
        let candidate_id = operation.rural_candidate_id().ok_or_else(|| {
            MidendError::Invalid(format!(
                "rural operation {} has no candidate",
                operation_task_id(operation)
            ))
        })?;
        let candidate = rural_candidate_from_task(&task, candidate_id)?;
        let mut access = candidate.access;
        access.decision_class = operation.decision_class().to_string();
        if let Some(reason) = operation_reason(operation) {
            access.reason = reason.to_string();
        }
        if operation.is_unresolved() {
            let is_network_gap =
                candidate.criterion == "unresolved-gap" || access.status == "network-gap";
            access.status = if is_network_gap {
                "network-gap"
            } else {
                "unresolved"
            }
            .to_string();
            access.is_primary = true;
            clear_unselected_rural_projection(&mut access);
        }
        records.retain(|existing: &CommunityAccess| existing.community_id != access.community_id);
        records.push(access);
    }
    records.sort_by(|left, right| left.community_id.cmp(&right.community_id));
    Ok(records)
}

fn make_task(
    base: &PlanningBase,
    connection: &Connection,
    prior_operations: &[TypedOperation],
    allow_provisional: bool,
) -> DecisionTask {
    let mut candidates = base
        .report
        .candidates
        .iter()
        .filter(|candidate| candidate.connection_id == connection.id)
        .collect::<Vec<_>>();
    candidates.sort_by(|left, right| left.id.cmp(&right.id));
    let summaries = candidates
        .iter()
        .map(|candidate| (candidate.id.clone(), candidate_summary(candidate)))
        .collect::<BTreeMap<_, _>>();
    let source_context = relevant_source_context(base, &candidates);
    let access_unknowns = relevant_access_unknowns(base, connection);
    let relevant_subjects = source_context
        .iter()
        .filter_map(|source| source.get("id").and_then(Value::as_str))
        .chain(
            access_unknowns
                .iter()
                .filter_map(|obligation| obligation.get("id").and_then(Value::as_str)),
        )
        .collect::<BTreeSet<_>>();
    let source_unknowns = base
        .report
        .unknown_facts
        .iter()
        .filter(|fact| relevant_subjects.contains(fact.subject.as_str()))
        .cloned()
        .collect::<Vec<_>>();
    let relevant_prior = prior_operations
        .iter()
        .filter(|operation| operation_relevant(base, connection, operation))
        .cloned()
        .map(|operation| serde_json::to_value(operation).unwrap_or(Value::Null))
        .collect::<Vec<_>>();
    let state = json!({
        "schema": "satn-rust-planning-task/v1",
        "base_id": base.base_id,
        "snapshot_id": base.report.snapshot_id,
        "connection": {
            "id": connection.id,
            "origin_place_id": connection.origin_place_id,
            "origin_name": connection.origin_name,
            "destination_place_id": connection.destination_place_id,
            "destination_name": connection.destination_name,
            "origin_node": connection.origin_node,
            "destination_node": connection.destination_node,
            "road_classes": connection.road_classes,
            "preferred_classes": connection.preferred_classes,
        },
        "candidates": summaries,
        "planning_brief": PLANNING_BRIEF,
        "relevant_sources": source_context,
        "source_unknowns": source_unknowns,
        "access_unknowns": access_unknowns,
        "destination_profile": base.report.destination_profile,
        "accounting": {
            "status": base.report.accounting.status,
            "complete": base.report.accounting.complete,
        },
        "prior_decisions": relevant_prior,
        "policy": {"allow_provisional_choices": allow_provisional},
    });
    let mut options = summaries;
    options.insert(
        UNKNOWN.to_string(),
        json!("record an unknown and continue review"),
    );
    options.insert(
        NEEDS_EVIDENCE.to_string(),
        json!("request evidence before selecting an alignment"),
    );
    options.insert(
        NONE.to_string(),
        json!("record that no supplied option is adopted"),
    );
    DecisionTask {
        task_id: format!("task:{connection_id}", connection_id = connection.id),
        base_id: base.base_id.clone(),
        connection_id: connection.id.clone(),
        question_id: format!("alignment:{}", connection.id),
        state,
        options,
    }
}

fn relevant_source_context(base: &PlanningBase, candidates: &[&Candidate]) -> Vec<Value> {
    let graph_edges = candidates
        .iter()
        .flat_map(|candidate| candidate.path_edge_ids.iter())
        .collect::<BTreeSet<_>>();
    base.report
        .source_inventory
        .iter()
        .filter(|source| {
            source
                .graph_edge_ids
                .iter()
                .any(|edge| graph_edges.contains(edge))
        })
        .map(source_summary)
        .collect()
}

fn source_summary(source: &SourceCorridor) -> Value {
    json!({
        "id": source.id,
        "reference": source.reference,
        "source_kind": source.source_kind,
        "source_id": source.source_id,
        "scope": source.scope,
        "baseline_role": source.baseline_role,
        "topology_status": source.topology_status,
        "attachment_status": source.attachment_status,
        "provision_status": source.provision_status,
        "graph_edge_count": source.graph_edge_ids.len(),
    })
}

fn relevant_access_unknowns(base: &PlanningBase, connection: &Connection) -> Vec<Value> {
    let endpoint_ids = [
        connection.origin_place_id.as_str(),
        connection.destination_place_id.as_str(),
    ];
    base.report
        .access_obligations
        .iter()
        .filter(|obligation| {
            endpoint_ids.iter().any(|endpoint| {
                obligation.id.ends_with(&format!(":{endpoint}"))
                    || obligation.source_id == *endpoint
            })
        })
        .map(access_obligation_summary)
        .collect()
}

fn access_obligation_summary(obligation: &AccessObligation) -> Value {
    json!({
        "id": obligation.id,
        "kind": obligation.kind,
        "source_id": obligation.source_id,
        "name": obligation.name,
        "access_point_status": obligation.access_point_status,
        "access_point_source_id": obligation.access_point_source_id,
        "disposition": obligation.disposition,
        "reason": obligation.reason,
    })
}

fn operation_relevant(
    base: &PlanningBase,
    connection: &Connection,
    operation: &TypedOperation,
) -> bool {
    let Some(prior_connection) = base
        .report
        .connections
        .iter()
        .find(|candidate| candidate.id == operation.connection_id())
    else {
        return false;
    };
    let shared_place = prior_connection.origin_place_id == connection.origin_place_id
        || prior_connection.origin_place_id == connection.destination_place_id
        || prior_connection.destination_place_id == connection.origin_place_id
        || prior_connection.destination_place_id == connection.destination_place_id;
    let shared_node = prior_connection.origin_node == connection.origin_node
        || prior_connection.origin_node == connection.destination_node
        || prior_connection.destination_node == connection.origin_node
        || prior_connection.destination_node == connection.destination_node;
    if prior_connection.id == connection.id || shared_place || shared_node {
        return true;
    }
    if prior_connection
        .cross_region_edge_ids
        .iter()
        .any(|edge| connection.cross_region_edge_ids.contains(edge))
    {
        return true;
    }
    let Some(prior_candidate_id) = operation.candidate_id() else {
        return false;
    };
    let Some(prior_candidate) = base
        .report
        .candidates
        .iter()
        .find(|candidate| candidate.id == prior_candidate_id)
    else {
        return false;
    };
    base.report
        .candidates
        .iter()
        .filter(|candidate| candidate.connection_id == connection.id)
        .any(|candidate| {
            prior_candidate
                .path_edge_ids
                .iter()
                .any(|edge| candidate.path_edge_ids.contains(edge))
        })
}

fn candidate_summary(candidate: &Candidate) -> Value {
    json!({
        "candidate_id": candidate.id,
        "connection_id": candidate.connection_id,
        "role": candidate.role,
        "role_aliases": candidate.role_aliases,
        "length_m": candidate.length_m,
        "search_cost_m": candidate.search_cost_m,
        "a_road_share": candidate.a_road_share,
        "ncn_share": candidate.ncn_share,
        "cycle_alignment_bases": candidate.cycle_alignment_bases,
        "topology_status": candidate.topology_status,
        "provision_status": candidate.provision_status,
        "path_edge_count": candidate.path_edge_ids.len(),
    })
}

fn classifier_operation(
    report: &CompileReport,
    task: &DecisionTask,
    attempt_id: &str,
    attempt: &ChoiceAttempt,
) -> Option<TypedOperation> {
    let result = attempt.result.as_ref()?;
    if result.choice == UNKNOWN || result.choice == NEEDS_EVIDENCE || result.choice == NONE {
        return None;
    }
    if !validate_candidate(report, task, &result.choice).is_ok() {
        return None;
    }
    Some(TypedOperation::SelectAlignment {
        id: format!("decision:{}:{}", task.task_id, result.choice),
        task_id: task.task_id.clone(),
        attempt_id: attempt_id.to_string(),
        connection_id: task.connection_id.clone(),
        candidate_id: result.choice.clone(),
        decision_class: "classifier".to_string(),
        provisional: false,
        reason: None,
        uncertainties: Vec::new(),
    })
}

fn specialist_operation(
    report: &CompileReport,
    task: &DecisionTask,
    attempt_id: &str,
    attempt: &SpecialistAttempt,
    allow_provisional: bool,
    marker: Option<String>,
) -> Option<TypedOperation> {
    let response = attempt.response.as_ref()?;
    let proposal = response.get("proposal")?.as_object()?;
    let operation = proposal.get("operation")?.as_object()?;
    let kind = operation.get("kind")?.as_str()?;
    let payload = operation.get("payload")?.as_object()?;
    match kind {
        "select-alignment" => {
            let candidate_id = payload.get("candidate_id")?.as_str()?;
            validate_candidate(report, task, candidate_id).ok()?;
            if payload
                .get("connection_id")
                .and_then(Value::as_str)
                .is_some_and(|connection_id| connection_id != task.connection_id)
            {
                return None;
            }
            if !allow_provisional || payload.get("provisional") != Some(&Value::Bool(true)) {
                return None;
            }
            let reason = specialist_decision_reason(payload)?;
            if reason.is_empty() {
                return None;
            }
            let uncertainties = payload
                .get("uncertainties")?
                .as_array()?
                .iter()
                .map(Value::as_str)
                .collect::<Option<Vec<_>>>()?
                .into_iter()
                .map(str::trim)
                .filter(|item| !item.is_empty())
                .map(str::to_string)
                .collect::<Vec<_>>();
            if uncertainties.is_empty() {
                return None;
            }
            Some(TypedOperation::SelectAlignment {
                id: format!("decision:{}:{}", task.task_id, candidate_id),
                task_id: task.task_id.clone(),
                attempt_id: attempt_id.to_string(),
                connection_id: task.connection_id.clone(),
                candidate_id: candidate_id.to_string(),
                decision_class: "agent".to_string(),
                provisional: true,
                reason: Some(reason.to_string()),
                uncertainties,
            })
        }
        "unresolved" => {
            let reason = specialist_decision_reason(payload)?;
            if reason.is_empty() {
                return None;
            }
            let uncertainties = payload
                .get("uncertainties")
                .and_then(Value::as_array)
                .map(|items| {
                    items
                        .iter()
                        .filter_map(Value::as_str)
                        .map(str::trim)
                        .filter(|item| !item.is_empty())
                        .map(str::to_string)
                        .collect::<Vec<_>>()
                })
                .unwrap_or_default();
            Some(unresolved_operation(
                task,
                attempt_id,
                "agent",
                reason,
                marker,
                uncertainties,
            ))
        }
        _ => None,
    }
}

fn validate_candidate<'a>(
    report: &'a CompileReport,
    task: &DecisionTask,
    candidate_id: &str,
) -> Result<&'a Candidate, MidendError> {
    let candidate = report
        .candidates
        .iter()
        .find(|candidate| candidate.id == candidate_id)
        .ok_or_else(|| MidendError::Invalid(format!("candidate {candidate_id} is not admitted")))?;
    if candidate.connection_id != task.connection_id {
        return Err(MidendError::Invalid(format!(
            "candidate {candidate_id} is outside connection {}",
            task.connection_id
        )));
    }
    if candidate.status.trim().is_empty()
        || !candidate.length_m.is_finite()
        || !candidate.search_cost_m.is_finite()
        || !candidate.a_road_share.is_finite()
        || !candidate.ncn_share.is_finite()
        || candidate.geometry.len() < 2
        || candidate
            .geometry
            .iter()
            .flatten()
            .any(|coordinate| !coordinate.is_finite())
        || candidate.path_edge_ids.is_empty()
    {
        return Err(MidendError::Invalid(format!(
            "candidate {candidate_id} has invalid admitted route data"
        )));
    }
    Ok(candidate)
}

pub(crate) fn validate_officer_candidate<'a>(
    report: &'a CompileReport,
    connection_id: &str,
    candidate_id: &str,
) -> Result<&'a Candidate, MidendError> {
    let task = DecisionTask {
        task_id: format!("officer-ledger:{connection_id}"),
        base_id: String::new(),
        connection_id: connection_id.to_string(),
        question_id: String::new(),
        state: Value::Null,
        options: BTreeMap::new(),
    };
    validate_candidate(report, &task, candidate_id)
}

fn validate_operation(
    report: &CompileReport,
    operation: &TypedOperation,
    allow_provisional: bool,
    task: Option<&DecisionTask>,
) -> Result<(), MidendError> {
    if operation.is_rural() {
        return validate_rural_operation(operation, allow_provisional, task);
    }
    let connection_id = operation.connection_id();
    if !report
        .connections
        .iter()
        .any(|connection| connection.id == connection_id)
    {
        return Err(MidendError::Invalid(format!(
            "operation references unadmitted connection {connection_id}"
        )));
    }
    match operation {
        TypedOperation::SelectAlignment {
            task_id,
            candidate_id,
            decision_class,
            provisional,
            reason,
            uncertainties,
            ..
        } => {
            if task_id != &format!("task:{connection_id}") {
                return Err(MidendError::Invalid(format!(
                    "select operation task {task_id} does not match connection {connection_id}"
                )));
            }
            let task = DecisionTask {
                task_id: task_id.clone(),
                base_id: String::new(),
                connection_id: connection_id.to_string(),
                question_id: String::new(),
                state: Value::Null,
                options: BTreeMap::new(),
            };
            validate_candidate(report, &task, candidate_id)?;
            if !matches!(
                decision_class.as_str(),
                "mechanical" | "classifier" | "agent"
            ) {
                return Err(MidendError::Invalid(format!(
                    "select operation has unsupported decision class {decision_class}"
                )));
            }
            if *provisional {
                if !allow_provisional {
                    return Err(MidendError::Invalid(
                        "provisional selection requires allow_provisional".to_string(),
                    ));
                }
                if reason
                    .as_deref()
                    .is_none_or(|value| value.trim().is_empty())
                {
                    return Err(MidendError::Invalid(
                        "provisional selection requires a reason".to_string(),
                    ));
                }
                if uncertainties.is_empty()
                    || uncertainties.iter().any(|item| item.trim().is_empty())
                {
                    return Err(MidendError::Invalid(
                        "provisional selection requires uncertainties".to_string(),
                    ));
                }
            }
        }
        TypedOperation::Unresolved {
            task_id,
            decision_class,
            reason,
            marker,
            uncertainties,
            ..
        } => {
            if task_id != &format!("task:{connection_id}") {
                return Err(MidendError::Invalid(format!(
                    "unresolved operation task {task_id} does not match connection {connection_id}"
                )));
            }
            if !matches!(
                decision_class.as_str(),
                "mechanical" | "classifier" | "agent"
            ) {
                return Err(MidendError::Invalid(format!(
                    "unresolved operation has unsupported decision class {decision_class}"
                )));
            }
            if reason.trim().is_empty() || uncertainties.iter().any(|item| item.trim().is_empty()) {
                return Err(MidendError::Invalid(
                    "unresolved operation needs a reason and nonblank uncertainties".to_string(),
                ));
            }
            if marker
                .as_deref()
                .is_some_and(|marker| !matches!(marker, UNKNOWN | NEEDS_EVIDENCE | NONE))
            {
                return Err(MidendError::Invalid(
                    "unresolved operation has an unknown marker".to_string(),
                ));
            }
        }
        TypedOperation::SelectCommunityAccess { .. }
        | TypedOperation::UnresolvedCommunityAccess { .. } => {
            return Err(MidendError::Invalid(
                "rural operation entered urban validation".to_string(),
            ));
        }
    }
    Ok(())
}

fn validate_rural_operation(
    operation: &TypedOperation,
    allow_provisional: bool,
    task: Option<&DecisionTask>,
) -> Result<(), MidendError> {
    let task = task.ok_or_else(|| {
        MidendError::Invalid(format!(
            "rural operation {} has no retained decision task",
            operation_task_id(operation)
        ))
    })?;
    if !task.task_id.starts_with("task:rural:")
        || task.state.get("schema") != Some(&json!("satn-rust-rural-task/v1"))
    {
        return Err(MidendError::Invalid(format!(
            "rural operation {} has an invalid retained task",
            operation_task_id(operation)
        )));
    }
    let (
        task_id,
        community_id,
        candidate_id,
        decision_class,
        provisional,
        reason,
        uncertainties,
        parent_community_id,
        root_spine_id,
        new_link_length_m,
        full_access_length_m,
        marker,
    ) = match operation {
        TypedOperation::SelectCommunityAccess {
            task_id,
            community_id,
            candidate_id,
            decision_class,
            provisional,
            reason,
            uncertainties,
            parent_community_id,
            root_spine_id,
            new_link_length_m,
            full_access_length_m,
            ..
        } => (
            task_id,
            community_id,
            candidate_id,
            decision_class,
            provisional,
            reason,
            uncertainties,
            parent_community_id,
            root_spine_id,
            new_link_length_m,
            full_access_length_m,
            None,
        ),
        TypedOperation::UnresolvedCommunityAccess {
            task_id,
            community_id,
            candidate_id,
            decision_class,
            reason,
            uncertainties,
            parent_community_id,
            root_spine_id,
            new_link_length_m,
            full_access_length_m,
            marker,
            ..
        } => (
            task_id,
            community_id,
            candidate_id,
            decision_class,
            &false,
            &Some(reason.clone()),
            uncertainties,
            parent_community_id,
            root_spine_id,
            new_link_length_m,
            full_access_length_m,
            marker.as_ref(),
        ),
        TypedOperation::SelectAlignment { .. } | TypedOperation::Unresolved { .. } => {
            return Err(MidendError::Invalid(
                "urban operation entered rural validation".to_string(),
            ));
        }
    };
    if task_id != &task.task_id || task_id != &format!("task:rural:{community_id}") {
        return Err(MidendError::Invalid(format!(
            "rural operation task {task_id} does not match community {community_id}"
        )));
    }
    if !matches!(
        decision_class.as_str(),
        "mechanical" | "classifier" | "agent"
    ) {
        return Err(MidendError::Invalid(format!(
            "rural operation has unsupported decision class {decision_class}"
        )));
    }
    let candidate = rural_candidate_from_task(task, candidate_id)?;
    let access = &candidate.access;
    if access.community_id != *community_id
        || access.parent_community_id.as_ref() != parent_community_id.as_ref()
        || access.root_spine_id.as_ref() != root_spine_id.as_ref()
        || access.new_link_length_m != *new_link_length_m
        || access.full_access_length_m != *full_access_length_m
    {
        return Err(MidendError::Invalid(format!(
            "rural operation {candidate_id} does not match its retained offer"
        )));
    }
    if let TypedOperation::SelectCommunityAccess { .. } = operation {
        if *provisional {
            if !allow_provisional {
                return Err(MidendError::Invalid(
                    "provisional rural selection requires allow_provisional".to_string(),
                ));
            }
            if reason
                .as_deref()
                .is_none_or(|value| value.trim().is_empty())
                || uncertainties.is_empty()
                || uncertainties.iter().any(|item| item.trim().is_empty())
            {
                return Err(MidendError::Invalid(
                    "provisional rural selection requires a reason and uncertainties".to_string(),
                ));
            }
        }
    } else if reason
        .as_deref()
        .is_none_or(|value| value.trim().is_empty())
        || uncertainties.iter().any(|item| item.trim().is_empty())
    {
        return Err(MidendError::Invalid(
            "unresolved rural operation needs a reason and nonblank uncertainties".to_string(),
        ));
    }
    if marker.is_some_and(|marker| ![UNKNOWN, NEEDS_EVIDENCE, NONE].contains(&marker.as_str())) {
        return Err(MidendError::Invalid(
            "unresolved rural operation has an unknown marker".to_string(),
        ));
    }
    Ok(())
}

fn should_escalate(attempt: &ChoiceAttempt) -> bool {
    attempt.result.is_none()
        || attempt.result.as_ref().is_some_and(|result| {
            result.choice == UNKNOWN || result.choice == NEEDS_EVIDENCE || result.choice == NONE
        })
}

fn classifier_marker(attempt: &ChoiceAttempt) -> Option<String> {
    attempt.result.as_ref().and_then(|result| {
        matches!(result.choice.as_str(), UNKNOWN | NEEDS_EVIDENCE | NONE)
            .then(|| result.choice.clone())
    })
}

fn classifier_decision_class(attempt: &ChoiceAttempt) -> &str {
    if attempt.result.is_some() {
        "classifier"
    } else {
        "mechanical"
    }
}

fn unresolved_operation(
    task: &DecisionTask,
    attempt_id: &str,
    decision_class: &str,
    reason: &str,
    marker: Option<String>,
    uncertainties: Vec<String>,
) -> TypedOperation {
    TypedOperation::Unresolved {
        id: format!("decision:{}:unresolved", task.task_id),
        task_id: task.task_id.clone(),
        attempt_id: attempt_id.to_string(),
        connection_id: task.connection_id.clone(),
        decision_class: decision_class.to_string(),
        marker,
        reason: reason.to_string(),
        uncertainties,
    }
}

fn skipped_attempt(provider: &str, model: &str, reason: &str) -> ChoiceAttempt {
    ChoiceAttempt {
        result: None,
        receipt: ProviderReceipt {
            provider: provider.to_string(),
            requested_model: model.to_string(),
            observed_model: None,
            status: "skipped".to_string(),
            request_body: String::new(),
            response_body: None,
            error: Some(reason.to_string()),
            usage: None,
        },
    }
}

fn specialist_prompt(task: &DecisionTask) -> Result<String, MidendError> {
    if task.state.get("schema") == Some(&json!("satn-rust-rural-task/v1")) {
        let planning_brief = task
            .state
            .get("planning_brief")
            .and_then(Value::as_str)
            .unwrap_or(RURAL_PLANNING_BRIEF);
        let mut state = task.state.clone();
        if let Some(object) = state.as_object_mut() {
            object.remove("offer");
            object.remove("prior_decisions");
        }
        let frozen_task = DecisionTask {
            state,
            ..task.clone()
        };
        return Ok(format!(
            "You are a SATN planning specialist. Use only the frozen rural task below. Do not call tools, browse, retrieve sources, inspect files, or add facts. Apply the owner goals to minimise unnecessary added feeder network and prefer a comfortable complete child-to-accepted-terminal journey, where the terminal may be an accepted strategic spine or a useful qualified sourced urban entry. Treat inside-town onward paths as diagnostic evidence, not a mandatory city-centre objective. Use the supplied ordered elevation evidence and the two separately named moving-time scenarios when present; the hill-neutral sensitivity is not an e-bike ETA. Make a defensible qualitative provisional best judgment when the supplied evidence supports a comparative preference, and give a comparative reason plus material uncertainties. Missing numeric weights alone do not require abstention. Unknown facts or route coverage that are material to the choice, or no defensible preference, require unresolved; do not force a choice when the evidence is unsupported. Do not invent numeric weights, facts, safety, provision, access or adoption claims. Planning brief: {planning_brief} Return exactly one JSON object with {{\"proposal\":{{\"operation\":{{\"kind\":\"select-community-access\" or \"unresolved\",\"payload\":{{...}}}}}}}}. A select-community-access payload must copy an offered candidate_id from the options, set community_id when supplied, set provisional true, and include a concise decision reason plus a nonempty uncertainties list. An unresolved payload must include a concise reason and may include uncertainties. Do not include hidden chain of thought.\n\nFrozen rural planning task:\n{}\n",
            serde_json::to_string_pretty(&frozen_task)?
        ));
    }
    let planning_brief = task
        .state
        .get("planning_brief")
        .and_then(Value::as_str)
        .unwrap_or(PLANNING_BRIEF);
    Ok(format!(
        "You are a SATN planning specialist. Use only the frozen task below. Do not call tools, browse, retrieve sources, inspect files, or add facts. Planning brief: {planning_brief} Return exactly one JSON object with {{\"proposal\":{{\"operation\":{{\"kind\":\"select-alignment\" or \"unresolved\",\"payload\":{{...}}}}}}}}. A select-alignment payload must copy an offered candidate_id, set provisional true, and include a concise decision reason plus a nonempty uncertainties list. Do not include hidden chain of thought.\n\nFrozen planning task:\n{}\n",
        serde_json::to_string_pretty(task)?
    ))
}

fn emit(
    progress: &mut dyn FnMut(MidendProgress),
    config: &MidendConfig,
    started: Instant,
    task_id: Option<String>,
    provider: Option<String>,
    stage: &str,
    status: &str,
) {
    progress(MidendProgress {
        stage: stage.to_string(),
        branch: config.branch.clone(),
        task_id,
        provider,
        status: status.to_string(),
        elapsed_ms: started.elapsed().as_millis(),
    });
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::compiler::{RuralAccessCandidate, RuralDestinationEvidence};
    use crate::topography::{
        RouteCoverage, RouteTopographyProfile, SustainedGradient, TopographyPolicy,
    };
    use crate::travel_time::{TravelTimeEstimate, brouter_trekking_v1_7_10};

    fn profile(variation: f64, gradient: f64) -> RouteTopographyProfile {
        RouteTopographyProfile {
            availability: TopographyAvailability::Available,
            reason: "fixture".to_string(),
            evidence_file: "fixture.geojson".to_string(),
            policy: TopographyPolicy {
                evidence_tolerance_m: 5.0,
                maximum_sample_spacing_m: 250.0,
                minimum_sustained_spacing_m: 10.0,
            },
            evidence_refs: Vec::new(),
            source_refs: Vec::new(),
            coverage: RouteCoverage {
                route_length_m: 10.0,
                start_m: Some(0.0),
                end_m: Some(10.0),
                maximum_gap_m: Some(0.0),
                sample_count: 2,
            },
            forward_ascent_m: Some(variation),
            forward_descent_m: Some(0.0),
            reverse_ascent_m: Some(0.0),
            reverse_descent_m: Some(variation),
            cumulative_elevation_variation_m: Some(variation),
            sustained_gradient: Some(SustainedGradient {
                gradient_pct: gradient,
                absolute_gradient_pct: gradient,
                interval_length_m: 10.0,
                evidence_refs: Vec::new(),
            }),
            source_resolution_m: None,
            output_sample_spacing_m: None,
            vertical_accuracy_m: None,
            estimated_moving_time: TravelTimeEstimate::Unknown {
                reason: "fixture".to_string(),
                model: brouter_trekking_v1_7_10(),
            },
            moving_time_boundary_extrapolation: None,
            hill_neutral_moving_time: None,
        }
    }

    fn candidate(id: &str, new_link: f64, full_access: f64) -> RuralAccessCandidate {
        RuralAccessCandidate {
            id: id.to_string(),
            criterion: "fixture".to_string(),
            access: CommunityAccess {
                community_id: id.to_string(),
                source_id: id.to_string(),
                name: id.to_string(),
                geometry: [0.0, 0.0],
                status: "served".to_string(),
                decision_class: "mechanical".to_string(),
                is_primary: true,
                attachment_node: None,
                attachment_edge_id: None,
                attachment_point: None,
                attachment_fraction: None,
                attachment_distance_m: Some(0.0),
                parent_community_id: None,
                parent_community_name: None,
                parent_junction_node: None,
                parent_junction_edge_id: None,
                parent_junction_fraction: None,
                parent_junction_remaining_m: None,
                root_spine_id: Some("spine".to_string()),
                admission_order: None,
                attachment_depth: Some(0),
                new_link_length_m: Some(new_link),
                full_access_length_m: Some(full_access),
                joined_spine_id: Some("spine".to_string()),
                urban_entry: None,
                access_length_m: Some(new_link),
                path_edge_ids: vec![format!("edge:{id}")],
                path_start_fraction: Some(0.0),
                path_end_fraction: Some(1.0),
                path_geometry: vec![[0.0, 0.0], [1.0, 0.0]],
                onward_destinations: Vec::new(),
                onward_benefits: Vec::new(),
                joined_spine_reference: Some("Spine".to_string()),
                provision_status: "unknown".to_string(),
                reason: "fixture".to_string(),
                new_link_topography: Some(profile(new_link, new_link)),
                full_access_topography: Some(profile(new_link, new_link)),
            },
            destination_evidence: Vec::new(),
        }
    }

    #[test]
    fn rural_mechanical_dominance_ignores_missing_destination_diagnostics() {
        let mut left = candidate("left", 1.0, 2.0);
        left.destination_evidence = vec![RuralDestinationEvidence {
            destination_id: "town".to_string(),
            destination_name: "Town".to_string(),
            status: "available".to_string(),
            complete_route_length_m: Some(10.0),
            complete_route_topography: Some(profile(1.0, 1.0)),
            reason: None,
        }];
        let mut right = candidate("right", 2.0, 3.0);
        right.destination_evidence = vec![RuralDestinationEvidence {
            destination_id: "town".to_string(),
            destination_name: "Town".to_string(),
            status: "unavailable".to_string(),
            complete_route_length_m: None,
            complete_route_topography: None,
            reason: Some("diagnostic route unavailable".to_string()),
        }];
        let offer = RuralAccessOffer {
            community_id: "left".to_string(),
            community_name: "Left".to_string(),
            candidates: vec![left, right],
        };

        assert_eq!(
            mechanical_rural_candidate(&offer).map(|candidate| candidate.id.as_str()),
            Some("left")
        );
    }
}
