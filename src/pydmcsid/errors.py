"""Exceptions for pydmcsid.

Built by :func:`pysidtracker.make_package_errors`: ``DmcParseError`` subclasses
both ``DmcError`` and :class:`pysidtracker.SidParseError` ("not a decodable DMC
tune"); ``SidParseError`` is kept as its backward-compatible alias.
"""

from pysidtracker import make_package_errors

DmcError, DmcParseError, DmcFormatError = make_package_errors("Dmc")
SidParseError = DmcParseError

__all__ = ["DmcError", "DmcFormatError", "DmcParseError", "SidParseError"]
