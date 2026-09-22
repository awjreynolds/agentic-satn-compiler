use std::collections::BTreeMap;
use std::env;
use std::fmt::{Display, Formatter};
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::{Map, Value, json};

pub const TYPESAFE_ENDPOINT: &str = "https://api.typesafe.ai/v1/systemone";

static OUTPUT_COUNTER: AtomicU64 = AtomicU64::new(0);

#[derive(Debug, Clone)]
pub struct ChoiceRequest {
    pub state: Value,
    pub question_id: String,
    pub instructions: Value,
    pub options: BTreeMap<String, Value>,
}

impl ChoiceRequest {
    pub fn new(
        state: Value,
        question_id: impl Into<String>,
        instructions: Value,
        options: BTreeMap<String, Value>,
    ) -> Self {
        Self {
            state,
            question_id: question_id.into(),
            instructions,
            options,
        }
    }

    fn payload(&self, model: &str) -> Result<Value, JudgmentError> {
        if model.trim().is_empty() {
            return Err(JudgmentError::InvalidInput(
                "TypeSafe model must not be empty".to_string(),
            ));
        }
        if self.question_id.trim().is_empty() {
            return Err(JudgmentError::InvalidInput(
                "Choice question ID must not be empty".to_string(),
            ));
        }
        if self.options.is_empty() {
            return Err(JudgmentError::InvalidInput(
                "Choice options must not be empty".to_string(),
            ));
        }
        if self.options.len() > 255 {
            return Err(JudgmentError::InvalidInput(
                "Choice options exceed the provider limit".to_string(),
            ));
        }
        if !valid_instruction_value(&self.state) {
            return Err(JudgmentError::InvalidInput(
                "Choice state must be a string, object, or array".to_string(),
            ));
        }
        if !valid_instruction_value(&self.instructions) {
            return Err(JudgmentError::InvalidInput(
                "Choice instructions must be a string, object, or array".to_string(),
            ));
        }
        if self.options.keys().any(|option| option.trim().is_empty()) {
            return Err(JudgmentError::InvalidInput(
                "Choice option keys must not be empty".to_string(),
            ));
        }

        let question = json!({
            "type": "choice",
            "instructions": self.instructions,
            "criteria": self.options,
        });
        let mut questions = Map::new();
        questions.insert(self.question_id.clone(), question);

        Ok(json!({
            "state": self.state,
            "model": model,
            "questions": questions,
        }))
    }
}

fn valid_instruction_value(value: &Value) -> bool {
    value.is_string() || value.is_object() || value.is_array()
}

#[derive(Debug, Clone, PartialEq)]
pub struct ChoiceResult {
    pub model: String,
    pub choice: String,
    pub probabilities: BTreeMap<String, f64>,
    pub confidence: f64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum JudgmentError {
    InvalidInput(String),
    MissingCredential,
    Transport,
    ProviderStatus(u16),
    InvalidResponse(String),
    ProcessLaunch,
    ProcessIo,
    ProcessExit(Option<i32>),
    FinalResponse(String),
}

impl Display for JudgmentError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::InvalidInput(message) => write!(formatter, "invalid judgment input: {message}"),
            Self::MissingCredential => formatter.write_str("TypeSafe credential is unavailable"),
            Self::Transport => formatter.write_str("TypeSafe transport failed"),
            Self::ProviderStatus(status) => {
                write!(formatter, "TypeSafe returned HTTP status {status}")
            }
            Self::InvalidResponse(message) => {
                write!(formatter, "invalid TypeSafe response: {message}")
            }
            Self::ProcessLaunch => formatter.write_str("Codex process could not be launched"),
            Self::ProcessIo => formatter.write_str("Codex process I/O failed"),
            Self::ProcessExit(code) => write!(formatter, "Codex process exited with {code:?}"),
            Self::FinalResponse(message) => {
                write!(formatter, "invalid Codex final response: {message}")
            }
        }
    }
}

impl std::error::Error for JudgmentError {}

#[derive(Clone)]
pub struct TypeSafeConfig {
    pub endpoint: String,
    pub model: String,
    api_key: Option<String>,
}

impl std::fmt::Debug for TypeSafeConfig {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("TypeSafeConfig")
            .field("endpoint", &self.endpoint)
            .field("model", &self.model)
            .field("api_key", &"[redacted]")
            .finish()
    }
}

