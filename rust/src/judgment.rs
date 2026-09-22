use std::collections::BTreeMap;
use std::env;
use std::fmt::{Display, Formatter};
use std::fs;
use std::io::Write;
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};
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

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ChoiceResult {
    pub model: String,
    pub choice: String,
    pub probabilities: BTreeMap<String, f64>,
    pub confidence: f64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ProviderReceipt {
    pub provider: String,
    pub requested_model: String,
    pub observed_model: Option<String>,
    pub status: String,
    pub request_body: String,
    pub response_body: Option<String>,
    pub error: Option<String>,
    pub usage: Option<Value>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ChoiceAttempt {
    pub result: Option<ChoiceResult>,
    pub receipt: ProviderReceipt,
}

#[derive(Debug, Clone, PartialEq)]
pub struct SpecialistAttempt {
    pub response: Option<Value>,
    pub receipt: ProviderReceipt,
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
        let attempt = self.classify_choice_attempt(request);
        attempt
            .result
            .ok_or_else(|| choice_attempt_error(&attempt.receipt))
    }

    pub fn classify_choice_attempt(&self, request: &ChoiceRequest) -> ChoiceAttempt {
        let payload = match request.payload(&self.model) {
            Ok(payload) => payload,
            Err(error) => {
                return ChoiceAttempt {
                    result: None,
                    receipt: ProviderReceipt {
                        provider: "typesafe".to_string(),
                        requested_model: self.model.clone(),
                        observed_model: None,
                        status: "invalid".to_string(),
                        request_body: String::new(),
                        response_body: None,
                        error: Some(error.to_string()),
                        usage: None,
                    },
                };
            }
        };
        let body = match serde_json::to_string(&payload) {
            Ok(body) => body,
            Err(_) => {
                return ChoiceAttempt {
                    result: None,
                    receipt: ProviderReceipt {
                        provider: "typesafe".to_string(),
                        requested_model: self.model.clone(),
                        observed_model: None,
                        status: "invalid".to_string(),
                        request_body: String::new(),
                        response_body: None,
                        error: Some("Choice request is not JSON".to_string()),
                        usage: None,
                    },
                };
            }
        };
        let mut receipt = ProviderReceipt {
            provider: "typesafe".to_string(),
            requested_model: self.model.clone(),
            observed_model: None,
            status: "started".to_string(),
            request_body: body.clone(),
            response_body: None,
            error: None,
            usage: None,
        };
        let Some(credential) = self.api_key.as_deref() else {
            receipt.status = "failed".to_string();
            receipt.error = Some("TypeSafe credential is unavailable".to_string());
            return ChoiceAttempt {
                result: None,
                receipt,
            };
        };
        let authorization = format!("Bearer {credential}");
        let response = match ureq::post(&self.endpoint)
            .set("Content-Type", "application/json")
            .set("Accept", "application/json")
            .set("Authorization", &authorization)
            .send_string(&body)
        {
            Ok(response) => response,
            Err(ureq::Error::Status(status, response)) => {
                receipt.status = "failed".to_string();
                receipt.error = Some(format!("TypeSafe returned HTTP status {status}"));
                receipt.response_body = response.into_string().ok();
                return ChoiceAttempt {
                    result: None,
                    receipt,
                };
            }
            Err(ureq::Error::Transport(_transport)) => {
                receipt.status = "failed".to_string();
                receipt.error = Some("TypeSafe transport failed".to_string());
                return ChoiceAttempt {
                    result: None,
                    receipt,
                };
            }
        };
        if !(200..300).contains(&response.status()) {
            receipt.status = "failed".to_string();
            receipt.error = Some(format!(
                "TypeSafe returned HTTP status {}",
                response.status()
            ));
            receipt.response_body = response.into_string().ok();
            return ChoiceAttempt {
                result: None,
                receipt,
            };
        }
        let response_body = match response.into_string() {
            Ok(body) => body,
            Err(_) => {
                receipt.status = "failed".to_string();
                receipt.error = Some("TypeSafe transport failed".to_string());
                return ChoiceAttempt {
                    result: None,
                    receipt,
                };
            }
        };
        receipt.response_body = Some(response_body.clone());
        match decode_choice_response(request, response_body.as_bytes()) {
            Ok(result) => {
                receipt.status = "answered".to_string();
                receipt.observed_model = Some(result.model.clone());
                ChoiceAttempt {
                    result: Some(result),
                    receipt,
                }
            }
            Err(error) => {
                receipt.status = "invalid".to_string();
                receipt.error = Some(error.to_string());
                ChoiceAttempt {
                    result: None,
                    receipt,
                }
            }
        }
    }
}

fn choice_attempt_error(receipt: &ProviderReceipt) -> JudgmentError {
    let message = receipt
        .error
        .clone()
        .unwrap_or_else(|| "TypeSafe attempt failed".to_string());
    if message == "TypeSafe credential is unavailable" {
        JudgmentError::MissingCredential
    } else if message == "TypeSafe transport failed" {
        JudgmentError::Transport
    } else if let Some(status) = message
        .strip_prefix("TypeSafe returned HTTP status ")
        .and_then(|value| value.parse::<u16>().ok())
    {
        JudgmentError::ProviderStatus(status)
    } else if message.starts_with("invalid TypeSafe response:") {
        JudgmentError::InvalidResponse(message)
    } else if message.starts_with("invalid judgment input:")
        || message == "Choice request is not JSON"
    {
        JudgmentError::InvalidInput(message)
    } else {
        JudgmentError::Transport
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
    let attempt = run_codex_attempt(config, prompt);
    if let Some(response) = attempt.response {
        return Ok(response);
    }
    Err(codex_attempt_error(&attempt.receipt))
}

pub fn run_codex_attempt(config: &CodexConfig, prompt: &str) -> SpecialistAttempt {
    let mut receipt = ProviderReceipt {
        provider: "codex-exec".to_string(),
        requested_model: config.model.clone(),
        observed_model: None,
        status: "started".to_string(),
        request_body: prompt.to_string(),
        response_body: None,
        error: None,
        usage: None,
    };
    if prompt.trim().is_empty() {
        receipt.status = "invalid".to_string();
        receipt.error = Some("Codex prompt must not be empty".to_string());
        return SpecialistAttempt {
            response: None,
            receipt,
        };
    }
    let (output_dir, output_path) = match create_output_path() {
        Ok(paths) => paths,
        Err(_) => {
            receipt.status = "failed".to_string();
            receipt.error = Some("Codex process I/O failed".to_string());
            return SpecialistAttempt {
                response: None,
                receipt,
            };
        }
    };
    let reasoning_config = format!("model_reasoning_effort=\"{}\"", config.reasoning_effort);
    let mut child = match Command::new(&config.executable)
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
        .arg(&output_path)
        .arg("--json")
        .arg("-")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
    {
        Ok(child) => child,
        Err(_) => {
            let _ = fs::remove_dir_all(output_dir);
            receipt.status = "failed".to_string();
            receipt.error = Some("Codex process could not be launched".to_string());
            return SpecialistAttempt {
                response: None,
                receipt,
            };
        }
    };
    let write_result = child
        .stdin
        .take()
        .ok_or(())
        .and_then(|mut stdin| stdin.write_all(prompt.as_bytes()).map_err(|_| ()));
    let output = match child.wait_with_output() {
        Ok(output) => output,
        Err(_) => {
            let _ = fs::remove_dir_all(output_dir);
            receipt.status = "failed".to_string();
            receipt.error = Some("Codex process I/O failed".to_string());
            return SpecialistAttempt {
                response: None,
                receipt,
            };
        }
    };
    receipt.observed_model = observed_model(&output.stdout);
    if write_result.is_err() {
        let _ = fs::remove_dir_all(output_dir);
        receipt.status = "failed".to_string();
        receipt.error = Some("Codex process I/O failed".to_string());
        return SpecialistAttempt {
            response: None,
            receipt,
        };
    }
    let final_response = fs::read_to_string(&output_path).ok();
    receipt.response_body = final_response.clone();
    if !output.status.success() {
        let _ = fs::remove_dir_all(output_dir);
        receipt.status = "failed".to_string();
        receipt.error = Some(format!(
            "Codex process exited with {:?}",
            output.status.code()
        ));
        return SpecialistAttempt {
            response: None,
            receipt,
        };
    }
    let Some(final_response) = final_response else {
        let _ = fs::remove_dir_all(output_dir);
        receipt.status = "invalid".to_string();
        receipt.error = Some("final message is missing".to_string());
        return SpecialistAttempt {
            response: None,
            receipt,
        };
    };
    if final_response.trim().is_empty() {
        let _ = fs::remove_dir_all(output_dir);
        receipt.status = "invalid".to_string();
        receipt.error = Some("final message is blank".to_string());
        return SpecialistAttempt {
            response: None,
            receipt,
        };
    }
    let response: Value = match serde_json::from_str(&final_response) {
        Ok(response) => response,
        Err(_) => {
            let _ = fs::remove_dir_all(output_dir);
            receipt.status = "invalid".to_string();
            receipt.error = Some("final message is not JSON".to_string());
            return SpecialistAttempt {
                response: None,
                receipt,
            };
        }
    };
    if !response.is_object() {
        let _ = fs::remove_dir_all(output_dir);
        receipt.status = "invalid".to_string();
        receipt.error = Some("final message must be a JSON object".to_string());
        return SpecialistAttempt {
            response: None,
            receipt,
        };
    }
    let _ = fs::remove_dir_all(output_dir);
    receipt.status = "answered".to_string();
    SpecialistAttempt {
        response: Some(response),
        receipt,
    }
}

fn observed_model(stdout: &[u8]) -> Option<String> {
    std::str::from_utf8(stdout).ok()?.lines().find_map(|line| {
        let value: Value = serde_json::from_str(line).ok()?;
        let object = value.as_object()?;
        let event_type = object.get("type")?.as_str()?;
        if !matches!(
            event_type,
            "thread.started" | "turn.started" | "turn.completed" | "response.completed"
        ) {
            return None;
        }
        object
            .get("model")
            .and_then(Value::as_str)
            .filter(|model| !model.trim().is_empty())
            .map(str::to_string)
    })
}

fn codex_attempt_error(receipt: &ProviderReceipt) -> JudgmentError {
    match receipt.error.as_deref() {
        Some("Codex prompt must not be empty") => {
            JudgmentError::InvalidInput("Codex prompt must not be empty".to_string())
        }
        Some("Codex process could not be launched") => JudgmentError::ProcessLaunch,
        Some("Codex process I/O failed") => JudgmentError::ProcessIo,
        Some("final message is missing") => {
            JudgmentError::FinalResponse("final message is missing".to_string())
        }
        Some("final message is blank") => {
            JudgmentError::FinalResponse("final message is blank".to_string())
        }
        Some("final message is not JSON") => {
            JudgmentError::FinalResponse("final message is not JSON".to_string())
        }
        Some("final message must be a JSON object") => {
            JudgmentError::FinalResponse("final message must be a JSON object".to_string())
        }
        Some(message) if message.starts_with("Codex process exited with ") => {
            let code = message
                .trim_start_matches("Codex process exited with ")
                .strip_prefix("Some(")
                .and_then(|value| value.strip_suffix(')'))
                .and_then(|value| value.parse::<i32>().ok());
            JudgmentError::ProcessExit(code)
        }
        _ => JudgmentError::ProcessIo,
    }
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
