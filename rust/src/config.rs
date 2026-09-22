use std::path::{Path, PathBuf};

use serde::Deserialize;

use crate::error::{Result, SatnError};

#[derive(Debug, Deserialize)]
pub(crate) struct AreaConfig {
    pub area_id: Option<String>,
    pub area_name: Option<String>,
    pub deployment_id: Option<String>,
    pub source: SourceConfig,
    #[serde(default)]
    pub compilation: CompilationConfig,
}

#[derive(Debug, Deserialize)]
pub(crate) struct SourceConfig {
    pub snapshot_dir: PathBuf,
    pub snapshot_id: String,
    #[serde(default = "default_community_place_types")]
    pub community_place_types: Vec<String>,
    #[serde(default = "default_urban_scope_buffer_km")]
    pub urban_scope_buffer_km: f64,
}

#[derive(Debug, Clone, Deserialize)]
pub(crate) struct CompilationConfig {
    #[serde(default = "default_max_connection_km")]
    pub max_connection_km: f64,
}

impl Default for CompilationConfig {
    fn default() -> Self {
        Self {
            max_connection_km: default_max_connection_km(),
        }
    }
}

fn default_max_connection_km() -> f64 {
    15.0
}

fn default_urban_scope_buffer_km() -> f64 {
    2.0
}

fn default_community_place_types() -> Vec<String> {
    [
        "city",
        "town",
        "village",
        "suburb",
        "quarter",
        "neighbourhood",
    ]
    .into_iter()
    .map(str::to_string)
    .collect()
}

impl AreaConfig {
    pub(crate) fn read(path: &Path) -> Result<Self> {
        let text = std::fs::read_to_string(path)?;
        let config: Self = serde_yaml::from_str(&text)?;
        if config.source.snapshot_id.trim().is_empty() {
            return Err(SatnError::InvalidInput(
                "source.snapshot_id must not be empty".to_string(),
            ));
        }
        if !config.compilation.max_connection_km.is_finite()
            || config.compilation.max_connection_km <= 0.0
        {
            return Err(SatnError::InvalidInput(
                "compilation.max_connection_km must be finite and positive".to_string(),
            ));
        }
        if !config.source.urban_scope_buffer_km.is_finite()
            || config.source.urban_scope_buffer_km < 0.0
        {
            return Err(SatnError::InvalidInput(
                "source.urban_scope_buffer_km must be finite and non-negative".to_string(),
            ));
        }
        Ok(config)
    }

    pub(crate) fn snapshot_path(&self, config_path: &Path) -> PathBuf {
        let snapshot_dir = if self.source.snapshot_dir.is_absolute() {
            self.source.snapshot_dir.clone()
        } else {
            config_path
                .parent()
                .unwrap_or_else(|| Path::new("."))
                .join(&self.source.snapshot_dir)
        };
        snapshot_dir.join(&self.source.snapshot_id)
    }

    pub(crate) fn title(&self) -> String {
        self.area_name
            .clone()
            .or_else(|| self.area_id.clone())
            .or_else(|| self.deployment_id.clone())
            .unwrap_or_else(|| "SATN mechanical review".to_string())
    }
}
