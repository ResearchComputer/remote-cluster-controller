# rcc — Remote Cluster Controller

**Status:** draft
**Date:** 2026-04-23
**Owner:** askfermi@gmail.com

## Summary

`rcc` is a Python CLI that generalizes two existing bash scripts (`run.sh`, `sync.sh`) into a reusable, PyPI-distributed package for managing remote HPC/cluster workflows. It provides project-local configuration (`.rcc/`), file sync (`push`/`pull`), remote command execution (`run`), and connection management, all driven by named profiles with per-command overrides.

## Goals

- Preserve the exact workflow the existing bash scripts enable (clariden push/pull/run with SSH ControlMaster multiplexing) while making it reusable across projects and hosts.
- Profile-based configuration: one `.rcc/config.toml` per project defines one or more remote targets; a default profile is used unless overridden.
- Per-command overrides for `--host` and `--remote-dir` so callers keep the env-var-style flexibility of the current scripts.
- Distributable via PyPI; installable with `uv tool install rcc` or `pipx install rcc`.

## Non-goals

- Reimplementing rsync's delta algorithm. The native-Python fallback is a brute-force SFTP walk — a safety net, not parity.
- Job scheduling or SLURM integration. Out of scope; a user composes `rcc run -- sbatch ...` instead.
- Shared team configuration. `.rcc/` is gitignored and per-user; onboarding a teammate means they run `rcc init` themselves.
- GUI or TUI. Pure CLI.
- Windows host support for v1. macOS/Linux only (rsync + OpenSSH assumed).

## Stack

- **Python:** 3.11+ (stdlib `tomllib` required; no `tomli` backport).
- **Packaging:** `uv` + `pyproject.toml` + `uv.lock`. Published to PyPI. Console entry point: `rcc = rcc.cli:main`.
- **Dependencies:**
  - `typer` — CLI framework (subcommand dispatch, type-hint-driven args, help/completions).
  - `paramiko` — pure-Python fallback for `push`/`pull`/`run`/`shell` when `rsync` / `ssh` are not on PATH. Never used when `ssh` is available (see "Transport choice" below).
- **Dev tools:** `pytest`, `pytest-cov`, `ruff`. No ad-hoc shell test scripts.

## Subcommands (v1 scope)

- `rcc init` — scaffold `.rcc/config.toml` + `.rcc/rccignore` in the current directory.
- `rcc push` — sync local project dir to remote (rsync with fallback). Flags: `--dry-run/-n`, `--delete`, `--exclude PATTERN` (repeatable).
- `rcc pull` — sync remote to local. Same flags as `push`.
- `rcc run [-t] -- CMD [ARG...]` — run command remotely from `remote_dir`. `-t` forces PTY allocation for interactive commands.
- `rcc shell` — open an interactive remote shell cd'd into `remote_dir` (`ssh -t` primary, paramiko interactive channel as fallback).
- `rcc status` — report whether the SSH ControlMaster connection is open for the active profile's host.
- `rcc close` — close the SSH ControlMaster connection for the active profile's host.
- `rcc config` — print the resolved profile (after merging CLI overrides) for debugging.

Global flags (every subcommand): `--profile NAME`, `--host HOST`, `--remote-dir PATH`, `-v/--verbose`.

## Configuration

### `.rcc/` layout

```
.rcc/
├── config.toml     # profiles, default profile
└── rccignore       # gitignore-syntax excludes
```

`.rcc/` is gitignored. Each user runs `rcc init` in their clone.

### `.rcc/config.toml` schema

```toml
default = "clariden"              # required; name of the default profile

[profiles.clariden]
host = "clariden"                 # required; SSH host (alias or user@host)
remote_dir = "/capstor/..."       # required; absolute path on remote
ssh_control_persist = "30m"       # optional; default "30m"
ssh_control_dir = "~/.ssh/controlmasters"  # optional; default shown

[profiles.euler]
host = "euler"
remote_dir = "/cluster/home/..."
```

Fields:

