# rcc

Remote cluster controller. Generalizes the classic `ssh host "cd dir && cmd"` +
`rsync project/ host:dir/` workflow into a small CLI with per-project config.

## Install

```bash
uv tool install remote-cluster-controller
# or
pipx install remote-cluster-controller
```

Both install the `rcc` command.

## Quickstart

```bash
cd my-project
rcc init
# edit .rcc/config.toml to set host and remote_dir
rcc push --dry-run
rcc push
rcc run -- nvidia-smi
rcc run -t -- htop
rcc run -s 'squeue -u $USER | head'   # shell snippet: pipelines, $vars survive
rcc shell
rcc pull
rcc status
rcc close
```

## Configuration

`.rcc/config.toml` (per project, gitignored):

```toml
default = "myhost"

[profiles.myhost]
host = "myhost"
remote_dir = "/abs/path/on/remote"
```

Per-command overrides: `--profile`, `--host`, `--remote-dir`.
Excludes: edit `.rcc/rccignore` (gitignore syntax).

## Slurm jobs (`rcc job`)

For HPC login nodes running Slurm, `rcc job` wraps the common verbs so you
never have to shell-quote a `--format=` value, a `$USER`, or a pipeline (the
friction that motivated this command — see issue #1).

```bash
rcc job submit train.sh --extra-env EPOCHS=10   # sbatch, prints the JOBID
rcc job submit train.sh -W --dependency afterok:524614   # block until done + chain after a prior job
rcc job list                                     # squeue for your user
rcc job status 524614                            # sacct -j <id> (fixed format)
rcc job tail 524614 -f                           # tail -f slurm-524614.out
rcc job cancel 524614                            # scancel
```

Notes:

- The `submit`/`list`/`status`/`cancel` verbs sniff for `sbatch` on the remote
  and exit `127` with a hint on non-Slurm hosts; `tail` skips the check (`tail`
  is universal).
- `list` and `status` use a fixed, readable `--format=`; you never type one.
- `job submit -W/--wait` blocks until the job finishes and `rcc` exits with the
  job's exit code — closing the submit→monitor loop in one command. `--dependency`
  passes `--dependency=<TYPE:JOBID>` straight to sbatch for chaining (e.g.
  `afterok:524614`).
- `job tail` reads `slurm-<JOBID>.out` inside `remote_dir` by default. Pass
  `--file NAME` for jobs that set a custom `--output`.

For one-off Slurm commands that aren't wrapped, use the shell-string mode of
`rcc run`, which also sidesteps the quoting problem:

```bash
rcc run -s 'sacct -j 524614 --format=JobID,State,Elapsed,ExitCode,Reason'
```

See [`docs/superpowers/specs/2026-04-23-rcc-design.md`](docs/superpowers/specs/2026-04-23-rcc-design.md) for the full spec.
