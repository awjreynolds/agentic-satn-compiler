mod candidate_neighbourhoods;
mod compiler;
mod config;
mod error;
mod geojson;
mod geometry;
mod graph;
pub mod judgment;
pub mod midend;
pub mod officer;
mod output;
pub mod publication;
pub mod topography;
pub mod travel_time;

pub use candidate_neighbourhoods::{CandidateNeighbourhood, CandidateNeighbourhoodGeometry};
pub use compiler::{
    AccessObligation, AccountingSummary, Candidate, CommunityAccess, CommunityAccessBenefit,
    CompileOptions, CompileReport, Connection, JourneyBatchEvaluation, JourneyBatchPair,
    JourneyBatchPathSummary, JourneyBatchProgress, JourneyBatchSuccess, JourneyBatchSummary,
    JourneyBatchTopographySummary, JourneyComparison, JourneyDestination, JourneyPairStatus,
    JourneyPath, NetworkPlace, Operation, PreparedCompilation, ProgressEvent, RuralAccessCandidate,
    RuralAccessOffer, RuralAccessPlanner, RuralDestinationEvidence, SchoolContext, SourceCorridor,
    UnknownFact, UrbanEntry, UrbanExtent, compile, compile_with_progress, prepare_with_progress,
};
pub use error::{Result, SatnError};
pub use geometry::{GRIDLESS_BNG_PROJECTION_POLICY, project_wgs84_to_bng};
pub use officer::{
    OfficerDecision, OfficerDecisionLedger, OfficerOutcome, OfficerOutcomeStatus, OfficerScenario,
    apply_officer_decisions, load_officer_decisions,
};
pub use publication::{
    BusContextPublication, DecisionMapPublication, add_bus_context, load_retained_report,
    publish_decision_map, publish_officer_scenario_map,
};
