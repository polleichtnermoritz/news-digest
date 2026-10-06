from pathlib import Path

import pytest

from digest.main import _write_github_output, run_live, should_run_now


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


class _RecordingNotifier:
    sent: list[str] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def send(self, text: str) -> None:
        _RecordingNotifier.sent.append(text)


class _BrokenNotifier:
    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def send(self, text: str) -> None:
        raise ConnectionError("telegram is down too")


async def test_run_live_sends_error_notification_and_reraises_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom() -> None:
        raise RuntimeError("fetch exploded")

    monkeypatch.setattr("digest.main.load_sources", boom)
    _RecordingNotifier.sent = []
    monkeypatch.setattr("digest.main.TelegramNotifier", _RecordingNotifier)

    with pytest.raises(RuntimeError, match="fetch exploded"):
        await run_live(force=True)

    assert len(_RecordingNotifier.sent) == 1
    assert "fetch exploded" in _RecordingNotifier.sent[0]
    assert _RecordingNotifier.sent[0].startswith("⚠️")


async def test_run_live_still_raises_original_error_if_notification_also_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom() -> None:
        raise RuntimeError("original failure")

    monkeypatch.setattr("digest.main.load_sources", boom)
    monkeypatch.setattr("digest.main.TelegramNotifier", _BrokenNotifier)

    with pytest.raises(RuntimeError, match="original failure"):
        await run_live(force=True)
