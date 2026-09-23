use std::collections::BTreeMap;
use std::fs;
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::path::{Path, PathBuf};

use satn_rs::judgment::{ChoiceResult, ProviderReceipt};
use satn_rs::midend::{
    ChoiceAttempt, ChoiceProvider, MidendConfig, MidendProgress, ProviderSet, SpecialistAttempt,
    SpecialistProvider, TypedOperation, fork, replay, run, run_prepared,
};
use satn_rs::{
    AccessObligation, AccountingSummary, Candidate, CompileOptions, CompileReport, Connection,
    NetworkPlace, Operation, SourceCorridor, UnknownFact, prepare_with_progress,
};
use serde_json::{Value, json};

fn base() -> CompileReport {
    CompileReport {
        area_id: "fixture".to_string(),
        deployment_id: "fixture".to_string(),
        attribution: String::new(),
        source_attributions: Vec::new(),
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
            baseline_role: "a-road".to_string(),
            source_edge_ids: vec!["edge-a".to_string()],
            graph_edge_ids: vec![
                "edge-candidate-a".to_string(),
                "edge-candidate-b".to_string(),
            ],
            geometry: vec![vec![[0.0, 0.0], [1.0, 0.0]]],
            topology_status: "graph-bound".to_string(),
            attachment_status: "graph-edge".to_string(),
            provision_status: "unknown".to_string(),
        }],
        unknown_facts: vec![UnknownFact {
            id: "unknown:a1".to_string(),
            subject: "source:a1".to_string(),
            status: "unknown".to_string(),
            reason: "source geometry does not establish provision".to_string(),
        }],
        network_places: vec![
            NetworkPlace {
                id: "alpha".to_string(),
                name: "Alpha".to_string(),
                source_id: "alpha".to_string(),
                place_class: "town".to_string(),
                geometry: [0.0, 0.0],
            },
            NetworkPlace {
                id: "beta".to_string(),
                name: "Beta".to_string(),
                source_id: "beta".to_string(),
                place_class: "town".to_string(),
                geometry: [1.0, 0.0],
            },
        ],
        school_context: Vec::new(),
        community_access: Vec::new(),
        access_obligations: vec![access_obligation("alpha"), access_obligation("beta")],
        destination_profile: "unconfigured".to_string(),
        accounting: AccountingSummary {
            status: "reviewable-with-gaps".to_string(),
            complete: false,
            source_baseline_count: 1,
            network_place_count: 2,
            obligation_count: 2,
            unresolved_count: 3,
            network_gap_count: 2,
        },
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
        candidate_neighbourhoods: Vec::new(),
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

fn access_obligation(place_id: &str) -> AccessObligation {
    AccessObligation {
        id: format!("obligation:community:{place_id}"),
        kind: "community".to_string(),
        source_id: place_id.to_string(),
        name: place_id.to_string(),
        geometry: None,
        access_point_status: None,
        access_point_source_id: None,
        access_point_rationale: None,
        disposition: "unresolved".to_string(),
        reason: "No selected access support is present.".to_string(),
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
        path_edge_geometries: Vec::new(),
        geometry: vec![[0.0, 0.0], [1.0, 0.0]],
    }
}

struct RecordingJev {
    prior_lengths: Vec<usize>,
    source_roles: Vec<Vec<String>>,
    access_unknown_lengths: Vec<usize>,
}

