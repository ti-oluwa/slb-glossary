# Saving, Output and Config Files

This page covers what happens to a result after it's found. Printing it, saving it, and setting defaults so you're not retyping the same flags on every command.

---

## Saving results: `--save`

Every lookup command (`search`, `define`, `compare`, `related`, `terms`, `random`, `topics`, `urls list`, `local export`) accepts `--save`/`-o`:

```bash
slb search "gas lift" --save gas_lift.json
```

The file format is chosen from the extension you give; `.json`, `.csv`, or `.xlsx` (the last needs the `xlsx` extra installed, since it depends on `openpyxl`). Pass `--format` to override the format independently of the extension, e.g. to save a file named `results.txt` as CSV anyway:

```bash
slb search "gas lift" --save results.txt --format csv
```

`--save` is repeatable, so you can write the same results to more than one file/format in a single run:

```bash
slb search "gas lift" --save gas_lift.json --save gas_lift.csv
```

By default the results are still printed to the terminal *as well as* saved. Add `-q`/`--quiet` if you only want the file:

```bash
slb search "gas lift" --save gas_lift.json --quiet
```

---

## Shaping the table

`search`, `compare`, and a few other commands share a set of `--show-*`/`--hide-*` toggles that control which columns appear (covered per-command on the [previous page](searching.md)), plus `--json` to print the same result set as JSON to stdout instead of a table, useful for piping into another tool without writing a file at all:

```bash
slb search porosity --json | jq '.[0].definition'
```

---

## The `config` command

Rather than retyping flags like `--browser-type`, `--db-path`, or `--headed` on every command, you can set them once in a config file. Every lookup command's `--config` flag (`default`/`none`/a path) controls whether, and where, that file is read from; `default` (the global config file) is what every command assumes if you do not say otherwise.

### Where it lives

```bash
slb config path
```

```text
/home/you/.config/slb-glossary/config.toml (does not exist yet)
```

### The interactive way

```bash
slb config
```

Run with no subcommand, `config` opens a guided wizard. Section by section, it shows you each setting's current value and lets you accept it or type a new one. This is the easiest way to set up a config file the first time.

### The scriptable way

```bash
slb config init                          # write a fresh, all-defaults file
slb config get session.headless          # print one setting
slb config set session.headless false    # change one setting and save
slb config show --format json            # print the full effective config
```

Settings are addressed with a dotted path. `session.*` for browser/session behavior (`headless`, `browser_type`, `timeout`, `retry.*`, ...), `local.*` for the database (`data_dir`, `db_filename`, `sync_max_age_days`, ...), `output.*` for display defaults (`default_format`, `show_topic`, ...). `config show` prints all three sections at once:

```bash
slb config set session.browser_type firefox
slb config set local.sync_max_age_days 3.5
slb config show --format yaml
```

!!! warning "`config show`'s TOML output can error on unset fields"
    `config show`'s documented default format is TOML, but as of this writing it can raise `Unable to convert an object of <class 'NoneType'> to a TOML item` when a setting is unset (`None`), since TOML has no native null value and the unset fields aren't stripped before serializing. `--format json` and `--format yaml` do not hit this, so prefer one of those explicitly until it's fixed.

!!! tip "Any flag you pass on the command line still wins"
    A config file only supplies *defaults*. Any option you give explicitly on a given command overrides the config file's value for that one run, so `slb search porosity --headed` runs headed even if `session.headless` is `true` in your config.

### Editing it by hand

```bash
slb config edit
```

Opens the file in `$EDITOR` (or `$VISUAL`), creating it with defaults first if it does not exist yet. TOML and YAML config files need the `config` extra installed (`uv add "slb-glossary[config]"`); JSON works with no extra at all.

### Using a project-specific config instead of the global one

```bash
slb search porosity --config ~/my-config.toml
slb search porosity --config none --headed    # ignore any config file, use built-in defaults only
```

---

## Environment variable overrides

Beyond the config file, a large number of individually tunable internals can be overridden with an environment variable, without touching a config file or passing a flag at all. Two of the more commonly needed ones:

```bash
export SLB_GLOSSARY_DATA_DIR=/mnt/shared/slb-glossary   # where the local database lives
export SLB_GLOSSARY_CONFIG_DIR=/etc/slb-glossary          # where the config file lives
export SLB_GLOSSARY_CLI_CACHE_BY_DEFAULT=false             # default every command to --no-cache
```

Every one of these follows the same `SLB_GLOSSARY_<NAME>` pattern, and each is documented next to the constant it overrides in [`slb_glossary.constants`](../api/library.md#slb_glossaryconstants). They're mainly useful for deployment environments (containers, CI) where setting an environment variable is easier than shipping a config file.

---

## Logging

Every command accepts `--log-level`, `--log-to`, and `--log-sink`, for seeing (or saving) what actually happened during a run beyond the command's own printed output:

```bash
slb search porosity --log-level debug --log-to run.log
```

`--log-to` accepts a file path, or the literal `stderr`/`stdout`. `--log-sink module:ClassName` points at your own sink class instead (see [Logging](../library/logging.md#sinks) for what a sink needs to implement), and takes priority over `--log-to` if both are given. These three flags are a wrapper over `slb_glossary.logging.configure_logging`; see [Logging](../library/logging.md) for the full library-side API, including routing different parts of the library to different sinks at once, which the CLI's flags do not expose.

---

## Where to go from here

For the same settings, but reached from Python instead of a config file or environment variable, see [Saving Results and Config Objects](../library/configuration.md). For every flag on every command in one place, see [CLI Commands](../api/cli.md).