| Field                  | Required | Type   | Default                          |
|------------------------|----------|--------|----------------------------------|
| `host`                 | yes      | string | —                                |
| `remote_dir`           | yes      | string | —                                |
| `ssh_control_persist`  | no       | string | `"30m"`                          |
| `ssh_control_dir`      | no       | string | `"~/.ssh/controlmasters"`        |

### `.rcc/rccignore`

Gitignore-syntax patterns. `rcc init` pre-seeds the file with:

```
.git/
.venv/
venv/
__pycache__/
.pytest_cache/
.mypy_cache/
.ruff_cache/
node_modules/
.next/
dist/
build/
```

For the rsync path: passed via `--exclude-from=.rcc/rccignore`. For the paramiko fallback: parsed in `ignore.py` and applied during tree walk.

`--exclude PATTERN` on the CLI is **additive** to `.rcc/rccignore`: both take effect together. (rsync naturally combines `--exclude` and `--exclude-from`; the fallback does the same by chaining patterns.)

### Profile resolution (highest precedence wins)

1. CLI flags: `--host`, `--remote-dir`.
2. `--profile NAME` → `[profiles.NAME]`.
3. Top-level `default = "NAME"` → `[profiles.NAME]`.

Example: `rcc push --host other-cluster` keeps the default profile's `remote_dir` and `excludes`, overriding only the host.

### Config discovery

Walk up from `cwd` looking for `.rcc/`, first match wins (same algorithm git uses for `.git/`). Subcommands other than `init` fail with `ConfigError: no .rcc/ found (run 'rcc init').` when no `.rcc/` is found.

## Transport choice

Every subcommand that touches the remote picks one of two transports at call time:

- **Primary transport: OpenSSH subprocess** — used whenever `ssh` (and, for sync, `rsync`) is on PATH. Benefits from ControlMaster multiplexing, standard ssh_config, ProxyJump, known_hosts. This is what `run`, `push`, `pull`, `shell`, `status`, `close` all prefer.
- **Fallback transport: paramiko** — used only when the primary transport's tools are unavailable. Opens a fresh TCP/auth connection per call (no mux reuse). Triggered with a `logger.warning("rsync/ssh not on PATH; using paramiko fallback")` message so the user knows they have a slow path.

Consequence: `rcc status`/`close` manage the OpenSSH ControlMaster used by every other subcommand (except the fallback path). They are a no-op / warning when the paramiko fallback is active, because paramiko doesn't share the socket.

## Architecture

### Module layout

```
rcc/
├── cli.py                  # typer app, global flag parsing, exception-to-exit-code mapping
├── config.py               # Profile dataclass, .rcc/ discovery, TOML load, CLI merge
├── ignore.py               # parse .rcc/rccignore (gitignore syntax) for native fallback
├── ssh.py                  # ssh arg builder, ControlMaster check/open/close, run_remote
├── sync.py                 # rsync subprocess path + paramiko SFTP fallback
├── errors.py               # ConfigError, RemoteError, MissingDependencyError
└── commands/
    ├── init.py
    ├── push.py
    ├── pull.py
    ├── run.py
    ├── shell.py
    ├── status.py
    ├── close.py
    └── config_cmd.py
```

**Dependency direction:** `commands → {ssh, sync, config} → {ignore, errors}`. No cycles.

**Rule:** `commands/*.py` files are thin. They parse subcommand-specific flags, resolve the profile, and delegate to `ssh.py` / `sync.py` / `config.py`. No business logic lives under `commands/`. This concentrates the testable surface in four files.

### Data flow

**Push:**

```
cli -> config.resolve_profile(cli_overrides) -> sync.push(profile, flags)
  1. ssh.ensure_remote_dir(profile)          # mkdir -p remote_dir on remote
                                              #   primary: ssh host "mkdir -p -- <dir>"
                                              #   fallback: paramiko exec same command
  2. if primary transport available:
       rsync <base_flags> [--dry-run] [--delete] \
             --exclude-from=.rcc/rccignore \
             [--exclude PATTERN ...]        # CLI --exclude is additive
             -e "ssh <mux flags>" \
             <project_dir>/ <host>:<remote_dir>/
     else:
       paramiko SFTP walk (see Fallback sync below)
```

