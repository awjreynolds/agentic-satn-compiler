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

use crate::compiler::{AccessObligation, Candidate, CompileReport, Connection, SourceCorridor};
pub use crate::judgment::{ChoiceAttempt, SpecialistAttempt};
use crate::judgment::{ChoiceRequest, ChoiceResult, CodexConfig, ProviderReceipt, TypeSafeConfig};

const UNKNOWN: &str = "__unknown__";
const NEEDS_EVIDENCE: &str = "__needs_evidence__";
const NONE: &str = "__none__";

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
        ChoiceRequest::new(
            self.state.clone(),
            self.question_id.clone(),
            json!("Choose one admitted planning option or an explicit unresolved outcome."),
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
}

impl TypedOperation {
    pub fn candidate_id(&self) -> Option<&str> {
        match self {
            Self::SelectAlignment { candidate_id, .. } => Some(candidate_id),
            Self::Unresolved { .. } => None,
        }
    }

    pub fn decision_class(&self) -> &str {
        match self {
            Self::SelectAlignment { decision_class, .. }
            | Self::Unresolved { decision_class, .. } => decision_class,
        }
    }

    pub fn is_provisional(&self) -> bool {
        matches!(
            self,
            Self::SelectAlignment {
                provisional: true,
                ..
            }
        )
    }

    pub fn is_unresolved(&self) -> bool {
        matches!(self, Self::Unresolved { .. })
    }

    fn connection_id(&self) -> &str {
        match self {
            Self::SelectAlignment { connection_id, .. }
            | Self::Unresolved { connection_id, .. } => connection_id,
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
            if serde_json::to_value(&existing.report)? != serde_json::to_value(&base.report)? {
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
        | TypedOperation::Unresolved { task_id, .. } => task_id,
    }
}

fn operation_attempt_id(operation: &TypedOperation) -> &str {
    match operation {
        TypedOperation::SelectAlignment { attempt_id, .. }
        | TypedOperation::Unresolved { attempt_id, .. } => attempt_id,
    }
}

pub fn run(
    root: &Path,
    report: CompileReport,
    config: MidendConfig,
    mut providers: ProviderSet<'_>,
    progress: &mut dyn FnMut(MidendProgress),
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
        validate_operation(&base.report, operation, config.allow_provisional)?;
    }
    let mut completed_connections: BTreeSet<String> = operations
        .iter()
        .map(|operation| operation.connection_id().to_string())
        .collect();
    let mut task_ids = Vec::new();
    let mut connections = base.report.connections.clone();
    connections.sort_by(|left, right| left.id.cmp(&right.id));

    for connection in connections {
        let task = make_task(&base, &connection, &operations, config.allow_provisional);
        task_ids.push(task.task_id.clone());
        if completed_connections.contains(&task.connection_id) {
            continue;
        }
        store.append_task(&config.branch, task.clone())?;
        let attempt_id = format!("attempt:{}:jev", task.task_id);
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
        let classifier_attempt = match providers.classifier.as_deref_mut() {
            Some(provider) if config.mode == "live" => {
                provider.classify_choice(&task.choice_request())
            }
            Some(_) => skipped_attempt("typesafe", &config.jev_model, "deterministic mode"),
            None => skipped_attempt("typesafe", &config.jev_model, "classifier is unconfigured"),
        };
        let classifier_record = AttemptRecord {
            id: attempt_id.clone(),
            task_id: task.task_id.clone(),
            actor: "classifier".to_string(),
            status: classifier_attempt.receipt.status.clone(),
            choice: classifier_attempt.result.clone(),
            proposal: None,
            receipt: classifier_attempt.receipt.clone(),
        };
        store.append_attempt_result(&config.branch, classifier_record)?;
        emit(
            progress,
            &config,
            started,
            Some(task.task_id.clone()),
            Some(classifier_attempt.receipt.provider.clone()),
            "provider",
            &classifier_attempt.receipt.status,
        );

        let mut operation =
            classifier_operation(&base.report, &task, &attempt_id, &classifier_attempt);
        if operation.is_none()
            && should_escalate(&classifier_attempt)
            && config.mode == "live"
            && providers.specialist.is_some()
        {
            let specialist_attempt_id = format!("attempt:{}:specialist", task.task_id);
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
            let specialist_attempt = providers
                .specialist
                .as_deref_mut()
                .expect("specialist presence checked above")
                .propose(&specialist_prompt(&task)?);
            let specialist_record = AttemptRecord {
                id: specialist_attempt_id.clone(),
                task_id: task.task_id.clone(),
                actor: "agent".to_string(),
                status: specialist_attempt.receipt.status.clone(),
                choice: None,
                proposal: specialist_attempt.response.clone(),
                receipt: specialist_attempt.receipt.clone(),
            };
            store.append_attempt_result(&config.branch, specialist_record)?;
            emit(
                progress,
                &config,
                started,
                Some(task.task_id.clone()),
                Some(specialist_attempt.receipt.provider.clone()),
                "provider",
                &specialist_attempt.receipt.status,
            );
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
                    "code",
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
        validate_operation(&base.report, &operation, config.allow_provisional)?;
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
    })
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
        validate_operation(&base.report, operation, true)?;
    }
    let task_ids = store
        .chain(branch)?
        .into_iter()
        .filter(|event| event.kind == "task")
        .filter_map(|event| event.task_id)
        .collect::<Vec<_>>();
    let config = MidendConfig::deterministic(false).with_branch(branch.to_string());
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
    })
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
        "graph_edge_ids": source.graph_edge_ids,
        "topology_status": source.topology_status,
        "attachment_status": source.attachment_status,
        "provision_status": source.provision_status,
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
    if prior_connection.id == connection.id
        || prior_connection.origin_place_id == connection.origin_place_id
        || prior_connection.destination_place_id == connection.destination_place_id
        || prior_connection.origin_node == connection.origin_node
        || prior_connection.destination_node == connection.destination_node
    {
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
        "path_edge_ids": candidate.path_edge_ids,
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
            let reason = payload.get("reason")?.as_str()?.trim();
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
            let reason = payload.get("reason")?.as_str()?.trim();
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

fn validate_operation(
    report: &CompileReport,
    operation: &TypedOperation,
    allow_provisional: bool,
) -> Result<(), MidendError> {
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
            if decision_class != "classifier" && decision_class != "agent" {
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
            if !matches!(decision_class.as_str(), "classifier" | "agent" | "code") {
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
        "code"
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
    Ok(format!(
        "You are a SATN planning specialist. Use only the frozen task below. Do not call tools, browse, retrieve sources, inspect files, or add facts. Return exactly one JSON object with {{\"proposal\":{{\"operation\":{{\"kind\":\"select-alignment\" or \"unresolved\",\"payload\":{{...}}}}}}}}. A select-alignment payload must copy an offered candidate_id, set provisional true, and include a concise reason plus a nonempty uncertainties list. Do not include hidden chain of thought.\n\nFrozen planning task:\n{}\n",
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
