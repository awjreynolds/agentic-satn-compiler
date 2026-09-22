use std::collections::BTreeMap;
use std::fs;
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::path::{Path, PathBuf};

use satn_rs::judgment::{ChoiceResult, ProviderReceipt};
use satn_rs::midend::{
    ChoiceAttempt, ChoiceProvider, MidendConfig, MidendProgress, ProviderSet, SpecialistAttempt,
    SpecialistProvider, TypedOperation, fork, replay, run,
};
use satn_rs::{Candidate, CompileReport, Connection, Operation, SourceCorridor, UnknownFact};
use serde_json::{Value, json};

fn base() -> CompileReport {
    CompileReport {
        area_id: "fixture".to_string(),
        title: "Fixture".to_string(),
        snapshot_id: "snapshot-1".to_string(),
        source_inventory_count: 1,
        unknown_fact_count: 1,
        connection_count: 1,
        candidate_count: 2,
        operation_count: 2,
        boundary_scope: None,
        source_inventory: vec![SourceCorridor {
            id: "source:a1".to_string(),
            reference: "A1".to_string(),
            source_kind: "network".to_string(),
            source_id: "network".to_string(),
            scope: "pinned-network".to_string(),
            source_edge_ids: vec!["edge-a".to_string()],
            geometry: vec![vec![[0.0, 0.0], [1.0, 0.0]]],
            topology_status: "graph-bound".to_string(),
            attachment_status: "graph-edge".to_string(),
        }],
        unknown_facts: vec![UnknownFact {
            id: "unknown:a1".to_string(),
            subject: "source:a1".to_string(),
            status: "unknown".to_string(),
            reason: "source geometry does not establish provision".to_string(),
        }],
        connections: vec![Connection {
            id: "connection:alpha:beta".to_string(),
            origin_place_id: "alpha".to_string(),
            origin_name: "Alpha".to_string(),
            destination_place_id: "beta".to_string(),
            destination_name: "Beta".to_string(),
            origin_node: "n1".to_string(),
            destination_node: "n2".to_string(),
            cross_region_edge_ids: vec!["edge-a".to_string()],
            road_classes: vec!["a-road-reference".to_string()],
            preferred_classes: vec!["a-road-reference".to_string()],
        }],
        candidates: vec![
            candidate("candidate-a", 100.0),
            candidate("candidate-b", 120.0),
        ],
        operations: vec![
            Operation {
                id: "operation:candidate-a".to_string(),
                kind: "generate-candidate".to_string(),
                decision_class: "mechanical".to_string(),
                candidate_id: "candidate-a".to_string(),
                reason: "fixture".to_string(),
            },
            Operation {
                id: "operation:candidate-b".to_string(),
                kind: "generate-candidate".to_string(),
                decision_class: "mechanical".to_string(),
                candidate_id: "candidate-b".to_string(),
                reason: "fixture".to_string(),
            },
        ],
    }
}

fn candidate(id: &str, length_m: f64) -> Candidate {
    candidate_for(id, "connection:alpha:beta", length_m)
}

fn candidate_for(id: &str, connection_id: &str, length_m: f64) -> Candidate {
    Candidate {
        id: id.to_string(),
        connection_id: connection_id.to_string(),
        status: "mechanical-candidate".to_string(),
        decision_class: "mechanical".to_string(),
        role: "direct".to_string(),
        role_aliases: Vec::new(),
        length_m,
        search_cost_m: length_m,
        a_road_share: 1.0,
        ncn_share: 0.0,
        cycle_alignment_bases: Vec::new(),
        topology_status: "graph-supported".to_string(),
        provision_status: "unknown".to_string(),
        path_edge_ids: vec![format!("edge-{id}")],
        geometry: vec![[0.0, 0.0], [1.0, 0.0]],
    }
}

struct RecordingJev {
    prior_lengths: Vec<usize>,
}

impl ChoiceProvider for RecordingJev {
    fn classify_choice(&mut self, request: &satn_rs::judgment::ChoiceRequest) -> ChoiceAttempt {
        self.prior_lengths.push(
            request.state["prior_decisions"]
                .as_array()
                .expect("prior decisions array")
                .len(),
        );
        let choice = request
            .options
            .keys()
            .find(|option| !option.starts_with("__"))
            .expect("candidate option")
            .clone();
        ChoiceAttempt {
            result: Some(ChoiceResult {
                model: "jev-fixture".to_string(),
                choice,
                probabilities: request
                    .options
                    .keys()
                    .map(|option| (option.clone(), 0.0))
                    .collect(),
                confidence: 0.0,
            }),
            receipt: receipt("typesafe", "jev-fixture", "{\"choice\":\"fixture\"}"),
        }
    }
}

