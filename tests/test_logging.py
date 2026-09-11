"""Tests for the package's one logging setup."""

from __future__ import annotations

import logging

from utility import logging as pkg_logging


def test_a_logger_is_named_for_the_module_that_asked_for_it() -> None:
    """The name is what tells a reader which parser emitted a warning."""
    assert pkg_logging.get_logger("utility.fda_date").name == "utility.fda_date"


def test_the_same_name_returns_the_same_logger() -> None:
    """Module-level ``_log = get_logger(__name__)`` must not multiply handlers."""
    first = pkg_logging.get_logger("utility.application_number")
    second = pkg_logging.get_logger("utility.application_number")
    assert first is second


def test_configure_installs_exactly_one_handler() -> None:
    """The entry script configures, and so does any test — once, in total."""
    root = logging.getLogger()
    saved = root.handlers[:]
    saved_level = root.level
    try:
        root.handlers = []
        pkg_logging.configure()
        assert len(root.handlers) == 1
    finally:
        root.handlers = saved
        root.setLevel(saved_level)


def test_configure_is_idempotent() -> None:
    """A second call must not double every line of output."""
    root = logging.getLogger()
    saved = root.handlers[:]
    saved_level = root.level
    try:
        root.handlers = []
        pkg_logging.configure()
        pkg_logging.configure()
        pkg_logging.configure()
        assert len(root.handlers) == 1
    finally:
        root.handlers = saved
        root.setLevel(saved_level)


def test_the_level_is_what_the_caller_asked_for() -> None:
    """``--verbose`` has to actually reach the root logger."""
    root = logging.getLogger()
    saved = root.handlers[:]
    saved_level = root.level
    try:
        root.handlers = []
        pkg_logging.configure(logging.DEBUG)
        assert root.level == logging.DEBUG
        # Idempotent on handlers, but the level still moves on a later call.
        pkg_logging.configure(logging.WARNING)
        assert root.level == logging.WARNING
    finally:
        root.handlers = saved
        root.setLevel(saved_level)