impl TypeSafeConfig {
    pub fn new(
        endpoint: impl Into<String>,
        model: impl Into<String>,
        api_key: Option<String>,
    ) -> Result<Self, JudgmentError> {
        let endpoint = endpoint.into();
        let model = model.into();
        if endpoint.trim().is_empty() {
            return Err(JudgmentError::InvalidInput(
                "TypeSafe endpoint must not be empty".to_string(),
            ));
        }
        if model.trim().is_empty() {
            return Err(JudgmentError::InvalidInput(
                "TypeSafe model must not be empty".to_string(),
            ));
        }
        if api_key
            .as_ref()
            .is_some_and(|credential| credential.trim().is_empty())
        {
            return Err(JudgmentError::InvalidInput(
                "TypeSafe credential must not be empty".to_string(),
            ));
        }
        Ok(Self {
            endpoint,
            model,
            api_key,
        })
    }

    pub fn from_env(model: impl Into<String>) -> Result<Self, JudgmentError> {
        let credential = env::var("TYPESAFE_API_KEY")
            .ok()
            .and_then(non_empty)
            .or_else(|| {
                let home = env::var_os("HOME")?;
                let path = PathBuf::from(home).join(".config/typesafe/api-key");
                fs::read_to_string(path).ok().and_then(non_empty)
            });
        Self::new(TYPESAFE_ENDPOINT, model, credential)
    }

    pub fn classify_choice(&self, request: &ChoiceRequest) -> Result<ChoiceResult, JudgmentError> {
        let credential = self
            .api_key
            .as_deref()
            .ok_or(JudgmentError::MissingCredential)?;
        let payload = request.payload(&self.model)?;
        let body = serde_json::to_string(&payload)
            .map_err(|_| JudgmentError::InvalidInput("Choice request is not JSON".to_string()))?;
        let authorization = format!("Bearer {credential}");
        let response = match ureq::post(&self.endpoint)
            .set("Content-Type", "application/json")
            .set("Accept", "application/json")
            .set("Authorization", &authorization)
            .send_string(&body)
        {
            Ok(response) => response,
            Err(ureq::Error::Status(status, _response)) => {
                return Err(JudgmentError::ProviderStatus(status));
            }
            Err(ureq::Error::Transport(_transport)) => return Err(JudgmentError::Transport),
        };
        if !(200..300).contains(&response.status()) {
            return Err(JudgmentError::ProviderStatus(response.status()));
        }
        let response_body = response
            .into_string()
            .map_err(|_| JudgmentError::Transport)?;
        decode_choice_response(request, response_body.as_bytes())
    }
}

fn non_empty(value: String) -> Option<String> {
    let value = value.trim().to_string();
    (!value.is_empty()).then_some(value)
}

pub fn decode_choice_response(
    request: &ChoiceRequest,
    body: &[u8],
) -> Result<ChoiceResult, JudgmentError> {
    let response: Value = serde_json::from_slice(body)
        .map_err(|_| JudgmentError::InvalidResponse("response JSON is invalid".to_string()))?;
    let object = response
        .as_object()
        .ok_or_else(|| invalid_response("response must be an object"))?;
    let model = object
        .get("model")
        .and_then(Value::as_str)
        .filter(|value| !value.trim().is_empty())
        .ok_or_else(|| invalid_response("response model is missing"))?
        .to_string();
    let answers = object
        .get("answers")
        .and_then(Value::as_object)
        .ok_or_else(|| invalid_response("response answers are missing"))?;
    if answers.len() != 1 || !answers.contains_key(&request.question_id) {
        return Err(invalid_response(
            "response answer IDs do not match the request",
        ));
    }
    let answer = answers
        .get(&request.question_id)
        .and_then(Value::as_object)
        .ok_or_else(|| invalid_response("Choice answer must be an object"))?;
    let choice = answer
        .get("choice")
        .and_then(Value::as_str)
        .filter(|value| request.options.contains_key(*value))
        .ok_or_else(|| invalid_response("Choice selection is not an offered option"))?
        .to_string();
    let probability_values = answer
        .get("probabilities")
        .and_then(Value::as_object)
        .ok_or_else(|| invalid_response("Choice probabilities are missing"))?;
    if probability_values.len() != request.options.len()
        || request
            .options
            .keys()
            .any(|option| !probability_values.contains_key(option))
    {
        return Err(invalid_response(
            "Choice probability keys do not match options",
        ));
    }
    let mut probabilities = BTreeMap::new();
    for (option, probability) in probability_values {
        let probability = probability
            .as_f64()
            .filter(|value| value.is_finite() && (0.0..=1.0).contains(value))
            .ok_or_else(|| invalid_response("Choice probability is outside [0, 1]"))?;
        probabilities.insert(option.clone(), probability);
    }
    let confidence = answer
        .get("confidence")
        .and_then(Value::as_f64)
        .filter(|value| value.is_finite() && (0.0..=1.0).contains(value))
        .ok_or_else(|| invalid_response("Choice confidence is outside [0, 1]"))?;

    Ok(ChoiceResult {
        model,
        choice,
        probabilities,
        confidence,
    })
}