fn receipt(provider: &str, model: &str, response: &str) -> ProviderReceipt {
    ProviderReceipt {
        provider: provider.to_string(),
        requested_model: model.to_string(),
        observed_model: Some(model.to_string()),
        status: "answered".to_string(),
        request_body: "{\"frozen\":true}".to_string(),
        response_body: Some(response.to_string()),
        error: None,
        usage: None,
    }
}

struct FakeJev {
    choice: String,
    calls: usize,
}

impl ChoiceProvider for FakeJev {
    fn classify_choice(&mut self, _request: &satn_rs::judgment::ChoiceRequest) -> ChoiceAttempt {
        self.calls += 1;
        ChoiceAttempt {
            result: Some(ChoiceResult {
                model: "jev-fixture".to_string(),
                choice: self.choice.clone(),
                probabilities: BTreeMap::from([
                    ("__unknown__".to_string(), 0.2),
                    ("candidate-a".to_string(), 0.4),
                    ("candidate-b".to_string(), 0.4),
                ]),
                confidence: 0.4,
            }),
            receipt: receipt("typesafe", "jev-fixture", "{\"choice\":\"fixture\"}"),
        }
    }
}

struct FakeSpecialist {
    response: Value,
    calls: usize,
}

impl SpecialistProvider for FakeSpecialist {
    fn propose(&mut self, _prompt: &str) -> SpecialistAttempt {
        self.calls += 1;
        SpecialistAttempt {
            response: Some(self.response.clone()),
            receipt: receipt("codex-exec", "gpt-5.6-luna", &self.response.to_string()),
        }
    }
}

struct PanicJev;

impl ChoiceProvider for PanicJev {
    fn classify_choice(&mut self, _request: &satn_rs::judgment::ChoiceRequest) -> ChoiceAttempt {
        panic!("provider must not be called during replay or after an unfinished attempt")
    }
}

struct UnknownJev;

impl ChoiceProvider for UnknownJev {
    fn classify_choice(&mut self, _request: &satn_rs::judgment::ChoiceRequest) -> ChoiceAttempt {
        ChoiceAttempt {
            result: Some(ChoiceResult {
                model: "jev-fixture".to_string(),
                choice: "__unknown__".to_string(),
                probabilities: BTreeMap::from([
                    ("__unknown__".to_string(), 0.7),
                    ("candidate-a".to_string(), 0.2),
                    ("candidate-b".to_string(), 0.1),
                ]),
                confidence: 0.7,
            }),
            receipt: receipt("typesafe", "jev-fixture", "{\"choice\":\"__unknown__\"}"),
        }
    }
}

struct FailingJev;

impl ChoiceProvider for FailingJev {
    fn classify_choice(&mut self, _request: &satn_rs::judgment::ChoiceRequest) -> ChoiceAttempt {
        ChoiceAttempt {
            result: None,
            receipt: ProviderReceipt {
                provider: "typesafe".to_string(),
                requested_model: "jev-fixture".to_string(),
                observed_model: None,
                status: "failed".to_string(),
                request_body: "{\"frozen\":true}".to_string(),
                response_body: Some("provider failure".to_string()),
                error: Some("transport".to_string()),
                usage: None,
            },
        }
    }
}

struct InvalidCandidateJev;

impl ChoiceProvider for InvalidCandidateJev {
    fn classify_choice(&mut self, _request: &satn_rs::judgment::ChoiceRequest) -> ChoiceAttempt {
        ChoiceAttempt {
            result: Some(ChoiceResult {
                model: "jev-fixture".to_string(),
                choice: "not-admitted".to_string(),
                probabilities: BTreeMap::new(),
                confidence: 0.0,
            }),
            receipt: receipt("typesafe", "jev-fixture", "{\"choice\":\"not-admitted\"}"),
        }
    }
}

struct PanicAfterStart;

impl ChoiceProvider for PanicAfterStart {
    fn classify_choice(&mut self, _request: &satn_rs::judgment::ChoiceRequest) -> ChoiceAttempt {
        panic!("simulate interruption after persisted attempt start")
    }
}

fn progress() -> Vec<MidendProgress> {
    Vec::new()
}

