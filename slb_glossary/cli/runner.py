"""Run a command's async body from click's synchronous callback interface."""

import asyncio
import typing

import click

__all__ = ["run_async"]


T = typing.TypeVar("T")


def run_async(coro: typing.Coroutine[typing.Any, typing.Any, T], /) -> T:
    """
    Run `coro` to completion, turning Ctrl-C into a clean CLI abort.

    :param coro: The coroutine to run, e.g. an `async def` command body.
    :return: Whatever `coro` returns.
    :raises click.Abort: If the run is interrupted with Ctrl-C, instead of
        letting a raw `KeyboardInterrupt` traceback reach the user.
    """
    try:
        return asyncio.run(coro)
    except KeyboardInterrupt as exc:
        raise click.Abort() from exc
