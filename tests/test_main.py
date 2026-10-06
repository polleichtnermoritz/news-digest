from pathlib import Path

import pytest

from digest.main import _write_github_output, should_run_now


@pytest.mark.parametrize(
    ("vienna_hour", "delivery_hour", "force", "expected"),
    [
        (7, 7, False, True),
        (6, 7, False, False),
        (8, 7, False, False),
        (0, 7, True, True),  # --force always runs, regardless of hour
    ],
)
def test_should_run_now(vienna_hour: int, delivery_hour: int, force: bool, expected: bool) -> None:
    assert should_run_now(vienna_hour, delivery_hour, force) is expected


def test_write_github_output_appends_when_env_var_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_file = tmp_path / "github_output"
    output_file.write_text("existing=value\n")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))

    _write_github_output("ran", "true")

    assert output_file.read_text() == "existing=value\nran=true\n"


def test_write_github_output_noop_without_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)

    _write_github_output("ran", "true")  # must not raise
