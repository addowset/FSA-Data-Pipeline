import os

from fsa_pipeline.config import load_dotenv


def test_load_dotenv_sets_new_variable(tmp_path, monkeypatch):
    monkeypatch.delenv("TEST_DOTENV_VAR", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("TEST_DOTENV_VAR=hello\n", encoding="utf-8")

    load_dotenv(env_file)

    assert os.environ["TEST_DOTENV_VAR"] == "hello"


def test_load_dotenv_does_not_override_real_env_var(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_DOTENV_VAR", "real-value")
    env_file = tmp_path / ".env"
    env_file.write_text("TEST_DOTENV_VAR=from-file\n", encoding="utf-8")

    load_dotenv(env_file)

    assert os.environ["TEST_DOTENV_VAR"] == "real-value"


def test_load_dotenv_ignores_comments_and_blank_lines(tmp_path, monkeypatch):
    monkeypatch.delenv("TEST_DOTENV_VAR", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("# a comment\n\nTEST_DOTENV_VAR=value\n", encoding="utf-8")

    load_dotenv(env_file)

    assert os.environ["TEST_DOTENV_VAR"] == "value"


def test_load_dotenv_missing_file_is_a_noop(tmp_path):
    load_dotenv(tmp_path / "does-not-exist.env")  # should not raise
