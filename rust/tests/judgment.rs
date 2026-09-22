use std::collections::BTreeMap;

use satn_rs::judgment::{
    ChoiceRequest, CodexConfig, JudgmentError, TypeSafeConfig, decode_choice_response, run_codex,
};
use serde_json::json;

fn request() -> ChoiceRequest {
    ChoiceRequest::new(
        json!({"task": "synthetic"}),
        "decision",
        json!("choose one admitted option"),
        BTreeMap::from([
            ("__unknown__".to_string(), json!("remain unresolved")),
            ("candidate-a".to_string(), json!("synthetic option")),
        ]),
    )
}

#[test]
fn choice_decoder_accepts_unresolved_marker_and_exact_option_keys() {
    let body = br#"{
        "model":"jev-latest",
        "answers":{"decision":{
            "choice":"__unknown__",
            "probabilities":{"__unknown__":0.8,"candidate-a":0.2},
            "confidence":0.8
        }}
    }"#;

    let result = decode_choice_response(&request(), body).expect("valid choice");

    assert_eq!(result.choice, "__unknown__");
    assert_eq!(result.probabilities.len(), 2);
}

#[test]
fn choice_decoder_accepts_zero_probability_selected_option_without_threshold() {
    let body = br#"{
        "model":"jev-latest",
        "answers":{"decision":{
            "choice":"__unknown__",
            "probabilities":{"__unknown__":0.0,"candidate-a":1.0},
            "confidence":0.0
        }}
    }"#;

    let result = decode_choice_response(&request(), body).expect("valid zero probability choice");

    assert_eq!(result.choice, "__unknown__");
    assert_eq!(result.probabilities["__unknown__"], 0.0);
}

#[test]
fn choice_decoder_rejects_probability_key_drift() {
    let body = br#"{
        "model":"jev-latest",
        "answers":{"decision":{
            "choice":"candidate-a",
            "probabilities":{"candidate-a":1.0},
            "confidence":1.0
        }}
    }"#;

    let error = decode_choice_response(&request(), body).expect_err("missing option key");

    assert!(matches!(error, JudgmentError::InvalidResponse(_)));
}

#[test]
fn typesafe_debug_redacts_credential() {
    let config = TypeSafeConfig::new(
        "https://example.invalid/systemone",
        "jev-latest",
        Some("test-secret".to_string()),
    )
    .expect("config");

    assert!(!format!("{config:?}").contains("test-secret"));
}

#[cfg(unix)]
mod codex_process {
    use std::fs;
    use std::path::PathBuf;
    use std::sync::atomic::{AtomicU64, Ordering};

    use super::*;

    static TEST_COUNTER: AtomicU64 = AtomicU64::new(0);

    fn fake_codex(
        final_body: Option<&str>,
        stdout_body: Option<&str>,
        exit_code: i32,
    ) -> (PathBuf, PathBuf) {
        use std::os::unix::fs::PermissionsExt;

        let directory = std::env::temp_dir().join(format!(
            "satn-rs-judgment-test-{}-{}",
            std::process::id(),
            TEST_COUNTER.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&directory).expect("test directory");
        let script_path = directory.join("fake-codex");
        let write_final = final_body
            .map(|body| format!("printf '%s' '{}' > \"$output\"", body))
            .unwrap_or_else(|| "true".to_string());
        let write_stdout = stdout_body
            .map(|body| format!("printf '%s' '{}'", body))
            .unwrap_or_else(|| "true".to_string());
        let script = format!(
            "#!/bin/sh\nset -eu\noutput=''\nprevious=''\nfor arg in \"$@\"; do\n  if [ \"$previous\" = \"--output-last-message\" ]; then output=\"$arg\"; fi\n  previous=\"$arg\"\ndone\nscript_dir=$(CDPATH= cd -- \"$(dirname -- \"$0\")\" && pwd)\nprintf '%s\\n' \"$@\" > \"$script_dir/argv.txt\"\ncat > \"$script_dir/stdin.txt\"\nif [ {exit_code} -ne 0 ]; then exit {exit_code}; fi\n{write_final}\n{write_stdout}\n",
            exit_code = exit_code,
            write_final = write_final,
            write_stdout = write_stdout,
        );
        fs::write(&script_path, script).expect("fake executable");
        let mut permissions = fs::metadata(&script_path)
            .expect("fake metadata")
            .permissions();
        permissions.set_mode(0o755);
        fs::set_permissions(&script_path, permissions).expect("fake executable permissions");
        (directory, script_path)
    }

    #[test]
    fn codex_process_uses_literal_argv_stdin_and_final_message() {
        let (directory, executable) = fake_codex(
            Some(r#"{"proposal":{"kind":"select-alignment"}}"#),
            Some(r#"{"type":"thread.started"}"#),
            0,
        );
        let config = CodexConfig::new(&executable, "gpt-5.6-luna", "max").expect("config");

        let response = run_codex(&config, "frozen synthetic task").expect("final JSON");

        assert_eq!(response, json!({"proposal": {"kind": "select-alignment"}}));
        let argv = fs::read_to_string(directory.join("argv.txt")).expect("argv receipt");
        assert_eq!(
            argv.lines().collect::<Vec<_>>(),
            vec![
                "exec",
                "--model",
                "gpt-5.6-luna",
                "-c",
                "model_reasoning_effort=\"max\"",
                "--sandbox",
                "read-only",
                "--ephemeral",
                "--skip-git-repo-check",
                "--output-last-message",
                argv.lines().nth(10).expect("output path"),
                "--json",
                "-",
            ]
        );
        assert_eq!(
            fs::read_to_string(directory.join("stdin.txt")).expect("stdin receipt"),
            "frozen synthetic task"
        );
        fs::remove_dir_all(directory).expect("test cleanup");
    }

    #[test]
    fn codex_process_reports_nonzero_exit() {
        let (directory, executable) = fake_codex(None, None, 7);
        let config = CodexConfig::new(&executable, "gpt-5.6-luna", "max").expect("config");

        let error = run_codex(&config, "frozen synthetic task").expect_err("nonzero exit");

        assert_eq!(error, JudgmentError::ProcessExit(Some(7)));
        fs::remove_dir_all(directory).expect("test cleanup");
    }

    #[test]
    fn codex_process_rejects_invalid_final_json_and_does_not_use_stdout() {
        let (directory, executable) = fake_codex(None, Some(r#"{"proposal":{}}"#), 0);
        let config = CodexConfig::new(&executable, "gpt-5.6-luna", "max").expect("config");

        let error = run_codex(&config, "frozen synthetic task").expect_err("missing final");

        assert_eq!(
            error,
            JudgmentError::FinalResponse("final message is missing".to_string())
        );
        fs::remove_dir_all(directory).expect("test cleanup");
    }

    #[test]
    fn codex_process_reports_malformed_final_json() {
        let (directory, executable) = fake_codex(Some("not-json"), None, 0);
        let config = CodexConfig::new(&executable, "gpt-5.6-luna", "max").expect("config");

        let error = run_codex(&config, "frozen synthetic task").expect_err("malformed final");

        assert_eq!(
            error,
            JudgmentError::FinalResponse("final message is not JSON".to_string())
        );
        fs::remove_dir_all(directory).expect("test cleanup");
    }
}
