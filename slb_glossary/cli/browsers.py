"""Manage the browser engines patchright launches, via patchright's driver CLI."""

import dataclasses
import logging
import os
import pathlib
import shutil
import subprocess
import sys
import time
import typing

from slb_glossary.live.types import BrowserType
from slb_glossary.retries import RetryPolicy

logger = logging.getLogger(__name__)


__all__ = [
    "KNOWN_BROWSERS",
    "BrowserInstallError",
    "InstalledBrowser",
    "install_browsers",
    "list_installed_browsers",
    "remove_browsers",
]


class BrowserInstallError(RuntimeError):
    """Raised when a patchright driver invocation (install/uninstall) fails."""


KNOWN_BROWSERS: tuple[str, ...] = tuple(browser_type for browser_type in BrowserType)
"""Browser families patchright's driver knows how to install and is supported by `slb_glossary`."""

DEFAULT_INSTALL_RETRY_POLICY = RetryPolicy.exponential(base_delay=2.0, attempts=3, max_delay=15.0)
"""
Default retry policy for a browser download that times out or drops.
Allows a handful of attempts with a short exponential backoff, since a slow or
flaky connection is often fine a few seconds later.
"""


@dataclasses.dataclass(slots=True, kw_only=True, frozen=True)
class InstalledBrowser:
    """One browser build found on disk in patchright's browser cache."""

    name: str
    """Directory name patchright installed the build under, e.g. `"chromium-1148"`."""

    family: str
    """Browser family the build belongs to, e.g. `"chromium"`."""

    path: pathlib.Path
    """Path to the build's directory."""

    size_bytes: int
    """Total on-disk size of the build's directory."""


def get_browsers_path() -> pathlib.Path:
    """
    Return the directory patchright installs (and looks up) browser builds in.

    Respects the `PLAYWRIGHT_BROWSERS_PATH` environment variable, since
    patchright's driver is a Playwright fork and honors the same variable.
    Falls back to the per-OS default Playwright/patchright cache directory.

    :return: The browsers cache directory. It may not exist yet if no
        browser has ever been installed.
    """
    override = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if override:
        return pathlib.Path(override).expanduser()

    if sys.platform == "win32":
        base = pathlib.Path(os.environ.get("LOCALAPPDATA", "~")).expanduser()
        return base / "ms-playwright"
    if sys.platform == "darwin":
        return pathlib.Path("~/Library/Caches/ms-playwright").expanduser()
    return pathlib.Path("~/.cache/ms-playwright").expanduser()


def get_directory_size(path: pathlib.Path) -> int:
    """Return the total size in bytes of every regular file under `path`."""
    total = 0
    for entry in path.rglob("*"):
        if entry.is_file():
            total += entry.stat().st_size
    return total


def run_driver(
    args: typing.Sequence[str],
    *,
    env_overrides: typing.Mapping[str, str] | None = None,
    retry: RetryPolicy | None = None,
) -> None:
    """
    Run `python -m patchright <args>`, streaming its output live.

    :param args: Arguments to pass after `patchright` on the command line,
        e.g. `["install", "chromium", "--with-deps"]`.
    :param env_overrides: Extra environment variables for the subprocess,
        layered on top of the current environment - e.g.
        `PLAYWRIGHT_DOWNLOAD_CONNECTION_TIMEOUT`/`PLAYWRIGHT_DOWNLOAD_HOST`,
        which is how patchright's driver actually takes a download timeout
        and an alternate CDN host; neither is a real CLI flag on `install`.
    :param retry: If given, retry a failing run per this policy (a fresh
        subprocess each attempt) instead of raising on the first failure.
        `None` (the default) tries exactly once, matching the previous
        behavior of this function.
    :raises BrowserInstallError: If patchright is not importable, or every
        attempt's driver process exits with a non-zero status.
    """
    try:
        import patchright  # noqa: F401
    except ImportError as exc:
        raise BrowserInstallError(
            "`patchright` is not installed. Install it with `pip install patchright` "
            "or `pip install slb-glossary` (it's a core dependency of the library)."
        ) from exc

    command = [sys.executable, "-m", "patchright", *args]
    env = {**os.environ, **env_overrides} if env_overrides else None
    if env_overrides:
        logger.debug("Running with extra env: %s", ", ".join(sorted(env_overrides)))

    policy = retry or RetryPolicy(attempts=1)
    last_error: BrowserInstallError | None = None
    for attempt in range(1, policy.attempts + 1):
        logger.debug("Running (attempt %d/%d): %s", attempt, policy.attempts, " ".join(command))
        try:
            result = subprocess.run(command, check=False, env=env)
        except OSError as exc:
            last_error = BrowserInstallError(f"Could not run the patchright driver: {exc}")
        else:
            if result.returncode == 0:
                return
            last_error = BrowserInstallError(
                f"`{' '.join(command)}` exited with status {result.returncode}."
            )

        if attempt < policy.attempts:
            delay = policy.delay_for_attempt(attempt)
            logger.warning(
                "Attempt %d/%d failed (%s); retrying in %.1fs",
                attempt,
                policy.attempts,
                last_error,
                delay,
            )
            time.sleep(delay)

    assert last_error is not None
    raise last_error


