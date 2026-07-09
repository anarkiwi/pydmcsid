"""Exceptions for pydmcsid."""

from pysidtracker import SidError


class DmcError(SidError):
    """Base error for all pydmcsid failures."""


class SidParseError(DmcError):
    """A SID/PRG image could not be parsed as a DMC tune."""
