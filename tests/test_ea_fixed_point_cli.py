from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

from satn.cli import app


def test_convergence_cli_exits_zero_for_equality_and_forwards_resume(
    monkeypatch,
    tmp_path: Path,
) -> None:
    calls: list[dict[str, object]] = []

    def run(config: Path, **kwargs: object) -> SimpleNamespace:
        calls.append({"config": config, **kwargs})
        return SimpleNamespace(
            status="converged",
            iterations=(object(), object()),
            record_path=tmp_path / "record.json",
        )

    monkeypatch.setattr("satn.cli.run_ea_fixed_point_convergence", run)

    result = CliRunner().invoke(
        app,
        [
            "converge-ea-elevation",
            str(tmp_path / "area.yaml"),
            "--max-iterations",
            "3",
            "--record",
            str(tmp_path / "record.json"),
            "--resume",
        ],
    )

    assert result.exit_code == 0
    assert result.stdout.startswith("converged: 2 compile comparison(s)")
    assert calls == [
        {
            "config": tmp_path / "area.yaml",
            "max_iterations": 3,
            "record_path": tmp_path / "record.json",
            "resume": True,
        }
    ]


def test_convergence_cli_exits_two_for_bounded_non_convergence(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "satn.cli.run_ea_fixed_point_convergence",
        lambda *_args, **_kwargs: SimpleNamespace(
            status="non-converged",
            iterations=(object(),),
            record_path=tmp_path / "record.json",
        ),
    )

    result = CliRunner().invoke(
        app,
        ["converge-ea-elevation", str(tmp_path / "area.yaml")],
    )

    assert result.exit_code == 2
    assert result.stdout.startswith("non-converged: 1 compile comparison(s)")


def test_convergence_cli_exits_one_when_governed_work_fails(
    monkeypatch,
    tmp_path: Path,
) -> None:
    def fail(*_args: object, **_kwargs: object) -> None:
        raise ValueError("invalid governed evidence")

    monkeypatch.setattr("satn.cli.run_ea_fixed_point_convergence", fail)

    result = CliRunner().invoke(
        app,
        ["converge-ea-elevation", str(tmp_path / "area.yaml")],
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, ValueError)


def test_recovery_candidate_cli_emits_governed_candidate(
    monkeypatch,
    tmp_path: Path,
) -> None:
    candidate = tmp_path / ".satn-ea-fixed-point-candidates" / "weca"
    monkeypatch.setattr(
        "satn.cli.compile_ea_recovery_candidate",
        lambda config: candidate if config == tmp_path / "area.yaml" else None,
    )

    result = CliRunner().invoke(
        app,
        ["compile-ea-recovery-candidate", str(tmp_path / "area.yaml")],
    )

    assert result.exit_code == 0
    assert result.stdout.strip() == str(candidate)