fn root(label: &str) -> PathBuf {
    let root =
        std::env::temp_dir().join(format!("satn-rust-midend-{label}-{}", std::process::id()));
    if root.exists() {
        fs::remove_dir_all(&root).expect("clean fixture history");
    }
    root
}

fn run_fixture(
    root: &Path,
    base: CompileReport,
    config: MidendConfig,
    providers: ProviderSet<'_>,
) -> satn_rs::midend::MidendRun {
    let mut events = progress();
    run(root, base, config, providers, &mut |event| {
        events.push(event)
    })
    .expect("midend run")
}

#[test]
fn live_choice_is_typed_and_replay_does_not_dispatch() {
    let history = root("choice-replay");
    let mut jev = FakeJev {
        choice: "candidate-b".to_string(),
        calls: 0,
    };
    let first = run_fixture(
        &history,
        base(),
        MidendConfig::live(false),
        ProviderSet {
            classifier: Some(&mut jev),
            specialist: None,
        },
    );

    assert_eq!(jev.calls, 1);
    assert_eq!(first.operations.len(), 1);
    assert!(matches!(
        first.operations[0],
        TypedOperation::SelectAlignment { .. }
    ));
    assert_eq!(first.operations[0].candidate_id(), Some("candidate-b"));
    assert_eq!(first.operations[0].decision_class(), "classifier");

    let replayed = replay(&history, "main", &mut |_event| {}).expect("offline replay");
    assert_eq!(replayed.operations, first.operations);
}

#[test]
fn later_tasks_freeze_relevant_prior_decisions_after_each_operation() {
    let history = root("relevant-prior");
    let mut report = base();
    report.connections.extend([
        Connection {
            id: "connection:beta:gamma".to_string(),
            origin_place_id: "beta".to_string(),
            origin_name: "Beta".to_string(),
            destination_place_id: "gamma".to_string(),
            destination_name: "Gamma".to_string(),
            origin_node: "n2".to_string(),
            destination_node: "n3".to_string(),
            cross_region_edge_ids: vec!["edge-a".to_string()],
            road_classes: vec!["a-road-reference".to_string()],
            preferred_classes: vec!["a-road-reference".to_string()],
        },
        Connection {
            id: "connection:delta:epsilon".to_string(),
            origin_place_id: "delta".to_string(),
            origin_name: "Delta".to_string(),
            destination_place_id: "epsilon".to_string(),
            destination_name: "Epsilon".to_string(),
            origin_node: "n4".to_string(),
            destination_node: "n5".to_string(),
            cross_region_edge_ids: vec!["edge-z".to_string()],
            road_classes: vec!["a-road-reference".to_string()],
            preferred_classes: vec!["a-road-reference".to_string()],
        },
    ]);
    report
        .candidates
        .push(candidate_for("candidate-c", "connection:beta:gamma", 140.0));
    report.candidates.push(candidate_for(
        "candidate-d",
        "connection:delta:epsilon",
        160.0,
    ));
    report.connection_count = report.connections.len();
    report.candidate_count = report.candidates.len();

    let mut jev = RecordingJev {
        prior_lengths: Vec::new(),
    };
    let result = run_fixture(
        &history,
        report,
        MidendConfig::live(false),
        ProviderSet {
            classifier: Some(&mut jev),
            specialist: None,
        },
    );

    assert_eq!(result.operations.len(), 3);
    assert_eq!(jev.prior_lengths, vec![0, 1, 0]);
}

#[test]
fn resume_rejects_a_different_prepared_report_with_the_same_base_id() {
    let history = root("retained-base");
    let mut jev = FakeJev {
        choice: "candidate-a".to_string(),
        calls: 0,
    };
    run_fixture(
        &history,
        base(),
        MidendConfig::live(false),
        ProviderSet {
            classifier: Some(&mut jev),
            specialist: None,
        },
    );

    let mut changed = base();
    changed.title = "Different prepared report".to_string();
    let mut panic_provider = PanicJev;
    let error = run(
        &history,
        changed,
        MidendConfig::live(false),
        ProviderSet {
            classifier: Some(&mut panic_provider),
            specialist: None,
        },
        &mut |_event| {},
    )
    .expect_err("changed report must not replace retained base");
    assert!(error.to_string().contains("retained planning base"));
}

