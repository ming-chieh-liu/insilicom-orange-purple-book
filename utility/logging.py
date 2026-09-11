"""One logging setup for the whole package, configured once from ``main``.

Every module takes its logger by name at import (``_log = get_logger(__name__)``)
and never configures handlers itself, so importing a utility never has a side
effect on a caller's logging. :func:`configure` is idempotent because the entry
script is not the only thing that calls it — the test suite does too, and a
second call must not double every line.

The level matters here more than in most packages. The parsers in this package
warn once per defective row, and a defective row is rare by design: 52 of
137,804 ``ndc`` application numbers are source typos, and zero dates in the whole
corpus fail to parse. A warning is therefore news, and is meant to be read.
"""

from __future__ import annotations

import logging

#: The format every line uses. The logger name is the module path, which is what
#: tells a reader whether a warning came from a date or an application number.
_FORMAT = "%(levelname)s %(name)s: %(message)s"


def configure(level: int = logging.INFO) -> None:
    """Install a single stderr handler on the root logger, at most once.

    Calling this twice is normal and must be harmless — the entry script
    configures, and so does any test that wants to see output.
    """
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(_FORMAT))
        root.addHandler(handler)
    root.setLevel(level)


def get_logger(name: str) -> logging.Logger:
    """The logger a module should use, named for the module.

    >>> get_logger("utility.fda_date").name
    'utility.fda_date'
    """
    return logging.getLogger(name)


__all__ = ["configure", "get_logger"]
