use std::fmt::{Display, Formatter};

#[derive(Debug)]
pub enum SatnError {
    Io(std::io::Error),
    Json(serde_json::Error),
    Yaml(serde_yaml::Error),
    InvalidInput(String),
}

impl Display for SatnError {
    fn fmt(&self, f: &mut Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Io(error) => write!(f, "I/O error: {error}"),
            Self::Json(error) => write!(f, "JSON error: {error}"),
            Self::Yaml(error) => write!(f, "YAML error: {error}"),
            Self::InvalidInput(message) => f.write_str(message),
        }
    }
}

impl std::error::Error for SatnError {}

impl From<std::io::Error> for SatnError {
    fn from(error: std::io::Error) -> Self {
        Self::Io(error)
    }
}

impl From<serde_json::Error> for SatnError {
    fn from(error: serde_json::Error) -> Self {
        Self::Json(error)
    }
}

impl From<serde_yaml::Error> for SatnError {
    fn from(error: serde_yaml::Error) -> Self {
        Self::Yaml(error)
    }
}

pub type Result<T> = std::result::Result<T, SatnError>;