Base rsync flags (lifted from `sync.sh`): `-az --human-readable --info=stats2,progress2 --partial`.
`--dry-run` also appends `--itemize-changes`.

**Pull:** same as push with source and destination swapped. No `ensure_remote_dir` step (local dir is assumed to exist — it's where `.rcc/` was found).

**Fallback sync (paramiko):**

```
connect via paramiko.SSHClient
walk local (or remote) tree, apply ignore.match() at each path
for each non-ignored regular file:
    sftp.put (push) or sftp.get (pull)
if --delete:
    walk destination, unlink/rmdir entries not present at source
--dry-run: print would-transfer / would-delete lines; no I/O
```

The two paths produce semantically-equivalent results for the common case. The fallback is slower and lacks delta transfer.

**Run:**

```
cli -> config.resolve_profile(cli_overrides) -> ssh.run_remote(profile, argv, tty=bool)
  if primary transport available:
      ssh <mux flags> [-t] <host> \
          "bash -lc '<quoted remote_command>'"
  else:
      paramiko exec_channel with optional get_pty(); stream stdout/stderr
```

`remote_command` is (single-quoted, `shlex.quote` on every piece):

```
set -euo pipefail
if [[ ! -d <remote_dir> ]]; then echo "error: remote directory does not exist: <remote_dir>" >&2; exit 1; fi
cd -- <remote_dir>
<quoted argv>...
```

This is lifted verbatim from `run.sh`. Exit code from remote process propagates to `rcc`'s exit code.

**Shell:** same as `run` with `tty=True` and no argv — drops the user into `bash -l` at `remote_dir`.

**Status / close:** call `ssh <mux flags> -O check|exit <host>` via the primary transport. If primary transport is unavailable, print a clear message ("ControlMaster not available without system ssh") and exit 1.

**ControlMaster directory:** `ssh_control_dir` (default `~/.ssh/controlmasters`). On first use, `rcc` `mkdir -p`s it with mode 0700 (matches `run.sh`). The control path template is `<ssh_control_dir>/%C`.

## Error handling

All errors are typed and caught at the boundary in `cli.main`:

| Exception                    | Exit | When                                                        |
|------------------------------|------|-------------------------------------------------------------|
| `ConfigError`                | 2    | no `.rcc/`, malformed TOML, unknown profile, missing field  |
| `MissingDependencyError`     | 127  | fallback path triggered but paramiko import also failed     |
| `RemoteError`                | N    | ssh/rsync non-zero exit; exit = remote code (or 1 if N/A)   |
| unhandled exception          | 1    | short message; full traceback only with `-v/--verbose`      |

## `rcc init`

Behavior:

- Creates `.rcc/config.toml` with a template profile and commented override examples.
- Creates `.rcc/rccignore` pre-seeded with the default exclude list (see Configuration).
- If `.rcc/` already exists: refuses unless `--force` is passed.
- Prints a next-step hint: `"edit .rcc/config.toml to set host and remote_dir, then run 'rcc push --dry-run'."`.
- Does not modify `.gitignore`; the user is responsible for gitignoring `.rcc/` (a note is printed).

## Testing strategy

**Unit (pytest, no network):**

- `config.py`: TOML parse, profile precedence, `.rcc/` discovery (via `tmp_path` fixture).
- `ignore.py`: gitignore-syntax match cases (positive, negative, directory-only, negation).
- `ssh.py`: arg-builder (input profile + flags to expected argv list, no subprocess invocation).

**Fallback sync:**

- SFTP fallback tested against `paramiko.SSHClient` mocked at the transport level.
- Verifies correct files are put and orphans are deleted on `--delete`.

**Integration (opt-in, skipped by default):**

- One test that runs `rcc push`/`pull`/`run` against `ssh localhost` when available.
- Marked `@pytest.mark.integration`; CI runs with `-m "not integration"` unless explicitly requested.

**No mocking of subprocess for the rsync path.** A `--dry-run` test against a real `rsync` binary is cheaper and more faithful than mocking rsync's flag grammar.

## Compatibility with existing scripts

Every behavior documented in `run.sh` and `sync.sh` has a direct counterpart:

| Bash script feature                         | `rcc` equivalent                                |
|---------------------------------------------|-------------------------------------------------|
| `REMOTE_HOST` / `REMOTE_DIR` env vars       | `--host` / `--remote-dir` CLI flags             |
| `SSH_CONTROL_PERSIST` / `SSH_CONTROL_DIR`   | Profile fields `ssh_control_persist`, `ssh_control_dir` |
| `--shell 'cmd'`                             | `rcc run -- bash -lc 'cmd'`                     |
| `--check-mux`                               | `rcc status`                                    |
| `--close-mux`                               | `rcc close`                                     |
| `--no-mux`                                  | *(not ported in v1; rare enough to defer)*      |
| `sync.sh --dry-run / --delete / --exclude`  | `rcc push --dry-run / --delete / --exclude`     |
| Hardcoded exclude list                      | `.rcc/rccignore` (pre-seeded with same list)    |
| `set -euo pipefail` + remote dir check      | Same prefix built in `ssh.py`                   |

## Open questions (to resolve before implementation)

None at this time. All v1 decisions are locked.

## Future work (post-v1)

- `--no-mux` flag per-invocation (low priority; rare use case).
- Shell completion install command (`rcc completion install`).
- Optional `.rcc/local.toml` override layer if multi-user support becomes necessary.
- Configurable `rsync_flags` profile field (v1 uses the hardcoded set below).

## Appendix A: SSH argv templates

`ssh.py` exposes one builder, `build_ssh_args(profile, *, mode)`, where `mode` is one of `run | mux_check | mux_exit | rsync_e`. The exact argv it produces:

**Shared mux flags** (present in every mode except `rsync_e` where they are embedded in the `-e` string):

```
-o ControlPersist=<ssh_control_persist>       # default "30m"
-o ControlPath=<ssh_control_dir>/%C           # default "~/.ssh/controlmasters/%C"
-o ControlMaster=auto
-o LogLevel=ERROR
```

**mode=run** (used by `rcc run`, `rcc shell`):

```
ssh <shared_mux_flags> [-t] <host> "bash -lc '<quoted remote_command>'"
```

- `-t` is included iff `tty=True`.
- `remote_command` is the `set -euo pipefail; cd; <argv>` block from Data flow.

**mode=mux_check** (used by `rcc status`):

```
ssh <shared_mux_flags> -O check <host>
```

Exit 0 means mux is open, non-zero means closed. (`-O check` only needs `ControlPath`; the other shared flags are harmless and kept for uniformity.)

**mode=mux_exit** (used by `rcc close`):

```
ssh <shared_mux_flags> -O exit <host>
```

**mode=rsync_e** (passed as `rsync -e "<this string>" ...`):

```
ssh -o ControlPersist=<..> -o ControlPath=<..> -o ControlMaster=auto -o LogLevel=ERROR
```

(Same options as `shared_mux_flags`, rendered as a single `-e` string for rsync.)

**Host argument:** passed verbatim as the profile's `host` field. rcc does not split `user@host` itself; OpenSSH handles it.

## Appendix B: rsync flag set

Full rsync invocation:

```
rsync \
    -a -z --human-readable --info=stats2,progress2 --partial \
    --exclude-from=.rcc/rccignore \
    [--exclude=PATTERN ...]             # one per CLI --exclude (additive)
    [--dry-run --itemize-changes]       # when --dry-run
    [--delete]                          # when --delete
    -e "<mode=rsync_e string from Appendix A>" \
    <source>/ <destination>/
```
