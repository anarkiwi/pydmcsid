"""Compare a playspec file's register grid with the tune's own 6502 run, call by call.

    python scripts/spec_oracle.py SPEC.play TUNE.sid [--frames N]

Prints the first call whose registers differ (the pulse-width high nibbles and the cutoff's low
bits masked) and exits 1, or 0 when the grids agree.
"""

import argparse
import sys
from pathlib import Path

from pysidtracker import find_format
from pysidtracker.oracle import EmuPlayer

sys.path.insert(0, str(Path(__file__).parent))
from spec_diff import player  # pylint: disable=wrong-import-position

MASK = {3: 0x0F, 10: 0x0F, 17: 0x0F, 21: 0x07}


def grid(regs):
    """``regs`` with the bits the chip ignores cleared."""
    return tuple(r & ~MASK.get(i, 0) for i, r in enumerate(regs))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("spec")
    parser.add_argument("tune")
    parser.add_argument("--frames", type=int, default=3000)
    args = parser.parse_args()
    raw = Path(args.tune).read_bytes()
    spec = player(raw, args.spec, None)
    fmt = find_format(raw)
    model = fmt.parser.parse(raw)
    binding = fmt.spec(model)
    _n, _a, _r, base, init, play = fmt.model_metadata(model)
    emu = EmuPlayer(binding.image, base, init, play, binding.subtune)
    for call in range(args.frames):
        spec.play_frame()
        emu.play_frame()
        if grid(spec.snapshot()) != grid(emu.snapshot()):
            print("call", call)
            print("spec", tuple(spec.snapshot()))
            print("6502", tuple(emu.snapshot()))
            return 1
    print("agree over", args.frames, "calls")
    return 0


if __name__ == "__main__":
    sys.exit(main())
