"""Scratch: dump canonical a1 / n95 body bytes (not to be merged)."""

import pydmcsid
from pydmcsid import constants as c


def test_dump(hvsc):
    out = []
    for tid, lo, hi in (
        ("DEMOS/G-L/Katusha.sid", c.A1_BODY_LO, c.A1_BODY_HI),
        ("MUSICIANS/C/CreaMD/Blutal_Haldcole.sid", c.A1_BODY_LO, c.A1_BODY_HI),
        ("DEMOS/G-L/Happy_Rave.sid", c.N95_BODY_LO, c.N95_BODY_HI),
        ("MUSICIANS/B/Bayliss_Richard/I_Love_DMC.sid", c.N95_BODY_LO, c.N95_BODY_HI),
    ):
        song = pydmcsid.parse(hvsc.read(tid))
        out.append(
            f"DUMP {tid} base={song.base:#06x} load={song.load:#06x} "
            f"{bytes(song.mem[song.base + lo : song.base + hi]).hex()}"
        )
    assert False, "\n" + "\n".join(out)
