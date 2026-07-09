"""Read and play DMC (Demo Music Creator) SID tunes (pure-Python)."""

from pydmcsid.errors import DmcError, SidParseError
from pydmcsid.player import Player, iter_frames
from pydmcsid.reader import (
    DmcSidParser,
    Song,
    dmc_byte_exact,
    find_dmc_base,
    parse,
    read,
)
from pydmcsid.reglog import RegWrite, iter_register_writes

__version__ = "0.1.0"

__all__ = [
    "DmcError",
    "DmcSidParser",
    "Player",
    "RegWrite",
    "SidParseError",
    "Song",
    "__version__",
    "dmc_byte_exact",
    "find_dmc_base",
    "iter_frames",
    "iter_register_writes",
    "parse",
    "read",
]
