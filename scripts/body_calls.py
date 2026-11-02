"""List the addresses inside a tune's player body that the tune's own code jumps or calls to.

    python scripts/body_calls.py TUNE.sid [--frames N]

Plays the tune's 6502 and prints each body address first reached from outside the body, with the
registers it is entered with, as ``address (offset from the base): count``.
"""

import argparse
import collections
import sys
from pathlib import Path

import functools

from pysidtracker import find_format, oracle
from pysidtracker.oracle import EmuPlayer

from pydmcsid import constants

BODIES = {
    "a1": (constants.A1_BODY_LO, constants.A1_BODY_HI),
    "n95": (constants.N95_BODY_LO, constants.N95_BODY_HI),
}


def calls(raw: bytes, frames: int):
    """``{(offset, x): count}`` of the body addresses entered from outside it."""
    oracle.wire_mpu = functools.partial(oracle.wire_mpu, native=False)
    fmt = find_format(raw)
    model = fmt.parser.parse(raw)
    binding = fmt.spec(model)
    _n, _a, _r, base, init, play = fmt.model_metadata(model)
    song = model
    lo, hi = (
        song.base + BODIES.get(song.variant(), (0x40, 0x707))[0],
        song.base + BODIES.get(song.variant(), (0x40, 0x707))[1],
    )
    player = EmuPlayer(binding.image, base, init, play, binding.subtune)
    seen = collections.Counter()
    player.play_frame()
    mpu = player._mpu  # pylint: disable=protected-access
    last = [0]
    orig = mpu.step

    def step():
        pc = mpu.pc
        inside = lo <= pc < hi
        if inside and not lo <= last[0] < hi:
            seen[(pc - song.base, mpu.x)] += 1
        last[0] = pc
        return orig()

    mpu.step = step
    for _ in range(frames):
        player.play_frame()
    return seen


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("tune")
    parser.add_argument("--frames", type=int, default=200)
    args = parser.parse_args()
    for (off, x), count in sorted(
        calls(Path(args.tune).read_bytes(), args.frames).items()
    ):
        print(f"{off:#06x} x={x}: {count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