def install_browsers(
    browsers: typing.Sequence[str],
    *,
    with_deps: bool = False,
    force: bool = False,
    only_shell: bool = False,
    timeout_ms: int | None = None,
    download_host: str | None = None,
    retry: RetryPolicy | None = None,
) -> None:
    """
    Install one or more browser engines via patchright's driver.

    Browser builds are large (100-400MB) downloads from a single CDN, so on
    a slow or congested connection, the default ~30s-per-request timeout
    patchright/Playwright ships with can trip before a build finishes -
    this shows up as the install just failing partway through with a
    timeout error and nothing installed. `timeout_ms`/`download_host`/`retry`
    exist to make that recoverable without editing your shell's environment
    by hand.

    :param browsers: Browser family names to install, e.g. `["chromium"]`.
        An empty sequence installs patchright's default set of browsers.
    :param with_deps: Also install the OS-level packages the browsers need
        to run (Linux only). Requires root/sudo privileges on most systems.
    :param force: Reinstall even if the browser is already present.
    :param only_shell: For Chromium, install the headless-shell build
        instead of the full browser. Ignored for other browsers.
    :param timeout_ms: Milliseconds to wait per download before giving up,
        sets `PLAYWRIGHT_DOWNLOAD_CONNECTION_TIMEOUT` for this run only.
        Raise this (e.g. to `120_000`) if installs keep timing out on a
        slow connection. `None` leaves patchright's own default in place.
    :param download_host: Alternate host to download browser builds from,
        sets `PLAYWRIGHT_DOWNLOAD_HOST` for this run only. Useful if the
        default CDN is slow or unreachable from your network - point this
        at a mirror, or a caching proxy you control.
    :param retry: Retry policy for a failed download (see
        `DEFAULT_INSTALL_RETRY_POLICY`). Pass `None` to try exactly once
        (the previous behavior), or `RetryPolicy(attempts=1)` explicitly.
    :raises BrowserInstallError: If the install fails for any reason,
        including patchright not being installed.
    """
    args = ["install", *browsers]
    if with_deps:
        args.append("--with-deps")
    if force:
        args.append("--force")
    if only_shell:
        args.append("--only-shell")

    env_overrides: dict[str, str] = {}
    if timeout_ms is not None:
        env_overrides["PLAYWRIGHT_DOWNLOAD_CONNECTION_TIMEOUT"] = str(timeout_ms)
    if download_host:
        env_overrides["PLAYWRIGHT_DOWNLOAD_HOST"] = download_host

    run_driver(
        args,
        env_overrides=env_overrides or None,
        retry=retry if retry is not None else DEFAULT_INSTALL_RETRY_POLICY,
    )


def remove_browsers(browsers: typing.Sequence[str]) -> list[str]:
    """
    Delete installed browser builds matching the given families from disk.

    patchright's driver has no per-browser uninstall subcommand, so this
    removes matching build directories directly from the browsers cache
    instead of shelling out to the driver.

    :param browsers: Browser family names to remove, e.g. `["firefox"]`.
        Matches by directory name prefix (`"firefox-1234"` matches
        `"firefox"`), so every installed build of a family is removed.
    :return: Names of the build directories that were actually removed.
        Empty if none of `browsers` were installed.
    :raises BrowserInstallError: If a matching directory could not be
        removed, e.g. due to a permissions error.
    """
    if not browsers:
        return []

    base = get_browsers_path()
    if not base.is_dir():
        return []

    wanted = {family.lower() for family in browsers}
    removed: list[str] = []
    for entry in sorted(base.iterdir()):
        if not entry.is_dir():
            continue
        family = entry.name.split("-", 1)[0].lower()
        # patchright also ships a "-headless-shell" suffix variant for chromium.
        family = family.removesuffix("_headless_shell")
        if family not in wanted and not any(entry.name.startswith(f"{w}") for w in wanted):
            continue
        try:
            shutil.rmtree(entry)
        except OSError as exc:
            raise BrowserInstallError(f"Could not remove {entry}: {exc}") from exc
        removed.append(entry.name)
        logger.info("Removed browser build %s", entry.name)

    return removed


def list_installed_browsers(
    browsers: typing.Sequence[str] | None = None,
) -> list[InstalledBrowser]:
    """
    List browser builds currently installed in patchright's browsers cache.

    :param browsers: If given, only list builds belonging to these
        families, e.g. `["chromium", "webkit"]`. Lists every installed
        build if `None` or empty.
    :return: Installed builds, sorted by family then build name. Empty if
        the browsers cache does not exist yet (nothing has been installed).
    """
    base = get_browsers_path()
    if not base.is_dir():
        return []

    wanted = {family.lower() for family in browsers} if browsers else None
    found: list[InstalledBrowser] = []
    for entry in sorted(base.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        family = entry.name.split("-", 1)[0].lower()
        if family not in KNOWN_BROWSERS:
            continue
        if wanted is not None and family not in wanted:
            continue
        found.append(
            InstalledBrowser(
                name=entry.name,
                family=family,
                path=entry,
                size_bytes=get_directory_size(entry),
            )
        )

    return sorted(found, key=lambda browser: (browser.family, browser.name))
