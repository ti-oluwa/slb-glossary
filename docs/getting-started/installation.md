# Installation

Audience: everyone. Every path through this documentation passes through this page once. If you only want the terminal tool, you can skip straight to [Installing the CLI](#installing-the-cli). If you're writing Python code, skip to [Installing the library](#installing-the-library). Either way, finish with [Installing the browser build](#installing-the-browser-build), since both need it.

---

## Requirements

- **Python 3.10 or newer.** `slb-glossary` uses modern typing syntax (`str | None`) throughout, which is why the floor is 3.10 rather than something older.
- **About 300MB of free disk space**, for the background browser build. See [below](#installing-the-browser-build) for exactly why.
- **A network connection**, at least the first time you look up any given term. After that, the [local cache](library-tutorial.md#caching-what-you-look-up-locally) can serve it without one.

No account, API key, or paid access to anything is needed. The glossary itself is free to browse.

---

## Installing the CLI

Any of the methods below give you two identical commands: `slb-glossary` and the shorter `slb`. Both run the exact same code, since `pyproject.toml` registers them as two names for one entry point. This documentation uses `slb` throughout, but reach for `slb-glossary` if `slb` happens to collide with something else already on your system.

=== "uv (recommended)"

    [uv](https://docs.astral.sh/uv/) installs `slb-glossary` into its own isolated tool environment, so its dependencies never leak into, or clash with, any other Python project or tool on your machine.

    ```bash
    uv tool install "slb-glossary[all]"
    ```

    Or skip installing anything and just try a command once:

    ```bash
    uvx slb-glossary search porosity
    ```

    `uvx` downloads the package into a temporary environment, runs the one command, and throws the environment away afterward. Handy for a one-off check; `uv tool install` is what you want for regular use, since it keeps the environment around.

=== "pipx"

    [pipx](https://pipx.pypa.io/) does the same isolated-install job as `uv tool install`, if you already have it set up and would rather not add uv as well.

    ```bash
    pipx install "slb-glossary[all]"
    ```

=== "One-line script (macOS/Linux/WSL)"

    Picks `uv` or `pipx` for you, installing `uv` first if you have neither. Useful for a fresh machine or a CI image where you don't want to think about which installer to reach for.

    ```bash
    curl -fsSL https://raw.githubusercontent.com/ti-oluwa/slb-glossary/main/scripts/install.sh | sh
    ```

    !!! tip "Read a script before piping it into `sh`"
        This is generally good practice for any `curl | sh` installer, not specific to this one. You can inspect the script first by fetching it without the pipe: `curl -fsSL https://raw.githubusercontent.com/ti-oluwa/slb-glossary/main/scripts/install.sh`.

=== "Windows (no WSL)"

    Install `uv` first, then use it to install the tool:

    ```powershell
    powershell -c "irm https://astral.sh/uv/install.ps1 | iex; uv tool install slb-glossary"
    ```

Once installed, jump to [Installing the browser build](#installing-the-browser-build). You do not also need [Installing the library](#installing-the-library) unless you're also writing Python code against it.

---

## Installing the library

If you're writing Python code rather than using a terminal command, add `slb-glossary` as a dependency of your own project.

=== "uv"

    ```bash
    uv add slb-glossary
    ```

=== "pip"

    ```bash
    pip install slb-glossary
    ```

=== "poetry"

    ```bash
    poetry add slb-glossary
    ```

### Choosing extras

The base install covers live search (`slb_glossary.live`) and local search (`slb_glossary.local`) with no extra dependencies beyond what the base install already brings in. A few optional extras unlock more, and you only need the ones you'll actually use:

| Extra | Unlocks | Install |
|---|---|---|
| *(none)* | Live and local search, `slb_glossary.query`, JSON config files. | `uv add slb-glossary` |
| `xlsx` | Saving results as `.xlsx`, and importing `.xlsx`/`.xlsm` files into the local database. | `uv add "slb-glossary[xlsx]"` |
| `config` | TOML and YAML config files, in addition to JSON. See [Configuration](../cli/configuration.md). | `uv add "slb-glossary[config]"` |
| `tui` | The interactive `--tui` mode available on every CLI command. | `uv add "slb-glossary[tui]"` |
| `mcp` | The MCP server (`slb mcp serve`). See [Connecting an AI agent](../agent/mcp-server.md). | `uv add "slb-glossary[mcp]"` |
| `semantic` | Semantic and hybrid search on the local database: matching a paraphrase, not just an exact word. See [Search modes](api-reference.md#search-modes). | `uv add "slb-glossary[semantic]"` |
| `all` | Every extra above, in one install. | `uv add "slb-glossary[all]"` |

!!! tip "Not sure yet? Install `all`"
    Each extra only adds a dependency or two; none of them are heavyweight on their own, and `slb-glossary[all]` is what the CLI installation methods above default to. Narrow it down later if you'd rather keep your own project's dependency list minimal.

### Fully typed

`slb-glossary` ships a `py.typed` marker ([PEP 561](https://peps.python.org/pep-0561/)), so type checkers and language servers such as `mypy`, `pyright`, or `ty` pick up its type annotations automatically. No separate stub package needed.

---

## Installing the browser build

**Whichever path above you took, this step is shared and required by both.** The glossary site is a JavaScript application, so `slb-glossary` doesn't just fetch a URL and parse HTML: it drives a real, invisible ("headless") browser to load the page the way a person's browser would, then reads the rendered result. That browser has to actually be downloaded once, and it's a real download of a browser engine, not a small package:

```bash
slb install chromium
```

Chromium is the browser family `slb_glossary.live.session()` uses by default, and the one this documentation's examples assume throughout. Firefox and WebKit builds are also available (`slb install firefox`, `slb install webkit`) if you need to compare behavior across engines, but there's no reason to install more than one unless you have a specific reason to.

This is a one-time step per machine. It downloads to Playwright's own cache directory, not anywhere inside `slb-glossary` itself, so reinstalling or upgrading `slb-glossary` later does not require running it again.

!!! warning "Slow connection? The download can time out"
    `install` takes two flags to make it more forgiving of a slow or flaky connection:

    ```bash
    slb install chromium --timeout 120000   # allow 2 minutes per download step, instead of the ~30s default
    slb install chromium --retries 5        # retry a failed download step more times, with backoff
    ```

    `--timeout` is in milliseconds, matching every other timeout value across this project's CLI and library API.

---

## Check it worked

If you installed the CLI:

```bash
slb --help
```

You should see a banner and a list of commands: `search`, `define`, `terms`, `random`, `sync`, `install`, and (if you installed the `mcp` extra) `mcp`.

If you installed the library:

```bash
python -c "import slb_glossary; print(slb_glossary.__version__)"
```

This should print a version number with no error. If either of these fails, the [FAQ](../faq.md) covers the most common causes; if the browser step itself is the one that's failing, start with [Why is the first search slow, or the install step failing?](../faq.md#why-is-the-first-search-slow).

---

## Next steps

<div class="grid cards" markdown>

- :material-console: **Using the CLI**

    ---

    Your first search, defining an exact term, and working offline once you've cached something.

    [Using the CLI](../cli/index.md){ .md-button }

- :material-language-python: **Using the library**

    ---

    The same capabilities, called directly from your own async Python code.

    [Using the library](../library/index.md){ .md-button }

</div>
