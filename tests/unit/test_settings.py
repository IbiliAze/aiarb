from pathlib import Path

import pytest

from aiarb.config.settings import Settings

KEYS = ("ANTHROPIC_API_KEY", "TYPESAFE_API_KEY")


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in KEYS:
        monkeypatch.delenv(key, raising=False)


def test_no_keys_is_valid() -> None:
    settings = Settings()

    assert settings.anthropic_api_key is None
    assert settings.typesafe_api_key is None


def test_keys_are_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-test")

    settings = Settings()

    assert settings.anthropic_api_key is not None
    assert settings.anthropic_api_key.get_secret_value() == "sk-ant-test"
    assert settings.typesafe_api_key is not None
    assert settings.typesafe_api_key.get_secret_value() == "ts-test"


@pytest.mark.parametrize("value", ["", "   ", "\n"])
def test_a_blank_key_counts_as_unset(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", value)

    assert Settings().anthropic_api_key is None


def test_whitespace_around_a_key_is_removed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", "  ts-test\n")

    key = Settings().typesafe_api_key

    assert key is not None
    assert key.get_secret_value() == "ts-test"


def test_keys_never_appear_when_settings_are_printed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret")
    settings = Settings()

    assert "sk-ant-secret" not in repr(settings)
    assert "sk-ant-secret" not in str(settings)
    assert "sk-ant-secret" not in settings.model_dump_json()


def test_a_dotenv_file_in_the_working_directory_is_ignored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=the-target-apps-own-key\n")
    monkeypatch.chdir(tmp_path)

    assert Settings().anthropic_api_key is None
