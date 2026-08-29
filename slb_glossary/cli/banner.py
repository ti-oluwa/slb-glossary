"""Banner for the `slb-glossary` CLI's `--help`/`--version` output."""

import os
import shutil
import sys

import click

__all__ = ["get_banner", "supports_color", "supports_unicode"]


SLB_BLOCK = (
    "███████╗██╗     ██████╗ \n"
    "██╔════╝██║     ██╔══██╗\n"
    "███████╗██║     ██████╔╝\n"
    "╚════██║██║     ██╔══██╗\n"
    "███████║███████╗██████╔╝\n"
    "╚══════╝╚══════╝╚═════╝ "
)
"""'SLB' in the `ansi_shadow` figlet font. Regenerate with `pyfiglet` if this ever needs to change."""

GLOSSARY_BLOCK = (
    " ██████╗ ██╗      ██████╗ ███████╗███████╗ █████╗ ██████╗ ██╗   ██╗\n"
    "██╔════╝ ██║     ██╔═══██╗██╔════╝██╔════╝██╔══██╗██╔══██╗╚██╗ ██╔╝\n"
    "██║  ███╗██║     ██║   ██║███████╗███████╗███████║██████╔╝ ╚████╔╝ \n"
    "██║   ██║██║     ██║   ██║╚════██║╚════██║██╔══██║██╔══██╗  ╚██╔╝  \n"
    "╚██████╔╝███████╗╚██████╔╝███████║███████║██║  ██║██║  ██║   ██║   \n"
    " ╚═════╝ ╚══════╝ ╚═════╝ ╚══════╝╚══════╝╚═╝  ╚═╝╚═╝  ╚═╝   ╚═╝   "
)
"""'GLOSSARY' in the same font as `SLB_BLOCK`, so the two stack as one consistent wordmark."""


def _clean_block(text: str) -> str:
    """Right-trim trailing whitespace figlet output tends to leave on each line."""
    return "\n".join(line.rstrip() for line in text.splitlines())


def _center_over(text: str, reference_width: int) -> str:
    """Indent every line of `text` so it's horizontally centered over something `reference_width` wide."""
    lines = text.splitlines()
    width = max((len(line) for line in lines), default=0)
    pad = " " * max(0, (reference_width - width) // 2)
    return "\n".join(pad + line for line in lines)


def supports_color() -> bool:
    """
    Whether the banner should include ANSI color.

    Off if `NO_COLOR` is set (https://no-color.org, any non-empty value)
    or stdout isn't a terminal, unless `FORCE_COLOR` is set, so a script
    piping this command's output, or a user who's globally disabled color,
    gets plain text rather than raw escape codes.
    """
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return sys.stdout.isatty()


def supports_unicode() -> bool:
    """
    Whether the banner should use box-drawing block characters.

    Checked against `sys.stdout`'s own encoding rather than assumed,
    since a non-UTF-8 locale (some CI runners, older Windows consoles,
    `LANG=C`) will otherwise render the block art as mangled `?`/`\\xXX`
    garbage instead of failing loudly.
    """
    encoding = getattr(sys.stdout, "encoding", None) or ""
    return "utf" in encoding.lower()


def get_banner(*, width: int | None = None) -> str:
    """
    Build the CLI banner, sized and styled for the current terminal.

    :param width: Terminal width to lay the banner out for. Defaults to
        `shutil.get_terminal_size()` (which itself falls back to 80 if
        the size can't be determined, e.g. output is piped/redirected).
        Exposed mainly for testing every width tier without needing to
        actually resize a terminal.
    :return: The banner, already ANSI-styled if `supports_color()` and
        ready to print as-is (via `click.echo`, a `click.HelpFormatter`,
        or plain `print`).
    """
    columns = width if width is not None else shutil.get_terminal_size(fallback=(80, 24)).columns
    color = supports_color()

    if supports_unicode():
        glossary_width = max(len(line) for line in GLOSSARY_BLOCK.splitlines())
        if columns >= glossary_width:
            slb = _center_over(_clean_block(SLB_BLOCK), glossary_width)
            glossary = _clean_block(GLOSSARY_BLOCK)
            if color:
                slb = click.style(slb, fg="cyan", bold=True)
                glossary = click.style(glossary, fg="cyan", bold=True)
            return f"{slb}\n\n{glossary}"

        tagline = "◆  SLB GLOSSARY  ◆"
    else:
        tagline = "[ SLB GLOSSARY ]"

    return click.style(tagline, fg="cyan", bold=True) if color else tagline