impl ChoiceProvider for RecordingJev {
    fn classify_choice(&mut self, request: &satn_rs::judgment::ChoiceRequest) -> ChoiceAttempt {
        assert!(
            request.instructions.as_str().is_some_and(|instructions| {
                instructions.contains("strategic active-travel alignment")
            }),
            "classifier receives the strategic planning brief"
        );
        assert_eq!(request.state["planning_brief"], request.instructions);
        assert!(
            request.state["candidates"]
                .as_object()
                .expect("candidate summaries")
                .values()
                .all(|candidate| candidate.get("path_edge_ids").is_none()),
            "classifier packet omits opaque candidate edge IDs"
        );
        assert!(
            request.state["relevant_sources"]
                .as_array()
                .expect("source summaries")
                .iter()
                .all(|source| source.get("graph_edge_ids").is_none()),
            "classifier packet omits opaque source edge IDs"
        );
        self.prior_lengths.push(
            request.state["prior_decisions"]
                .as_array()
                .expect("prior decisions array")
                .len(),
        );
        self.source_roles.push(
            request.state["relevant_sources"]
                .as_array()
                .expect("relevant source array")
                .iter()
                .filter_map(|source| source["baseline_role"].as_str().map(str::to_string))
                .collect(),
        );
        self.access_unknown_lengths.push(
            request.state["access_unknowns"]
                .as_array()
                .expect("access unknown array")
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
        source_roles: Vec::new(),
        access_unknown_lengths: Vec::new(),
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
    assert_eq!(jev.source_roles[0], vec!["a-road"]);
    assert_eq!(jev.access_unknown_lengths, vec![2, 1, 0]);
}

#[test]
fn opposite_direction_shared_endpoint_is_relevant_without_shared_corridor_edge() {
    let history = root("cross-orientation");
    let mut report = base();
    let mut second = report.connections[0].clone();
    second.id = "connection:beta:gamma".to_string();
    second.origin_place_id = "beta".to_string();
    second.origin_name = "Beta".to_string();
    second.destination_place_id = "gamma".to_string();
    second.destination_name = "Gamma".to_string();
    second.origin_node = "n2".to_string();
    second.destination_node = "n3".to_string();
    second.cross_region_edge_ids = vec!["edge-c".to_string()];
    report.connections.push(second);
    report
        .candidates
        .push(candidate_for("candidate-c", "connection:beta:gamma", 140.0));
    report.connection_count = report.connections.len();
    report.candidate_count = report.candidates.len();

    let mut jev = RecordingJev {
        prior_lengths: Vec::new(),
        source_roles: Vec::new(),
        access_unknown_lengths: Vec::new(),
    };
    run_fixture(
        &history,
        report,
        MidendConfig::live(false),
        ProviderSet {
            classifier: Some(&mut jev),
            specialist: None,
        },
    );

    assert_eq!(jev.prior_lengths, vec![0, 1]);
}

#[test]
fn resume_uses_recorded_classifier_result_without_redispatch() {
    let history = root("recorded-result-resume");
    let mut first_jev = FakeJev {
        choice: "candidate-a".to_string(),
        calls: 0,
    };
    run_fixture(
        &history,
        base(),
        MidendConfig::live(false),
        ProviderSet {
            classifier: Some(&mut first_jev),
            specialist: None,
        },
    );
    assert_eq!(first_jev.calls, 1);

    let events_path = history.join("events.jsonl");
    let mut events = fs::read_to_string(&events_path)
        .expect("events")
        .lines()
        .map(|line| serde_json::from_str::<Value>(line).expect("event JSON"))
        .collect::<Vec<_>>();
    let decision = events.pop().expect("decision event");
    assert_eq!(decision["kind"], "decision");
    let retained_head = events.last().expect("retained event")["id"]
        .as_str()
        .expect("retained event id")
        .to_string();
    fs::write(
        &events_path,
        events
            .iter()
            .map(Value::to_string)
            .collect::<Vec<_>>()
            .join("\n")
            + "\n",
    )
    .expect("truncate decision");
    fs::write(
        history.join("branches.json"),
        json!({"main": {"head": retained_head}}).to_string(),
    )
    .expect("rewind branch");

    let mut resumed_jev = FakeJev {
        choice: "candidate-b".to_string(),
        calls: 0,
    };
    let resumed = run_fixture(
        &history,
        base(),
        MidendConfig::live(false),
        ProviderSet {
            classifier: Some(&mut resumed_jev),
            specialist: None,
        },
    );
    assert_eq!(resumed_jev.calls, 0);
    assert_eq!(resumed.operations[0].candidate_id(), Some("candidate-a"));
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
fn resume_accepts_the_report_float_that_was_persisted_as_json() {
    let history = root("retained-float-roundtrip");
    let mut report = base();
    report.candidates[0].a_road_share = 0.009418363318192279_f64;
    let mut jev = FakeJev {
        choice: "candidate-a".to_string(),
        calls: 0,
    };
    run_fixture(
        &history,
        report.clone(),
        MidendConfig::live(false),
        ProviderSet {
            classifier: Some(&mut jev),
            specialist: None,
        },
    );

    let mut panic_provider = PanicJev;
    run(
        &history,
        report,
        MidendConfig::deterministic(false),
        ProviderSet {
            classifier: Some(&mut panic_provider),
            specialist: None,
        },
        &mut |_event| {},
    )
    .expect("persisted report should compare equal after JSON round-trip");
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
    assert_eq!(result.operations[0].decision_class(), "mechanical");
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

struct RuralChoiceJev {
    calls: usize,
    choices: Vec<String>,
    choose_comfort: bool,
}

impl ChoiceProvider for RuralChoiceJev {
    fn classify_choice(&mut self, request: &satn_rs::judgment::ChoiceRequest) -> ChoiceAttempt {
        assert_eq!(request.state["schema"], "satn-rust-rural-task/v1");
        assert!(request.state.get("offer").is_none());
        assert!(request.state.get("prior_decisions").is_none());
        assert!(
            request
                .instructions
                .as_str()
                .is_some_and(|brief| brief.contains("rural community access"))
        );
        let brief = request.instructions.as_str().expect("rural policy brief");
        assert!(brief.contains("minimise unnecessary added feeder network"));
        assert!(brief.contains("comfortable complete child-to-accepted-terminal journey"));
        assert!(brief.contains("qualified sourced urban entry"));
        assert!(
            brief.contains("diagnostic evidence rather than a mandatory city-centre objective")
        );
        assert!(brief.contains("hill-neutral sensitivity"));
        assert!(brief.contains("missing numeric weights alone do not require abstention"));
        assert!(brief.contains("unsupported"));
        assert!(
            request
                .options
                .iter()
                .filter(|(id, _)| !id.starts_with("__"))
                .all(|(_, option)| option.get("destination_evidence").is_some())
        );
        for marker in ["__unknown__", "__needs_evidence__", "__none__"] {
            assert!(request.options.contains_key(marker));
        }
        self.calls += 1;
        let choice = request
            .options
            .keys()
            .find(|option| self.choose_comfort && option.ends_with(":comfort"))
            .or_else(|| {
                (!self.choose_comfort)
                    .then(|| {
                        request
                            .options
                            .keys()
                            .find(|option| option.ends_with(":shortest"))
                    })
                    .flatten()
            })
            .or_else(|| {
                request
                    .options
                    .keys()
                    .find(|option| !option.starts_with("__"))
            })
            .expect("rural candidate option")
            .clone();
        self.choices.push(choice.clone());
        ChoiceAttempt {
            result: Some(ChoiceResult {
                model: "jev-rural-fixture".to_string(),
                choice,
                probabilities: BTreeMap::new(),
                confidence: 0.0,
            }),
            receipt: receipt("typesafe", "jev-rural-fixture", "{\"choice\":\"rural\"}"),
        }
    }
}

struct RuralUnknownJev {
    calls: usize,
}

impl ChoiceProvider for RuralUnknownJev {
    fn classify_choice(&mut self, request: &satn_rs::judgment::ChoiceRequest) -> ChoiceAttempt {
        self.calls += 1;
        assert!(request.options.contains_key("__unknown__"));
        ChoiceAttempt {
            result: Some(ChoiceResult {
                model: "jev-rural-fixture".to_string(),
                choice: "__unknown__".to_string(),
                probabilities: BTreeMap::new(),
                confidence: 0.0,
            }),
            receipt: receipt(
                "typesafe",
                "jev-rural-fixture",
                "{\"choice\":\"__unknown__\"}",
            ),
        }
    }
}

struct TownwardChoiceJev {
    calls: usize,
    choices: Vec<String>,
}

impl ChoiceProvider for TownwardChoiceJev {
    fn classify_choice(&mut self, request: &satn_rs::judgment::ChoiceRequest) -> ChoiceAttempt {
        self.calls += 1;
        assert_eq!(request.state["schema"], "satn-rust-rural-task/v1");
        let townward = request
            .options
            .iter()
            .find(|(id, option)| {
                id.contains(":townward:town")
                    && option["destination_evidence"]
                        .as_array()
                        .is_some_and(|evidence| {
                            evidence.iter().any(|item| {
                                item["destination_name"] == "Town"
                                    && item["status"] == "available"
                                    && item["complete_route_length_m"].is_number()
                            })
                        })
            })
            .map(|(id, _)| id.clone());
        let choice = townward
            .or_else(|| {
                request
                    .options
                    .keys()
                    .find(|id| id.ends_with(":shortest"))
                    .cloned()
            })
            .expect("townward or shortest rural candidate");
        self.choices.push(choice.clone());
        ChoiceAttempt {
            result: Some(ChoiceResult {
                model: "jev-townward-fixture".to_string(),
                choice,
                probabilities: BTreeMap::new(),
                confidence: 0.0,
            }),
            receipt: receipt(
                "typesafe",
                "jev-townward-fixture",
                "{\"choice\":\"townward\"}",
            ),
        }
    }
}

struct CapturingRuralJev {
    packet: Option<Value>,
}

impl ChoiceProvider for CapturingRuralJev {
    fn classify_choice(&mut self, request: &satn_rs::judgment::ChoiceRequest) -> ChoiceAttempt {
        self.packet = Some(json!({
            "state": request.state.clone(),
            "question_id": request.question_id.clone(),
            "instructions": request.instructions.clone(),
            "options": request.options.clone(),
        }));
        let choice = request
            .options
            .keys()
            .find(|option| !option.starts_with("__"))
            .expect("rural candidate option")
            .clone();
        ChoiceAttempt {
            result: Some(ChoiceResult {
                model: "jev-packet-fixture".to_string(),
                choice,
                probabilities: BTreeMap::new(),
                confidence: 0.0,
            }),
            receipt: receipt("typesafe", "jev-packet-fixture", "{\"choice\":\"rural\"}"),
        }
    }
}

struct RuralSpecialist {
    response: Value,
    calls: usize,
}

impl SpecialistProvider for RuralSpecialist {
    fn propose(&mut self, prompt: &str) -> SpecialistAttempt {
        self.calls += 1;
        assert!(prompt.contains("select-community-access"));
        assert!(!prompt.contains("\"offer\""));
        assert!(!prompt.contains("\"prior_decisions\""));
        assert!(prompt.contains("minimise unnecessary added feeder network"));
        assert!(prompt.contains("missing numeric weights alone do not require abstention"));
        assert!(prompt.contains("material uncertainties"));
        SpecialistAttempt {
            response: Some(self.response.clone()),
            receipt: receipt("codex-exec", "gpt-5.6-luna", &self.response.to_string()),
        }
    }
}

#[test]
fn prepared_rural_choice_binds_the_next_offer_to_the_selected_parent_path() {
    let root = rural_prepared_fixture("selected-parent");
    let prepared = prepare_with_progress(
        &root.join("area.yaml"),
        CompileOptions::default(),
        &mut |_event| {},
    )
    .expect("prepared rural fixture");
    let mut probe = prepared.rural_planner();
    let offer = probe
        .offer_next()
        .expect("rural offer")
        .expect("parent offer");
    assert_eq!(
        offer
            .candidates
            .iter()
            .map(|candidate| candidate.id.as_str())
            .collect::<Vec<_>>(),
        vec!["rural:parent:shortest", "rural:parent:comfort"]
    );
    drop(probe);
    let history = root.join("history");
    let mut jev = RuralChoiceJev {
        calls: 0,
        choices: Vec::new(),
        choose_comfort: true,
    };
    let result = run_prepared(
        &history,
        &prepared,
        MidendConfig::live(false),
        ProviderSet {
            classifier: Some(&mut jev),
            specialist: None,
        },
        &mut |_event| {},
    )
    .expect("prepared rural run");

    assert_eq!(
        jev.calls, 1,
        "only the tradeoff parent needs the classifier"
    );
    assert_eq!(jev.choices, vec!["rural:parent:comfort"]);
    assert_eq!(result.operations.len(), 2);
    let parent = result
        .community_access
        .iter()
        .find(|access| access.community_id == "parent")
        .expect("accepted parent");
    let child = result
        .community_access
        .iter()
        .find(|access| access.community_id == "child")
        .expect("accepted child");
    assert_eq!(child.parent_community_id.as_deref(), Some("parent"));
    assert_eq!(child.path_edge_ids, Vec::<String>::new());
    assert_eq!(
        result
            .operations
            .iter()
            .find_map(|operation| match operation {
                TypedOperation::SelectCommunityAccess {
                    community_id,
                    candidate_id,
                    ..
                } if community_id == "parent" => Some(candidate_id.as_str()),
                _ => None,
            }),
        Some("rural:parent:comfort")
    );
    assert!(
        parent
            .path_edge_ids
            .iter()
            .any(|edge_id| edge_id.contains(":parent:flat:")),
        "selected parent path must be the alternate flat branch"
    );
    assert_eq!(
        result
            .operations
            .iter()
            .filter(|operation| operation.is_rural())
            .count(),
        2
    );
}

#[test]
fn prepared_rural_choice_exposes_townward_evidence_and_replays_without_provider() {
    let root = rural_destination_fixture("townward-runtime");
    let prepared = prepare_with_progress(
        &root.join("area.yaml"),
        CompileOptions::default(),
        &mut |_event| {},
    )
    .expect("prepared townward fixture");
    let history = root.join("history");
    let mut jev = TownwardChoiceJev {
        calls: 0,
        choices: Vec::new(),
    };
    let first = run_prepared(
        &history,
        &prepared,
        MidendConfig::live(false),
        ProviderSet {
            classifier: Some(&mut jev),
            specialist: None,
        },
        &mut |_event| {},
    )
    .expect("townward rural run");
    assert!(jev.calls > 0);
    assert!(jev.choices[0].contains(":townward:town"));
    let parent = first
        .community_access
        .iter()
        .find(|access| access.community_id == "parent" && access.is_primary)
        .expect("townward parent access");
    assert_eq!(
        parent.root_spine_id.as_deref(),
        Some("source:network:a-road:A2")
    );

    let replayed = replay(&history, "main", &mut |_event| {}).expect("townward replay");
    assert_eq!(replayed.operations, first.operations);
    assert_eq!(replayed.community_access, first.community_access);
}

#[test]
fn rural_provider_packet_is_compact_but_retains_full_offer_for_replay() {
    let root = rural_destination_fixture("compact-provider-packet");
    let prepared = prepare_with_progress(
        &root.join("area.yaml"),
        CompileOptions::default(),
        &mut |_event| {},
    )
    .expect("prepared compact-packet fixture");
    let history = root.join("history");
    let mut jev = CapturingRuralJev { packet: None };
    run_prepared(
        &history,
        &prepared,
        MidendConfig::live(false),
        ProviderSet {
            classifier: Some(&mut jev),
            specialist: None,
        },
        &mut |_event| {},
    )
    .expect("compact-packet rural run");

    let packet = jev.packet.expect("captured provider packet");
    let packet_bytes = serde_json::to_vec(&packet).expect("provider packet bytes");
    eprintln!("rural provider packet bytes: {}", packet_bytes.len());
    let options = packet["options"].as_object().expect("provider options");
    let candidate = options
        .iter()
        .find(|(id, _)| !id.starts_with("__"))
        .map(|(_, option)| option)
        .expect("candidate option");
    let evidence = candidate["destination_evidence"]
        .as_array()
        .expect("destination evidence summary");
    assert!(!evidence.is_empty());
    assert!(evidence.iter().all(|item| {
        let topography = &item["topography"];
        item["destination_name"].is_string()
            && item["status"].is_string()
            && item["complete_route_length_m"].is_number()
            && topography.is_null()
            && item["reason"]
                .as_str()
                .is_some_and(|reason| reason.contains("offline complete-journey comparison"))
    }));

    let retained_task = fs::read_to_string(history.join("events.jsonl"))
        .expect("retained events")
        .lines()
        .filter_map(|line| serde_json::from_str::<Value>(line).ok())
        .find(|event| {
            event["kind"] == "task"
                && event["task_id"]
                    .as_str()
                    .is_some_and(|task_id| task_id.starts_with("task:rural:"))
        })
        .expect("retained rural task");
    let retained_candidate = retained_task["task"]["state"]["offer"]["candidates"]
        .as_array()
        .expect("retained offer candidates")
        .iter()
        .find(|candidate| candidate["access"]["full_access_topography"].is_object())
        .expect("full retained access profile");
    assert!(
        retained_candidate["access"]["full_access_topography"]["estimated_moving_time"].is_object()
    );
    let retained_evidence = retained_candidate["destination_evidence"]
        .as_array()
        .expect("retained destination evidence")
        .iter()
        .find(|item| item["status"] == "available")
        .expect("retained available destination evidence");
    assert!(retained_evidence["complete_route_topography"].is_null());
}

#[test]
fn sourced_urban_entry_choice_replays_without_provider() {
    let root = urban_entry_fixture("urban-replay");
    let prepared = prepare_with_progress(
        &root.join("area.yaml"),
        CompileOptions::default(),
        &mut |_event| {},
    )
    .expect("prepared urban-entry fixture");
    let history = root.join("history");
    let first = run_prepared(
        &history,
        &prepared,
        MidendConfig::deterministic(false),
        ProviderSet {
            classifier: None,
            specialist: None,
        },
        &mut |_event| {},
    )
    .expect("urban-entry run");
    assert!(first.operations.iter().any(TypedOperation::is_rural));
    let community = first
        .community_access
        .iter()
        .find(|access| access.community_id == "community" && access.is_primary)
        .expect("urban-entry community");
    assert_eq!(community.status, "urban-entry");
    assert_eq!(community.root_spine_id, None);
    assert_eq!(community.joined_spine_id, None);
    assert_eq!(
        community
            .urban_entry
            .as_ref()
            .map(|entry| entry.extent_source_id.as_str()),
        Some("5342409")
    );
    let child = first
        .community_access
        .iter()
        .find(|access| access.community_id == "child" && access.is_primary)
        .expect("child urban-entry inheritance");
    assert_eq!(child.parent_community_id.as_deref(), Some("community"));
    assert_eq!(child.urban_entry, community.urban_entry);

    let replayed = replay(&history, "main", &mut |_event| {}).expect("urban-entry replay");
    assert_eq!(replayed.operations, first.operations);
    assert_eq!(replayed.community_access, first.community_access);
}

#[test]
fn saved_specialist_result_precedes_a_new_mechanical_shortcut() {
    let root = urban_entry_fixture("saved-specialist-priority");
    let prepared = prepare_with_progress(
        &root.join("area.yaml"),
        CompileOptions::default(),
        &mut |_event| {},
    )
    .expect("prepared urban-entry fixture");
    let history = root.join("history");

    // Seed the retained task and offer without retaining its deterministic
    // decision. The synthetic provider records below model a completed Jev
    // abstention followed by a completed specialist answer at that checkpoint.
    run_prepared(
        &history,
        &prepared,
        MidendConfig::deterministic(false),
        ProviderSet {
            classifier: None,
            specialist: None,
        },
        &mut |_event| {},
    )
    .expect("seed deterministic rural task");
    let events_path = history.join("events.jsonl");
    let mut events = fs::read_to_string(&events_path)
        .expect("seed events")
        .lines()
        .map(|line| serde_json::from_str::<Value>(line).expect("event JSON"))
        .collect::<Vec<_>>();
    let task_event = events
        .iter()
        .find(|event| event["kind"] == "task" && event["task_id"].is_string())
        .cloned()
        .expect("retained rural task");
    let task_id = task_event["task_id"].as_str().expect("task id");
    let task = &task_event["task"];
    let community_id = task["state"]["community"]["id"]
        .as_str()
        .expect("community id");
    let candidate_id = task["state"]["offer"]["candidates"][0]["id"]
        .as_str()
        .expect("candidate id")
        .to_string();
    let task_event_id = task_event["id"].as_str().expect("task event id");
    let started_id = "event:main:saved-specialist-started";
    let result_id = "event:main:saved-jev-result";
    let jev_attempt_id = format!("attempt:{task_id}:jev");
    let specialist_attempt_id = format!("attempt:{task_id}:specialist");
    let jev_receipt = json!({
        "provider": "typesafe",
        "requested_model": "jev-fixture",
        "observed_model": "jev-fixture",
        "status": "answered",
        "request_body": "{\"saved\":true}",
        "response_body": "{\"choice\":\"__unknown__\"}",
        "error": null,
        "usage": null
    });
    let specialist_response = json!({
        "proposal": {"operation": {"kind": "select-community-access", "payload": {
            "candidate_id": candidate_id,
            "community_id": community_id,
            "provisional": true,
            "decision_reason": "Retained specialist evidence remains the selected answer.",
            "uncertainties": ["Provision remains unknown."]
        }}}
    });
    let specialist_receipt = json!({
        "provider": "codex-exec",
        "requested_model": "gpt-5.6-luna",
        "observed_model": "gpt-5.6-luna",
        "status": "answered",
        "request_body": "saved specialist prompt",
        "response_body": specialist_response.to_string(),
        "error": null,
        "usage": null
    });

    // Rewind the branch to the task event, leaving later seed events in the
    // append-only file but outside the branch chain.
    let mut branches = json!({"main": {"head": task_event_id}});
    fs::write(
        &events_path,
        events
            .iter()
            .map(Value::to_string)
            .collect::<Vec<_>>()
            .join("\n")
            + "\n",
    )
    .expect("rewind seed decision");
    events.push(json!({
        "id": started_id,
        "branch": "main",
        "parent": task_event_id,
        "kind": "attempt-started",
        "task_id": task_id,
        "task": null,
        "attempt_id": jev_attempt_id,
        "attempt": {
            "id": jev_attempt_id,
            "task_id": task_id,
            "actor": "classifier",
            "status": "started",
            "choice": null,
            "proposal": null,
            "receipt": {
                "provider": "typesafe",
                "requested_model": "jev-fixture",
                "observed_model": null,
                "status": "started",
                "request_body": "",
                "response_body": null,
                "error": null,
                "usage": null
            }
        },
        "operation": null
    }));
    events.push(json!({
        "id": result_id,
        "branch": "main",
        "parent": started_id,
        "kind": "attempt-result",
        "task_id": task_id,
        "task": null,
        "attempt_id": jev_attempt_id,
        "attempt": {
            "id": jev_attempt_id,
            "task_id": task_id,
            "actor": "classifier",
            "status": "answered",
            "choice": {
                "model": "jev-fixture",
                "choice": "__unknown__",
                "probabilities": {"__unknown__": 1.0},
                "confidence": 0.0
            },
            "proposal": null,
            "receipt": jev_receipt
        },
        "operation": null
    }));
    let specialist_started_id = "event:main:saved-specialist-started-specialist";
    events.push(json!({
        "id": specialist_started_id,
        "branch": "main",
        "parent": result_id,
        "kind": "attempt-started",
        "task_id": task_id,
        "task": null,
        "attempt_id": specialist_attempt_id,
        "attempt": {
            "id": specialist_attempt_id,
            "task_id": task_id,
            "actor": "agent",
            "status": "started",
            "choice": null,
            "proposal": null,
            "receipt": {
                "provider": "codex-exec",
                "requested_model": "gpt-5.6-luna",
                "observed_model": null,
                "status": "started",
                "request_body": "",
                "response_body": null,
                "error": null,
                "usage": null
            }
        },
        "operation": null
    }));
    let specialist_result_id = "event:main:saved-specialist-result";
    events.push(json!({
        "id": specialist_result_id,
        "branch": "main",
        "parent": specialist_started_id,
        "kind": "attempt-result",
        "task_id": task_id,
        "task": null,
        "attempt_id": specialist_attempt_id,
        "attempt": {
            "id": specialist_attempt_id,
            "task_id": task_id,
            "actor": "agent",
            "status": "answered",
            "choice": null,
            "proposal": specialist_response,
            "receipt": specialist_receipt
        },
        "operation": null
    }));
    branches["main"]["head"] = specialist_result_id.into();
    fs::write(
        history.join("events.jsonl"),
        events
            .iter()
            .map(Value::to_string)
            .collect::<Vec<_>>()
            .join("\n")
            + "\n",
    )
    .expect("write saved attempts");
    fs::write(
        history.join("branches.json"),
        serde_json::to_string(&branches).expect("branches JSON"),
    )
    .expect("write saved branch");

    let mut jev = PanicJev;
    let response = json!({
        "proposal": {"operation": {"kind": "select-community-access", "payload": {
            "candidate_id": candidate_id,
            "community_id": community_id,
            "provisional": true,
            "decision_reason": "unused provider",
            "uncertainties": ["unused provider"]
        }}}
    });
    let mut specialist = RuralSpecialist { response, calls: 0 };
    let resumed = run_prepared(
        &history,
        &prepared,
        MidendConfig::live(true),
        ProviderSet {
            classifier: Some(&mut jev),
            specialist: Some(&mut specialist),
        },
        &mut |_event| {},
    )
    .expect("resume from saved attempts");
    assert_eq!(specialist.calls, 0, "saved specialist must be reused");
    assert!(matches!(
        resumed.operations.first(),
        Some(TypedOperation::SelectCommunityAccess {
            decision_class,
            provisional: true,
            reason: Some(reason),
            ..
        }) if decision_class == "agent"
            && reason == "Retained specialist evidence remains the selected answer."
    ));
}

#[test]
fn prepared_rural_replay_reuses_retained_operations_without_providers() {
    let root = rural_prepared_fixture("replay");
    let prepared = prepare_with_progress(
        &root.join("area.yaml"),
        CompileOptions::default(),
        &mut |_event| {},
    )
    .expect("prepared rural fixture");
    let history = root.join("history");
    let mut jev = RuralChoiceJev {
        calls: 0,
        choices: Vec::new(),
        choose_comfort: true,
    };
    let first = run_prepared(
        &history,
        &prepared,
        MidendConfig::live(false),
        ProviderSet {
            classifier: Some(&mut jev),
            specialist: None,
        },
        &mut |_event| {},
    )
    .expect("prepared rural run");
    assert_eq!(jev.calls, 1);

    let replayed = replay(&history, "main", &mut |_event| {}).expect("rural replay");
    assert_eq!(replayed.operations, first.operations);
    assert_eq!(replayed.community_access, first.community_access);
}

#[test]
fn rural_fork_discards_descendants_and_replays_a_replacement_parent_choice() {
    let root = rural_prepared_fixture("fork");
    let prepared = prepare_with_progress(
        &root.join("area.yaml"),
        CompileOptions::default(),
        &mut |_event| {},
    )
    .expect("prepared rural fixture");
    let history = root.join("history");
    let mut jev = RuralChoiceJev {
        calls: 0,
        choices: Vec::new(),
        choose_comfort: true,
    };
    let first = run_prepared(
        &history,
        &prepared,
        MidendConfig::live(false),
        ProviderSet {
            classifier: Some(&mut jev),
            specialist: None,
        },
        &mut |_event| {},
    )
    .expect("prepared rural run");
    let parent_task = first
        .task_ids
        .iter()
        .find(|task_id| task_id.as_str() == "task:rural:parent")
        .expect("retained parent task")
        .clone();
    fork(&history, "main", "shortest", &parent_task).expect("fork before parent decision");

    let mut replacement = RuralChoiceJev {
        calls: 0,
        choices: Vec::new(),
        choose_comfort: false,
    };
    let branch = run_prepared(
        &history,
        &prepared,
        MidendConfig::live(false).with_branch("shortest"),
        ProviderSet {
            classifier: Some(&mut replacement),
            specialist: None,
        },
        &mut |_event| {},
    )
    .expect("replacement rural branch");
    assert_eq!(replacement.choices, vec!["rural:parent:shortest"]);
    assert!(matches!(
        branch.operations.first(),
        Some(TypedOperation::SelectCommunityAccess { candidate_id, .. })
            if candidate_id == "rural:parent:shortest"
    ));
    assert_eq!(branch.operations.len(), 2);
    assert!(branch.task_ids.contains(&"task:rural:child".to_string()));
}

#[test]
fn unresolved_rural_parent_is_not_used_as_a_child_frontier() {
    let root = rural_prepared_fixture("unresolved-parent");
    let prepared = prepare_with_progress(
        &root.join("area.yaml"),
        CompileOptions::default(),
        &mut |_event| {},
    )
    .expect("prepared rural fixture");
    let mut jev = RuralUnknownJev { calls: 0 };
    let result = run_prepared(
        &root.join("history"),
        &prepared,
        MidendConfig::live(false),
        ProviderSet {
            classifier: Some(&mut jev),
            specialist: None,
        },
        &mut |_event| {},
    )
    .expect("unresolved rural run");
    assert_eq!(jev.calls, 1);
    assert!(matches!(
        result.operations.first(),
        Some(TypedOperation::UnresolvedCommunityAccess { community_id, .. })
            if community_id == "parent"
    ));
    assert!(
        result
            .community_access
            .iter()
            .all(|access| access.parent_community_id.as_deref() != Some("parent")),
        "a child must not be served from an unresolved parent"
    );
    let parent = result
        .community_access
        .iter()
        .find(|access| access.community_id == "parent")
        .expect("unresolved parent projection");
    assert_eq!(parent.status, "unresolved");
    assert!(parent.path_edge_ids.is_empty());
    assert!(parent.path_geometry.is_empty());
    assert!(parent.parent_community_id.is_none());
    assert!(parent.root_spine_id.is_none());
    assert!(parent.new_link_length_m.is_none());
    assert!(parent.full_access_length_m.is_none());
    assert!(parent.new_link_topography.is_none());
    assert!(parent.full_access_topography.is_none());
}

#[test]
fn unknown_rural_jev_escalates_to_typed_provisional_specialist_choice() {
    let root = rural_prepared_fixture("rural-specialist");
    let prepared = prepare_with_progress(
        &root.join("area.yaml"),
        CompileOptions::default(),
        &mut |_event| {},
    )
    .expect("prepared rural fixture");
    let response = json!({
        "proposal": {"operation": {"kind": "select-community-access", "payload": {
            "candidate_id": "rural:parent:comfort",
            "community_id": "parent",
            "provisional": true,
            "decision_reason": "The flatter complete journey is the supported best guess.",
            "uncertainties": ["Provision remains unknown."]
        }}}
    });
    let mut jev = RuralUnknownJev { calls: 0 };
    let mut specialist = RuralSpecialist { response, calls: 0 };
    let result = run_prepared(
        &root.join("history"),
        &prepared,
        MidendConfig::live(true),
        ProviderSet {
            classifier: Some(&mut jev),
            specialist: Some(&mut specialist),
        },
        &mut |_event| {},
    )
    .expect("rural specialist run");
    assert_eq!(jev.calls, 1);
    assert_eq!(specialist.calls, 1);
    assert!(matches!(
        result.operations.first(),
        Some(TypedOperation::SelectCommunityAccess {
            community_id,
            candidate_id,
            decision_class,
            provisional: true,
            reason: Some(reason),
            uncertainties,
            ..
        }) if community_id == "parent"
            && candidate_id == "rural:parent:comfort"
            && decision_class == "agent"
            && reason == "The flatter complete journey is the supported best guess."
            && uncertainties == &["Provision remains unknown.".to_string()]
    ));
    assert_eq!(result.operations.len(), 2);

    let saved_attempt = fs::read_to_string(root.join("history/events.jsonl"))
        .expect("specialist history")
        .lines()
        .filter_map(|line| serde_json::from_str::<Value>(line).ok())
        .find(|event| {
            event["kind"] == "attempt-result"
                && event["attempt"]["actor"] == "agent"
                && event["attempt"]["proposal"]["proposal"]["operation"]["kind"]
                    == "select-community-access"
        })
        .expect("saved specialist attempt");
    assert_eq!(
        saved_attempt["attempt"]["proposal"]["proposal"]["operation"]["payload"]["decision_reason"],
        "The flatter complete journey is the supported best guess."
    );
    assert_eq!(
        saved_attempt["attempt"]["proposal"]["proposal"]["operation"]["payload"]["uncertainties"]
            [0],
        "Provision remains unknown."
    );
    assert_eq!(
        saved_attempt["attempt"]["proposal"]["proposal"]["operation"]["payload"]["provisional"],
        true
    );
}

#[test]
fn genuine_gap_can_resume_without_an_offer_or_provider() {
    let root = rural_prepared_fixture("replay-gap");
    let network_path = root.join("snapshot/network.geojson");
    let mut network: Value =
        serde_json::from_str(&fs::read_to_string(&network_path).expect("network")).expect("json");
    add_bidirectional(
        network["features"].as_array_mut().expect("features"),
        "isolated",
        "isolated-end",
        [2.0, 2.0],
        [2.01, 2.0],
        10.0,
        "residential",
        None,
    );
    fs::write(
        &network_path,
        serde_json::to_vec(&network).expect("network json"),
    )
    .expect("write network");
    let places_path = root.join("snapshot/places.geojson");
    let mut places: Value =
        serde_json::from_str(&fs::read_to_string(&places_path).expect("places")).expect("json");
    places["features"]
        .as_array_mut()
        .expect("features")
        .push(place_feature("isolated", "Isolated", "village", [2.0, 2.0]));
    fs::write(
        &places_path,
        serde_json::to_vec(&places).expect("places json"),
    )
    .expect("write places");
    let prepared = prepare_with_progress(
        &root.join("area.yaml"),
        CompileOptions::default(),
        &mut |_event| {},
    )
    .expect("prepared rural fixture");
    let history = root.join("history");
    let mut jev = RuralChoiceJev {
        calls: 0,
        choices: Vec::new(),
        choose_comfort: true,
    };
    let first = run_prepared(
        &history,
        &prepared,
        MidendConfig::live(false),
        ProviderSet {
            classifier: Some(&mut jev),
            specialist: None,
        },
        &mut |_event| {},
    )
    .expect("prepared rural run");
    assert_eq!(jev.calls, 1);
    let gap = first
        .community_access
        .iter()
        .find(|access| access.community_id == "isolated")
        .expect("retained graph gap");
    assert_eq!(gap.status, "network-gap");
    assert!(gap.path_edge_ids.is_empty());
    assert!(gap.parent_community_id.is_none());
    assert!(gap.root_spine_id.is_none());
    assert!(gap.full_access_length_m.is_none());

    let resumed = run_prepared(
        &history,
        &prepared,
        MidendConfig::live(false),
        ProviderSet {
            classifier: None,
            specialist: None,
        },
        &mut |_event| {},
    )
    .expect("resume with genuine gap");
    assert_eq!(resumed.operations, first.operations);
    assert_eq!(resumed.community_access, first.community_access);
}

fn rural_prepared_fixture(label: &str) -> PathBuf {
    let root = std::env::temp_dir().join(format!(
        "satn-rust-midend-rural-{label}-{}",
        std::process::id()
    ));
    if root.exists() {
        fs::remove_dir_all(&root).expect("clean rural fixture");
    }
    let snapshot = root.join("snapshot");
    fs::create_dir_all(&snapshot).expect("rural snapshot");
    fs::write(
        root.join("area.yaml"),
        format!(
            "area_id: fixture\narea_name: Fixture\nsource:\n  snapshot_dir: {}\n  snapshot_id: snapshot\n  community_place_types: [village]\n  national_elevation:\n    path: {}\ncompilation:\n  max_connection_km: 15\n",
            root.display(),
            root.join("elevation.geojson").display()
        ),
    )
    .expect("rural config");
    let mut edges = Vec::new();
    add_bidirectional(
        &mut edges,
        "parent",
        "high",
        [0.0, 0.0],
        [0.01, 0.0],
        10.0,
        "residential",
        None,
    );
    add_bidirectional(
        &mut edges,
        "high",
        "spine",
        [0.01, 0.0],
        [0.02, 0.0],
        10.0,
        "primary",
        Some("A1"),
    );
    add_bidirectional(
        &mut edges,
        "parent",
        "flat",
        [0.0, 0.0],
        [0.0, 0.01],
        15.0,
        "residential",
        None,
    );
    add_bidirectional(
        &mut edges,
        "flat",
        "spine",
        [0.0, 0.01],
        [0.02, 0.0],
        15.0,
        "residential",
        None,
    );
    write_collection(&snapshot.join("network.geojson"), edges);
    write_collection(
        &snapshot.join("places.geojson"),
        vec![
            place_feature("parent", "Parent", "village", [0.0, 0.0]),
            place_feature("child", "Child", "village", [0.0, 0.005]),
        ],
    );
    write_collection(
        &root.join("elevation.geojson"),
        vec![
            elevation_feature("high-0", [0.0, 0.0], 0.0),
            elevation_feature("high-1", [0.002, 0.0], 4.0),
            elevation_feature("high-2", [0.004, 0.0], 8.0),
            elevation_feature("high-3", [0.006, 0.0], 12.0),
            elevation_feature("high-4", [0.008, 0.0], 16.0),
            elevation_feature("high-5", [0.01, 0.0], 20.0),
            elevation_feature("spine-1", [0.012, 0.0], 16.0),
            elevation_feature("spine-2", [0.014, 0.0], 12.0),
            elevation_feature("spine-3", [0.016, 0.0], 8.0),
            elevation_feature("spine-4", [0.018, 0.0], 4.0),
            elevation_feature("spine-5", [0.02, 0.0], 0.0),
            elevation_feature("flat-0", [0.0, 0.0], 0.0),
            elevation_feature("flat-1", [0.0, 0.002], 0.2),
            elevation_feature("flat-2", [0.0, 0.004], 0.4),
            elevation_feature("flat-3", [0.0, 0.006], 0.6),
            elevation_feature("flat-4", [0.0, 0.008], 0.8),
            elevation_feature("flat-5", [0.0, 0.01], 1.0),
            elevation_feature("flat-spine-1", [0.002, 0.009], 1.1),
            elevation_feature("flat-spine-2", [0.004, 0.008], 1.2),
            elevation_feature("flat-spine-3", [0.006, 0.007], 1.3),
            elevation_feature("flat-spine-4", [0.008, 0.006], 1.4),
            elevation_feature("flat-spine-5", [0.01, 0.005], 1.5),
            elevation_feature("flat-spine-6", [0.012, 0.004], 1.6),
            elevation_feature("flat-spine-7", [0.014, 0.003], 1.7),
            elevation_feature("flat-spine-8", [0.016, 0.002], 1.8),
            elevation_feature("flat-spine-9", [0.018, 0.001], 1.9),
            elevation_feature("flat-spine-10", [0.02, 0.0], 2.0),
        ],
    );
    root
}

fn rural_destination_fixture(label: &str) -> PathBuf {
    let root = rural_prepared_fixture(label);
    let network_path = root.join("snapshot/network.geojson");
    let mut network: Value =
        serde_json::from_str(&fs::read_to_string(&network_path).expect("network")).expect("json");
    add_bidirectional(
        network["features"].as_array_mut().expect("features"),
        "parent",
        "town-entry",
        [0.0, 0.0],
        [0.0, -0.01],
        12.0,
        "residential",
        None,
    );
    add_bidirectional(
        network["features"].as_array_mut().expect("features"),
        "town-entry",
        "town-spine",
        [0.0, -0.01],
        [0.02, -0.01],
        3.0,
        "primary",
        Some("A2"),
    );
    add_bidirectional(
        network["features"].as_array_mut().expect("features"),
        "town-spine",
        "town",
        [0.02, -0.01],
        [0.02, -0.02],
        1.0,
        "residential",
        None,
    );
    fs::write(
        &network_path,
        serde_json::to_vec(&network).expect("network json"),
    )
    .expect("write network");

    let places_path = root.join("snapshot/places.geojson");
    let mut places: Value =
        serde_json::from_str(&fs::read_to_string(&places_path).expect("places")).expect("json");
    places["features"]
        .as_array_mut()
        .expect("place features")
        .push(place_feature("town", "Town", "town", [0.02, -0.02]));
    fs::write(
        &places_path,
        serde_json::to_vec(&places).expect("places json"),
    )
    .expect("write places");
    root
}

fn urban_entry_fixture(label: &str) -> PathBuf {
    let root = std::env::temp_dir().join(format!(
        "satn-rust-midend-urban-entry-{label}-{}",
        std::process::id()
    ));
    if root.exists() {
        fs::remove_dir_all(&root).expect("clean urban-entry fixture");
    }
    let snapshot = root.join("snapshot");
    fs::create_dir_all(&snapshot).expect("urban-entry snapshot");
    fs::write(
        root.join("area.yaml"),
        format!(
            "area_id: fixture\narea_name: Fixture\nsource:\n  snapshot_dir: {}\n  snapshot_id: snapshot\n  community_place_types: [village]\ncompilation:\n  max_connection_km: 15\n",
            root.display()
        ),
    )
    .expect("urban-entry config");
    let mut edges = Vec::new();
    add_bidirectional(
        &mut edges,
        "community",
        "urban-edge",
        [0.0, 0.0],
        [0.001, 0.0],
        100.0,
        "residential",
        None,
    );
    add_bidirectional(
        &mut edges,
        "child",
        "community",
        [0.0002, 0.0002],
        [0.0, 0.0],
        10.0,
        "residential",
        None,
    );
    add_bidirectional(
        &mut edges,
        "urban-edge",
        "spine",
        [0.001, 0.0],
        [0.002, 0.0],
        100.0,
        "primary",
        Some("A1"),
    );
    write_collection(&snapshot.join("network.geojson"), edges);
    write_collection(
        &snapshot.join("places.geojson"),
        vec![
            place_feature("community", "Community", "village", [0.0, 0.0]),
            place_feature("child", "Child", "village", [0.0002, 0.0002]),
        ],
    );
    write_collection(
        &snapshot.join("osm-place-features.geojson"),
        vec![
            json!({
                "type": "Feature",
                "properties": {"id": 1947201, "name": "Bath", "place": "city", "wikidata": "Q22889"},
                "geometry": {"type": "Point", "coordinates": [0.001, 0.0]}
            }),
            json!({
                "type": "Feature",
                "properties": {"id": 5342409, "name": "Bath", "place": "city", "boundary": "place", "wikidata": "Q22889"},
                "geometry": {"type": "Polygon", "coordinates": [[[0.0008, -0.0001], [0.0012, -0.0001], [0.0012, 0.0001], [0.0008, 0.0001], [0.0008, -0.0001]]]}
            }),
        ],
    );
    root
}

fn place_feature(id: &str, name: &str, class: &str, point: [f64; 2]) -> Value {
    json!({
        "type": "Feature",
        "properties": {"place_id": id, "source_id": id, "name": name, "place_class": class},
        "geometry": {"type": "Point", "coordinates": point}
    })
}

fn elevation_feature(id: &str, point: [f64; 2], elevation_m: f64) -> Value {
    json!({
        "type": "Feature",
        "properties": {"evidence_id": id, "source_id": "fixture-dtm", "elevation_m": elevation_m},
        "geometry": {"type": "Point", "coordinates": point}
    })
}

fn add_bidirectional(
    edges: &mut Vec<Value>,
    from: &str,
    to: &str,
    start: [f64; 2],
    end: [f64; 2],
    length: f64,
    highway: &str,
    reference: Option<&str>,
) {
    for (u, v, coordinates) in [
        (from, to, json!([start, end])),
        (to, from, json!([end, start])),
    ] {
        let mut properties =
            json!({"u": u, "v": v, "key": 0, "length": length, "highway": highway});
        if let Some(reference) = reference {
            properties["ref"] = json!(reference);
        }
        edges.push(json!({
            "type": "Feature",
            "properties": properties,
            "geometry": {"type": "LineString", "coordinates": coordinates}
        }));
    }
}

fn write_collection(path: &Path, features: Vec<Value>) {
    fs::write(
        path,
        serde_json::to_string(&json!({"type": "FeatureCollection", "features": features}))
            .expect("feature collection"),
    )
    .expect("write feature collection");
}
