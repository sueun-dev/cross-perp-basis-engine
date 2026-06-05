import importlib
import os

import env_loader


def _fresh_loader():
    # env_loader caches "already loaded" in a module global; reload for isolation.
    return importlib.reload(env_loader)


def test_loads_keys_handles_export_quotes_and_comments(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "# a comment",
                "",
                "PLAIN=value1",
                'QUOTED="value 2"',
                "SINGLE='value3'",
                "export EXPORTED=value4",
                "WITH_EQUALS=a=b=c",
            ]
        ),
        encoding="utf-8",
    )
    for key in ("PLAIN", "QUOTED", "SINGLE", "EXPORTED", "WITH_EQUALS"):
        monkeypatch.delenv(key, raising=False)

    loader = _fresh_loader()
    loader.load_env(str(env_file))

    assert os.environ["PLAIN"] == "value1"
    assert os.environ["QUOTED"] == "value 2"
    assert os.environ["SINGLE"] == "value3"
    assert os.environ["EXPORTED"] == "value4"
    assert os.environ["WITH_EQUALS"] == "a=b=c"


def test_does_not_override_existing_env(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("ALREADY_SET=from_file\n", encoding="utf-8")
    monkeypatch.setenv("ALREADY_SET", "from_environment")

    loader = _fresh_loader()
    loader.load_env(str(env_file))

    assert os.environ["ALREADY_SET"] == "from_environment"


def test_missing_file_is_noop(tmp_path):
    loader = _fresh_loader()
    # Should not raise even when the file does not exist.
    loader.load_env(str(tmp_path / "does-not-exist.env"))
