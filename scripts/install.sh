#!/bin/sh
# Install slb-glossary as a standalone CLI tool (`slb-glossary` and its `slb` alias).
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/ti-oluwa/slb-glossary/main/scripts/install.sh | sh
#
# Prefers, in order: an existing `uv`, an existing `pipx`, then installs `uv`
# (via astral.sh's own installer) and uses that. This is a POSIX `sh` script
# for macOS, Linux, and WSL; Windows users without WSL should use uv's native
# PowerShell installer instead (see the README).
#
# Environment variables:
#   SLB_GLOSSARY_VERSION   Install a specific version, e.g. "0.2.0" (default: latest)
#   SLB_GLOSSARY_INSTALLER Force "uv" or "pipx" instead of auto-detecting

set -eu

PACKAGE="slb-glossary"
VERSION="${SLB_GLOSSARY_VERSION:-}"
FORCE_INSTALLER="${SLB_GLOSSARY_INSTALLER:-}"

# Composes "slb-glossary" or "slb-glossary==X.Y.Z" for the installer to consume.
package_spec() {
    if [ -n "$VERSION" ]; then
        printf '%s==%s' "$PACKAGE" "$VERSION"
    else
        printf '%s' "$PACKAGE"
    fi
}

command_exists() {
    command -v "$1" >/dev/null 2>&1
}

info() {
    printf '\033[1;34m==>\033[0m %s\n' "$1"
}

warn() {
    printf '\033[1;33mwarning:\033[0m %s\n' "$1" >&2
}

fail() {
    printf '\033[1;31merror:\033[0m %s\n' "$1" >&2
    exit 1
}

# Windows only gets here via a POSIX shell (WSL, Git Bash, MSYS/Cygwin);
# native `cmd.exe`/PowerShell can't run this script at all, so this is
# purely a friendly nudge toward the documented PowerShell path.
case "$(uname -s 2>/dev/null || echo unknown)" in
    MINGW* | MSYS* | CYGWIN*)
        warn "Detected a Windows POSIX shell (Git Bash/MSYS/Cygwin)."
        warn "This should work, but native PowerShell users should instead run:"
        warn '  powershell -c "irm https://astral.sh/uv/install.ps1 | iex; uv tool install slb-glossary"'
        ;;
esac

install_with_uv() {
    info "Installing $PACKAGE with uv..."
    uv tool install "$(package_spec)"
}

install_with_pipx() {
    info "Installing $PACKAGE with pipx..."
    pipx install "$(package_spec)"
}

bootstrap_uv() {
    info "uv not found; installing it first (via astral.sh)..."
    if command_exists curl; then
        curl -LsSf https://astral.sh/uv/install.sh | sh
    elif command_exists wget; then
        wget -qO- https://astral.sh/uv/install.sh | sh
    else
        fail "Neither curl nor wget is available to install uv. Install one and re-run this script."
    fi

    # uv's installer places the binary in ~/.local/bin (or ~/.cargo/bin on
    # older installers); add both to PATH for the rest of this run so we
    # don't have to ask the user to open a new shell just to finish installing.
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

    if ! command_exists uv; then
        fail "uv installation finished but 'uv' is still not on PATH. Open a new shell and re-run this script."
    fi
}

main() {
    case "$FORCE_INSTALLER" in
        uv)
            command_exists uv || bootstrap_uv
            install_with_uv
            ;;
        pipx)
            command_exists pipx || fail "SLB_GLOSSARY_INSTALLER=pipx was set, but 'pipx' is not on PATH."
            install_with_pipx
            ;;
        "")
            if command_exists uv; then
                install_with_uv
            elif command_exists pipx; then
                install_with_pipx
            elif command_exists curl || command_exists wget; then
                bootstrap_uv
                install_with_uv
            else
                fail "No suitable installer found (uv, pipx) and no curl/wget to bootstrap one. Install Python and pipx, or uv, then re-run."
            fi
            ;;
        *)
            fail "Unknown SLB_GLOSSARY_INSTALLER='$FORCE_INSTALLER' (expected 'uv' or 'pipx')."
            ;;
    esac

    echo ""
    info "Installed! Run 'slb-glossary --help' (or the shorter 'slb --help') to get started."
    info "First run needs a browser engine too. Run 'slb-glossary install' to install one (or see 'slb-glossary install --help' for more options)."
    echo ""
    echo "If 'slb-glossary'/'slb' aren't found, open a new shell (or add ~/.local/bin to PATH) and try again."
}

main "$@"
