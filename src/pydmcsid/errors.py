"""Exceptions for pydmcsid.

The hierarchy is built by the shared :func:`pysidtracker.make_package_errors`
factory so the DMC-named errors subclass BOTH the ``DmcError`` root AND the base
``pysidtracker`` errors (a caller's ``except SidParseError`` still catches them).
"""

from pysidtracker import make_package_errors

DmcError, SidParseError, DmcFormatError = make_package_errors("Dmc")

__all__ = ["DmcError", "DmcFormatError", "SidParseError"]