fn invalid_response(message: &str) -> JudgmentError {
    JudgmentError::InvalidResponse(message.to_string())
}

#[derive(Debug, Clone)]
pub struct CodexConfig {
    pub executable: PathBuf,
    pub model: String,
    pub reasoning_effort: String,
}

impl CodexConfig {
    pub fn new(
        executable: impl Into<PathBuf>,
        model: impl Into<String>,
        reasoning_effort: impl Into<String>,
    ) -> Result<Self, JudgmentError> {
        let model = model.into();
        let reasoning_effort = reasoning_effort.into();
        if model.trim().is_empty() || reasoning_effort.trim().is_empty() {
            return Err(JudgmentError::InvalidInput(
                "Codex model and reasoning effort are required".to_string(),
            ));
        }
        Ok(Self {
            executable: executable.into(),
            model,
            reasoning_effort,
        })
    }
}

pub fn run_codex(config: &CodexConfig, prompt: &str) -> Result<Value, JudgmentError> {
    if prompt.trim().is_empty() {
        return Err(JudgmentError::InvalidInput(
            "Codex prompt must not be empty".to_string(),
        ));
    }
    let (output_dir, output_path) = create_output_path().map_err(|_| JudgmentError::ProcessIo)?;
    let result = run_codex_in_dir(config, prompt, &output_path);
    let _ = fs::remove_dir_all(output_dir);
    result
}

fn run_codex_in_dir(
    config: &CodexConfig,
    prompt: &str,
    output_path: &Path,
) -> Result<Value, JudgmentError> {
    let reasoning_config = format!("model_reasoning_effort=\"{}\"", config.reasoning_effort);
    let mut child = Command::new(&config.executable)
        .arg("exec")
        .arg("--model")
        .arg(&config.model)
        .arg("-c")
        .arg(reasoning_config)
        .arg("--sandbox")
        .arg("read-only")
        .arg("--ephemeral")
        .arg("--skip-git-repo-check")
        .arg("--output-last-message")
        .arg(output_path)
        .arg("--json")
        .arg("-")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|_| JudgmentError::ProcessLaunch)?;
    let write_result = child
        .stdin
        .take()
        .ok_or(JudgmentError::ProcessIo)
        .and_then(|mut stdin| {
            stdin
                .write_all(prompt.as_bytes())
                .map_err(|_| JudgmentError::ProcessIo)
        });
    let output = child
        .wait_with_output()
        .map_err(|_| JudgmentError::ProcessIo)?;
    if write_result.is_err() {
        return Err(JudgmentError::ProcessIo);
    }
    if !output.status.success() {
        return Err(JudgmentError::ProcessExit(output.status.code()));
    }

    let final_response = fs::read(output_path).map_err(|error| {
        if error.kind() == std::io::ErrorKind::NotFound {
            JudgmentError::FinalResponse("final message is missing".to_string())
        } else {
            JudgmentError::ProcessIo
        }
    })?;
    if final_response.iter().all(u8::is_ascii_whitespace) {
        return Err(JudgmentError::FinalResponse(
            "final message is blank".to_string(),
        ));
    }
    let response: Value = serde_json::from_slice(&final_response)
        .map_err(|_| JudgmentError::FinalResponse("final message is not JSON".to_string()))?;
    if !response.is_object() {
        return Err(JudgmentError::FinalResponse(
            "final message must be a JSON object".to_string(),
        ));
    }
    Ok(response)
}

fn create_output_path() -> std::io::Result<(PathBuf, PathBuf)> {
    let timestamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    loop {
        let counter = OUTPUT_COUNTER.fetch_add(1, Ordering::Relaxed);
        let directory = env::temp_dir().join(format!(
            "satn-rs-codex-{}-{timestamp}-{counter}",
            std::process::id()
        ));
        match fs::create_dir(&directory) {
            Ok(()) => return Ok((directory.clone(), directory.join("final-message.json"))),
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(error),
        }
    }
}
