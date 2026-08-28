"""Lightweight internet-connectivity check utilities."""

import asyncio
import logging
import time

from slb_glossary.constants import constants

logger = logging.getLogger(__name__)

__all__ = ["has_internet_connection"]


PROBE_TARGETS: tuple[tuple[str, int], ...] = (
    ("1.1.1.1", 53),  # Cloudflare public DNS
    ("8.8.8.8", 53),  # Google public DNS
    ("9.9.9.9", 53),  # Quad9 public DNS
)
"""
Well-known, highly-available public DNS resolvers, probed by raw IP:port
TCP connect. No DNS lookup needed (so a broken *resolver* doesn't read
as "no internet"), and no HTTP/TLS handshake, just a bare TCP SYN/ACK.

About as cheap and dependency-free as an internet-reachability check
gets. Several are tried, concurrently, so one being blocked, firewalled,
or briefly down doesn't read as "no internet" on its own.
"""

_CACHE: tuple[float, bool] | None = None
"""`(checked_at, result)` from the last real probe. `None` until the first check."""


async def probe(host: str, port: int, timeout: float) -> bool:
    """
    Try one raw TCP connect to `host:port`.

    :param host: IP address to connect to (no DNS lookup performed).
    :param port: Port to connect to.
    :param timeout: Seconds to wait for the connection before giving up.
    :return: `True` if the connection succeeded, `False` on any failure
        or timeout.
    """
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
    except Exception:
        return False
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass
    return True


async def has_internet_connection(*, use_cache: bool = True) -> bool:
    """
    Check whether this machine currently has outbound internet connectivity.

    Probes every `PROBE_TARGETS` entry concurrently, each capped at
    `constants.internet_check_timeout` seconds, and returns `True` as
    soon as any one succeeds.

    Only when every target fails (each having run for the full timeout)
    is the result `False`; and total wall time in that case is still just
    `constants.internet_check_timeout`, not the sum of all attempts.

    :param use_cache: If `True` (the default), reuse a result from the
        last `constants.internet_check_cache_ttl` seconds instead of
        probing again. Pass `False` to force a fresh check regardless
        of any cached result.
    :return: `True` if at least one probe target was reachable.
    """
    global _CACHE
    if use_cache and _CACHE is not None:
        checked_at, result = _CACHE
        if time.monotonic() - checked_at < constants.internet_check_cache_ttl:
            return result

    timeout = constants.internet_check_timeout
    results = await asyncio.gather(
        *(probe(host, port, timeout) for host, port in PROBE_TARGETS),
        return_exceptions=True,
    )
    connected = any(result is True for result in results)
    if not connected:
        logger.debug(
            "No internet connectivity detected (tried %d target(s), %.1fs timeout each)",
            len(PROBE_TARGETS),
            timeout,
        )
    _CACHE = (time.monotonic(), connected)
    return connected