#[test]
fn unresolved_jev_escalates_to_provisional_specialist() {
    let history = root("specialist");
    let mut jev = UnknownJev;
    let response = json!({
        "proposal": {"operation": {"kind": "select-alignment", "payload": {
            "candidate_id": "candidate-a",
            "connection_id": "connection:alpha:beta",
            "provisional": true,
            "reason": "Admitted route remains the supported best guess.",
            "uncertainties": ["Current provision remains unknown."]
        }}}
    });
    let mut specialist = FakeSpecialist { response, calls: 0 };
    let result = run_fixture(
        &history,
        base(),
        MidendConfig::live(true),
        ProviderSet {
            classifier: Some(&mut jev),
            specialist: Some(&mut specialist),
        },
    );

    assert_eq!(specialist.calls, 1);
    assert_eq!(result.operations[0].candidate_id(), Some("candidate-a"));
    assert_eq!(result.operations[0].decision_class(), "agent");
    assert!(result.operations[0].is_provisional());
}

#[test]
fn failed_providers_are_retained_without_becoming_classifier_answers() {
    let history = root("failed-provider");
    let mut jev = FailingJev;
    let response = json!({"proposal": {"operation": {"kind": "select-alignment", "payload": {
        "candidate_id": "candidate-a",
        "connection_id": "connection:alpha:beta",
        "provisional": true,
        "reason": "best guess",
        "uncertainties": ["unknown"]
    }}}});
    let mut specialist = FakeSpecialist { response, calls: 0 };
    let result = run_fixture(
        &history,
        base(),
        MidendConfig::live(false),
        ProviderSet {
            classifier: Some(&mut jev),
            specialist: Some(&mut specialist),
        },
    );

    assert!(result.operations[0].is_unresolved());
    assert_eq!(result.operations[0].decision_class(), "code");
    assert_eq!(specialist.calls, 1);
    let history_text = fs::read_to_string(history.join("events.jsonl")).expect("history");
    assert!(history_text.contains("provider failure"));
    assert!(history_text.contains("codex-exec"));
}

#[test]
fn invalid_classifier_candidate_becomes_unresolved_without_being_applied() {
    let history = root("invalid-candidate");
    let mut jev = InvalidCandidateJev;
    let result = run_fixture(
        &history,
        base(),
        MidendConfig::live(false),
        ProviderSet {
            classifier: Some(&mut jev),
            specialist: None,
        },
    );

    assert!(result.operations[0].is_unresolved());
    assert_eq!(result.operations[0].decision_class(), "classifier");
    let history_text = fs::read_to_string(history.join("events.jsonl")).expect("history");
    assert!(history_text.contains("not-admitted"));
}

#[test]
fn an_started_attempt_is_not_silently_repeated() {
    let history = root("unfinished");
    let interrupted = catch_unwind(AssertUnwindSafe(|| {
        let mut jev = PanicAfterStart;
        let _ = run_fixture(
            &history,
            base(),
            MidendConfig::live(false),
            ProviderSet {
                classifier: Some(&mut jev),
                specialist: None,
            },
        );
    }));
    assert!(interrupted.is_err());

    let mut jev = PanicJev;
    let mut events = Vec::new();
    let error = run(
        &history,
        base(),
        MidendConfig::live(false),
        ProviderSet {
            classifier: Some(&mut jev),
            specialist: None,
        },
        &mut |event| events.push(event),
    )
    .expect_err("unfinished provider attempt needs attention");
    assert!(error.to_string().contains("unfinished"));
    assert!(events.iter().any(|event| event.stage == "checkpoint"));
}

#[test]
fn fork_starts_before_decision_and_accepts_replacement() {
    let history = root("fork");
    let mut jev = FakeJev {
        choice: "candidate-a".to_string(),
        calls: 0,
    };
    let first = run_fixture(
        &history,
        base(),
        MidendConfig::live(false),
        ProviderSet {
            classifier: Some(&mut jev),
            specialist: None,
        },
    );
    let task_id = first.task_ids[0].clone();
    fork(&history, "main", "alternative", &task_id).expect("fork before decision");

    let mut replacement = FakeJev {
        choice: "candidate-b".to_string(),
        calls: 0,
    };
    let branch = run_fixture(
        &history,
        base(),
        MidendConfig::live(false).with_branch("alternative"),
        ProviderSet {
            classifier: Some(&mut replacement),
            specialist: None,
        },
    );
    assert_eq!(branch.branch, "alternative");

    let replayed = replay(&history, "alternative", &mut |_event| {}).expect("branch replay");
    assert_eq!(replayed.operations[0].candidate_id(), Some("candidate-b"));
}
