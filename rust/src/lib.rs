mod compiler;
mod config;
mod error;
mod geojson;
mod geometry;
mod graph;
pub mod judgment;
mod output;

pub use compiler::{
    Candidate, CompileOptions, CompileReport, Connection, Operation, ProgressEvent, SourceCorridor,
    UnknownFact, compile, compile_with_progress,
};
pub use error::{Result, SatnError};
pub use geometry::{GRIDLESS_BNG_PROJECTION_POLICY, project_wgs84_to_bng};
