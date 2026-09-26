use satn_rs::midend::{MidendRun, TypedOperation};
use satn_rs::officer::{
    OfficerDecision, OfficerDecisionLedger, OfficerOutcomeStatus, apply_officer_decisions,
};
use satn_rs::{AccountingSummary, Candidate, CompileReport, Connection};
use serde_json::json;

fn report() -> CompileReport {
    CompileReport {
        area_id: "fixture".into(),
        deployment_id: "fixture".into(),
        attribution: String::new(),
        source_attributions: Vec::new(),
        title: "Fixture".into(),
        snapshot_id: "snapshot".into(),
        source_inventory_count: 0,
        unknown_fact_count: 0,
        connection_count: 2,
        candidate_count: 3,
        operation_count: 0,
        boundary_scope: None,
        source_inventory: Vec::new(),
        unknown_facts: Vec::new(),
        network_places: Vec::new(),
        school_context: Vec::new(),
        community_access: Vec::new(),
        access_obligations: Vec::new(),
        destination_profile: String::new(),
        accounting: AccountingSummary::default(),
        connections: vec![
            connection("connection:alpha:beta"),
            connection("connection:beta:gamma"),
        ],
        candidates: vec![
            candidate("candidate:alpha", "connection:alpha:beta"),
            candidate("candidate:beta", "connection:alpha:beta"),
            candidate("candidate:gamma", "connection:beta:gamma"),
        ],
        candidate_neighbourhoods: Vec::new(),
        operations: Vec::new(),
    }
}

fn connection(id: &str) -> Connection {
    Connection {
        id: id.into(),
        origin_place_id: "origin".into(),
        origin_name: "Origin".into(),
        destination_place_id: "destination".into(),
        destination_name: "Destination".into(),
        origin_node: "n1".into(),
        destination_node: "n2".into(),
        cross_region_edge_ids: Vec::new(),
        road_classes: Vec::new(),
        preferred_classes: Vec::new(),
    }
}

fn candidate(id: &str, connection_id: &str) -> Candidate {
    Candidate {
        id: id.into(),
        connection_id: connection_id.into(),
        status: "admitted".into(),
        decision_class: "mechanical".into(),
        role: "direct".into(),
        role_aliases: Vec::new(),
        length_m: 100.0,
        search_cost_m: 100.0,
        a_road_share: 0.0,
        ncn_share: 0.0,
        cycle_alignment_bases: Vec::new(),
        topology_status: "graph-supported".into(),
        provision_status: "unknown".into(),
        path_edge_ids: vec![format!("edge:{id}")],
        path_edge_geometries: Vec::new(),
        geometry: vec![[0.0, 0.0], [1.0, 0.0]],
    }
}

fn baseline(candidate_id: &str) -> MidendRun {
    MidendRun {
        branch: "main".into(),
        base_id: "base:fixture:snapshot".into(),
        status: "replayed".into(),
        task_ids: vec!["task:connection:alpha:beta".into()],
        operations: vec![TypedOperation::SelectAlignment {
            id: "decision:agent".into(),
            task_id: "task:connection:alpha:beta".into(),
            attempt_id: "attempt:classifier".into(),
            connection_id: "connection:alpha:beta".into(),
            candidate_id: candidate_id.into(),
            decision_class: "agent".into(),
            provisional: false,
            reason: Some("Classifier chose the baseline candidate.".into()),
            uncertainties: vec!["Provision is unknown.".into()],
        }],
        community_access: Vec::new(),
    }
}

fn decision(connection_id: &str, candidate_id: Option<&str>) -> OfficerDecision {
    OfficerDecision {
        decision_id: "officer-example:decision-1".into(),
        connection_id: connection_id.into(),
        candidate_id: candidate_id.map(str::to_string),
        source_refs: vec!["source:committee-note-1".into()],
        attribution: "Example active travel officer".into(),
        rationale: "The alternative better serves the school access objective.".into(),
    }
}

#[test]
fn officer_alternative_is_applied_without_rewriting_baseline_provenance() {
    let report = report();
    let baseline = baseline("candidate:alpha");
    let ledger = OfficerDecisionLedger {
        decisions: vec![decision("connection:alpha:beta", Some("candidate:beta"))],
        strategic_network: None,
    };

    let (effective, scenario) =
        apply_officer_decisions(&report, &baseline, &ledger).expect("valid officer binding");

    assert_eq!(
        baseline.operations[0].candidate_id(),
        Some("candidate:alpha")
    );
    assert_eq!(baseline.operations[0].decision_class(), "agent");
    assert_eq!(
        effective.operations[0].candidate_id(),
        Some("candidate:beta")
    );
    assert_eq!(effective.operations[0].decision_class(), "mechanical");
    assert!(matches!(
        &effective.operations[0],
        TypedOperation::SelectAlignment {
            id,
            task_id,
            attempt_id,
            ..
        }
            if id.starts_with("officer-ledger:")
                && task_id.starts_with("officer-ledger:")
                && attempt_id.starts_with("officer-ledger:")
                && !attempt_id.contains("classifier")
    ));
    assert_eq!(scenario.authority, "officer-example");
    assert_eq!(
        scenario.outcomes[0].baseline_candidate_id.as_deref(),
        Some("candidate:alpha")
    );
    assert_eq!(
        scenario.outcomes[0].officer_candidate_id.as_deref(),
        Some("candidate:beta")
    );
    assert_eq!(
        scenario.outcomes[0].effective_candidate_id.as_deref(),
        Some("candidate:beta")
    );
    assert_eq!(
        scenario.outcomes[0].status,
        OfficerOutcomeStatus::Divergence
    );
    assert_eq!(
        scenario.outcomes[0].baseline_decision_class.as_deref(),
        Some("agent")
    );
    assert!(scenario.outcomes[0].baseline_operation.is_some());
}

