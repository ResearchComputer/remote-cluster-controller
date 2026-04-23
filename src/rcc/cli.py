from __future__ import annotations

import functools
import logging
import sys
from collections.abc import Callable
from typing import ParamSpec, TypeVar

import typer

from rcc import __version__
from rcc.commands import close as close_cmd
from rcc.commands import config_cmd
from rcc.commands import init as init_cmd
from rcc.commands import pull as pull_cmd
from rcc.commands import push as push_cmd
from rcc.commands import run as run_cmd
from rcc.commands import shell as shell_cmd
from rcc.commands import status as status_cmd
from rcc.context import CliOverrides, set_cli_overrides
from rcc.errors import ConfigError, MissingDependencyError, RccError, RemoteError

P = ParamSpec("P")
R = TypeVar("R")

app = typer.Typer(
    help="rcc - remote cluster controller: push, pull, run, shell over SSH + rsync.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)

_verbose = False


@app.callback()
def _root(
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Verbose logging"),
    profile: str | None = typer.Option(None, "--profile", help="Default profile override"),
    host: str | None = typer.Option(None, "--host", help="Default host override"),
    remote_dir: str | None = typer.Option(None, "--remote-dir", help="Default remote dir override"),
) -> None:
    global _verbose
    _verbose = verbose
    set_cli_overrides(CliOverrides(profile=profile, host=host, remote_dir=remote_dir))
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, format="%(message)s")


def _handle_rcc_error(exc: Exception) -> None:
    if isinstance(exc, ConfigError):
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    if isinstance(exc, MissingDependencyError):
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=127) from exc
    if isinstance(exc, RemoteError):
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=exc.exit_code) from exc
    if isinstance(exc, RccError):
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if _verbose:
        raise exc
    typer.echo(f"error: {exc}", err=True)
    raise typer.Exit(code=1) from exc


def _wrap(fn: Callable[P, R]) -> Callable[P, R | None]:
    @functools.wraps(fn)
    def inner(*args: P.args, **kwargs: P.kwargs) -> R | None:
        try:
            return fn(*args, **kwargs)
        except typer.Exit:
            raise
        except Exception as exc:
            _handle_rcc_error(exc)
            return None

    return inner


app.command(name="init")(_wrap(init_cmd.init))
app.command(name="push")(_wrap(push_cmd.push))
app.command(name="pull")(_wrap(pull_cmd.pull))
app.command(
    name="run",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)(_wrap(run_cmd.run))
app.command(name="shell")(_wrap(shell_cmd.shell))
app.command(name="status")(_wrap(status_cmd.status))
app.command(name="close")(_wrap(close_cmd.close))
app.command(name="config")(_wrap(config_cmd.config))


@app.command(name="version")
def version() -> None:
    """Print rcc version."""
    typer.echo(__version__)


def main() -> None:
    try:
        app()
    except typer.Exit:
        raise
    except Exception as exc:
        _handle_rcc_error(exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
