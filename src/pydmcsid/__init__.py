"""Read and play DMC (Demo Music Creator) SID tunes (pure-Python)."""

from pydmcsid.errors import DmcError, SidParseError
from pydmcsid.player import (
    Player,
    Player937,
    PlayerA1,
    PlayerNN,
    PlayerV1D,
    iter_frames,
)
from pydmcsid.reader import (
    DmcSidParser,
    Song,
    dmc_byte_exact,
    dmc_variant,
    find_dmc_base,
    parse,
    read,
    release_clears_adsr,
)
from pydmcsid.reglog import RegWrite, iter_register_writes
from pydmcsid.writer import image_bytes, to_prg, to_sid, write

__version__ = "0.1.0"

__all__ = [
    "DmcError",
    "DmcSidParser",
    "Player",
    "Player937",
    "PlayerA1",
    "PlayerNN",
    "PlayerV1D",
    "RegWrite",
    "SidParseError",
    "Song",
    "__version__",
    "dmc_byte_exact",
    "dmc_variant",
    "find_dmc_base",
    "image_bytes",
    "iter_frames",
    "iter_register_writes",
    "parse",
    "read",
    "release_clears_adsr",
    "to_prg",
    "to_sid",
    "write",
]
