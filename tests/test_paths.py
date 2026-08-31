"""
OS-appropriate data/config directory resolution and env-var overrides.
"""

import pathlib

import pytest

from slb_glossary import paths

pytestmark = pytest.mark.unit


class TestGetDataDir:
    def test_uses_platformdirs_default_when_env_var_unset(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ):
        """Without an override or env var, `get_data_dir` uses `platformdirs.user_data_dir`."""
        monkeypatch.delenv(paths.DATA_DIR_ENV_VAR, raising=False)
        fake_dir = tmp_path / "platformdirs-data"
        monkeypatch.setattr(paths.platformdirs, "user_data_dir", lambda *a, **k: str(fake_dir))
        assert paths.get_data_dir() == fake_dir
        assert fake_dir.is_dir()

    def test_env_var_overrides_default(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ):
        """`SLB_GLOSSARY_DATA_DIR` overrides the platformdirs default."""
        env_dir = tmp_path / "env-data"
        monkeypatch.setenv(paths.DATA_DIR_ENV_VAR, str(env_dir))
        assert paths.get_data_dir() == env_dir
        assert env_dir.is_dir()

    def test_override_argument_wins_over_everything(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ):
        """An explicit `override` argument wins over both the env var and the default."""
        monkeypatch.setenv(paths.DATA_DIR_ENV_VAR, str(tmp_path / "env-data"))
        override_dir = tmp_path / "override-data"
        assert paths.get_data_dir(override_dir) == override_dir
        assert override_dir.is_dir()


class TestGetConfigDir:
    def test_uses_platformdirs_default_when_env_var_unset(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ):
        """Without an override or env var, `get_config_dir` uses `platformdirs.user_config_dir`."""
        monkeypatch.delenv(paths.CONFIG_DIR_ENV_VAR, raising=False)
        fake_dir = tmp_path / "platformdirs-config"
        monkeypatch.setattr(paths.platformdirs, "user_config_dir", lambda *a, **k: str(fake_dir))
        assert paths.get_config_dir() == fake_dir
        assert fake_dir.is_dir()

    def test_env_var_overrides_default(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ):
        """`SLB_GLOSSARY_CONFIG_DIR` overrides the platformdirs default."""
        env_dir = tmp_path / "env-config"
        monkeypatch.setenv(paths.CONFIG_DIR_ENV_VAR, str(env_dir))
        assert paths.get_config_dir() == env_dir
        assert env_dir.is_dir()


class TestDefaultPaths:
    def test_default_db_path_is_under_data_dir(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ):
        """`default_db_path()` is `glossary.db` inside the resolved data dir."""
        monkeypatch.setenv(paths.DATA_DIR_ENV_VAR, str(tmp_path))
        assert paths.default_db_path() == tmp_path / "glossary.db"

    def test_default_metadata_path_is_under_data_dir(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ):
        """`default_metadata_path()` is `metadata.json` inside the resolved data dir."""
        monkeypatch.setenv(paths.DATA_DIR_ENV_VAR, str(tmp_path))
        assert paths.default_metadata_path() == tmp_path / "metadata.json"

    def test_default_config_path_is_under_config_dir(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ):
        """`default_config_path()` is `config.toml` inside the resolved config dir."""
        monkeypatch.setenv(paths.CONFIG_DIR_ENV_VAR, str(tmp_path))
        assert paths.default_config_path() == tmp_path / "config.toml"
