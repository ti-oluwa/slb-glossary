"""
Platform-appropriate filesystem locations for the application's local data.

**Disclaimer**: the SLB Energy Glossary's content and data are
owned by SLB. Anything cached locally via the paths in this module; the local
search database (`slb_glossary.local`), its metadata, and the default
config file (`slb_glossary.config`), is a local copy the user has chosen
to keep on their own machine. The user is solely responsible for managing
that data's lifecycle (retention, refreshing, and deletion) in compliance
with SLB's terms of use <https://www.slb.com/en/terms-of-service>.
"""

import os
import pathlib

import platformdirs

__all__ = [
    "APP_AUTHOR",
    "APP_NAME",
    "CONFIG_DIR_ENV_VAR",
    "DATA_DIR_ENV_VAR",
    "default_config_path",
    "default_db_path",
    "default_metadata_path",
    "get_config_dir",
    "get_data_dir",
]

APP_NAME = "slb-glossary"
"""Application name passed to `platformdirs` when resolving OS directories."""

APP_AUTHOR = "ti-oluwa"
"""Application author passed to `platformdirs` (only used on Windows)."""

DATA_DIR_ENV_VAR = "SLB_GLOSSARY_DATA_DIR"
"""Environment variable that overrides the resolved local data directory."""

CONFIG_DIR_ENV_VAR = "SLB_GLOSSARY_CONFIG_DIR"
"""Environment variable that overrides the resolved config directory."""


def get_data_dir(override: str | pathlib.Path | None = None) -> pathlib.Path:
    """
    Resolve the directory the application stores local data in,
    creating it if needed.

    This is where `slb_glossary.local` keeps its SQLite database and
    `metadata.json`. Resolution order: `override` if given, then the
    `SLB_GLOSSARY_DATA_DIR` environment variable, then the OS-appropriate
    user data directory (e.g. `~/.local/share/slb-glossary` on Linux,
    `~/Library/Application Support/slb-glossary` on macOS,
    `%LOCALAPPDATA%\\slb-glossary` on Windows).

    :param override: A directory to use instead of any environment
        variable or OS default, e.g. `Config.local.data_dir` or a
        user-supplied `--data-dir` CLI option.
    :return: The resolved data directory. Created (including parents) if
        it did not already exist.
    """
    if override is not None:
        resolved = pathlib.Path(override).expanduser()
    elif DATA_DIR_ENV_VAR in os.environ:
        resolved = pathlib.Path(os.environ[DATA_DIR_ENV_VAR]).expanduser()
    else:
        resolved = pathlib.Path(platformdirs.user_data_dir(APP_NAME, APP_AUTHOR))
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def get_config_dir(override: str | pathlib.Path | None = None) -> pathlib.Path:
    """
    Resolve the directory the application looks for a config file in,
    creating it if needed.

    Same resolution order as `get_data_dir`, but for the OS-appropriate
    user *config* directory (e.g. `~/.config/slb-glossary` on Linux).

    :param override: A directory to use instead of any environment
        variable or OS default.
    :return: The resolved config directory. Created (including parents) if
        it did not already exist.
    """
    if override is not None:
        resolved = pathlib.Path(override).expanduser()
    elif CONFIG_DIR_ENV_VAR in os.environ:
        resolved = pathlib.Path(os.environ[CONFIG_DIR_ENV_VAR]).expanduser()
    else:
        resolved = pathlib.Path(platformdirs.user_config_dir(APP_NAME, APP_AUTHOR))
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def default_config_path() -> pathlib.Path:
    """Return the default path the application looks for a config file at."""
    return get_config_dir() / "config.toml"


def default_db_path() -> pathlib.Path:
    """Return the default path the application stores its local search database at."""
    return get_data_dir() / "glossary.db"


def default_metadata_path() -> pathlib.Path:
    """Return the default path the application stores local database metadata at."""
    return get_data_dir() / "metadata.json"