#[test]
fn unavailable_targets_remain_visible_and_do_not_change_the_effective_selection() {
    let report = report();
    let baseline = baseline("candidate:alpha");
    let ledger = OfficerDecisionLedger {
        decisions: vec![decision("connection:alpha:beta", None)],
        strategic_network: None,
    };

    let (effective, scenario) =
        apply_officer_decisions(&report, &baseline, &ledger).expect("unbound decision is visible");

    assert_eq!(
        effective.operations[0].candidate_id(),
        Some("candidate:alpha")
    );
    assert_eq!(
        scenario.outcomes[0].status,
        OfficerOutcomeStatus::Unavailable
    );
    assert_eq!(scenario.outcomes[0].officer_candidate_id, None);
    assert_eq!(
        scenario.outcomes[0].effective_candidate_id.as_deref(),
        Some("candidate:alpha")
    );
}

#[test]
fn exact_connection_binding_rejects_cross_connection_candidates() {
    let ledger = OfficerDecisionLedger {
        decisions: vec![decision("connection:alpha:beta", Some("candidate:gamma"))],
        strategic_network: None,
    };
    let error = apply_officer_decisions(&report(), &baseline("candidate:alpha"), &ledger)
        .expect_err("candidate from another connection must be rejected");

    assert!(error.to_string().contains("outside connection"));
}

#[test]
fn missing_candidates_are_unavailable_and_decisions_survive_baseline_changes() {
    let report = report();
    let ledger = OfficerDecisionLedger {
        decisions: vec![decision("connection:alpha:beta", Some("candidate:beta"))],
        strategic_network: None,
    };

    let (first_effective, first_scenario) =
        apply_officer_decisions(&report, &baseline("candidate:alpha"), &ledger)
            .expect("decision applies to exact target");
    let (repeat_effective, repeat_scenario) =
        apply_officer_decisions(&report, &baseline("candidate:alpha"), &ledger)
            .expect("replay is deterministic");
    let (recompiled_effective, recompiled_scenario) =
        apply_officer_decisions(&report, &baseline("candidate:beta"), &ledger)
            .expect("current baseline is provenance, not an input gate");
    let unavailable = OfficerDecisionLedger {
        decisions: vec![decision(
            "connection:alpha:beta",
            Some("candidate:no-longer-admitted"),
        )],
        strategic_network: None,
    };
    let (unavailable_effective, unavailable_scenario) =
        apply_officer_decisions(&report, &baseline("candidate:alpha"), &unavailable)
            .expect("a missing target stays visible");

    assert_eq!(first_effective, repeat_effective);
    assert_eq!(
        serde_json::to_value(first_scenario).expect("serialize"),
        serde_json::to_value(repeat_scenario).expect("serialize repeat")
    );
    assert_eq!(
        recompiled_effective.operations[0].candidate_id(),
        Some("candidate:beta")
    );
    assert_eq!(
        recompiled_scenario.outcomes[0].status,
        OfficerOutcomeStatus::Agreement
    );
    assert_eq!(
        unavailable_effective.operations[0].candidate_id(),
        Some("candidate:alpha")
    );
    assert_eq!(
        unavailable_scenario.outcomes[0].status,
        OfficerOutcomeStatus::Unavailable
    );
    assert_eq!(
        unavailable_scenario.outcomes[0]
            .officer_candidate_id
            .as_deref(),
        Some("candidate:no-longer-admitted")
    );
}

#[test]
fn duplicate_and_malformed_decisions_are_rejected() {
    let first = decision("connection:alpha:beta", Some("candidate:alpha"));
    let mut duplicate_id = decision("connection:beta:gamma", Some("candidate:gamma"));
    duplicate_id.decision_id = first.decision_id.clone();
    let duplicate_ids = OfficerDecisionLedger {
        decisions: vec![first.clone(), duplicate_id],
        strategic_network: None,
    };
    let duplicate_targets = OfficerDecisionLedger {
        decisions: vec![
            first,
            decision("connection:alpha:beta", Some("candidate:beta")),
        ],
        strategic_network: None,
    };
    let mut malformed = decision("connection:alpha:beta", Some("candidate:alpha"));
    malformed.attribution = "  ".into();
    malformed.source_refs.clear();
    let malformed_ledger = OfficerDecisionLedger {
        decisions: vec![malformed],
        strategic_network: None,
    };

    assert!(
        apply_officer_decisions(&report(), &baseline("candidate:alpha"), &duplicate_ids).is_err()
    );
    assert!(
        apply_officer_decisions(&report(), &baseline("candidate:alpha"), &duplicate_targets)
            .is_err()
    );
    assert!(
        apply_officer_decisions(&report(), &baseline("candidate:alpha"), &malformed_ledger)
            .is_err()
    );
}

#[test]
fn legacy_officer_ledger_without_network_scope_remains_valid() {
    let ledger: OfficerDecisionLedger = serde_json::from_value(json!({
        "decisions": [{
            "decision_id": "legacy-example",
            "connection_id": "connection:alpha:beta",
            "candidate_id": "candidate:beta",
            "source_refs": ["legacy-source"],
            "attribution": "Legacy officer example",
            "rationale": "Retain the original per-journey decision format."
        }]
    }))
    .expect("legacy ledger without network scope");

    assert!(ledger.strategic_network.is_none());
}
