"""pydmcsid's renderer for the ``pysidtracker.survey`` whole-song HVSC oracle survey.

    PYTHONPATH=scripts python -m pysidtracker.survey run $HVSC tunes.csv \
        --work .hvsc-survey --render oracle_hvsc:render
"""

import numpy as np

from pysidtracker.cadence import cadence_info

from pydmcsid import DmcPlayer, parse
from pydmcsid.constants import STD_PLAY_REL

PLAYER = "^\\(?dmc(_v[45]\\.x\\))?$"

_BUILD = {
    "release_clears_adsr": "_release_clears_adsr",
    "release_clears_sr": "_rel_clears_sr",
    "pw_min_shift": "_pw_min_shift",
    "tail_d418": "_tail_d418",
    "onset_ctrl": "_onset_ctrl",
    "onset_adsr": "_onset_adsr",
}


def render(data, song, nframes):
    """Render 1-based ``song`` of a DMC ``.sid`` and the player build it decoded."""
    tune = parse(data)
    player = DmcPlayer(tune, subtune=song - 1)
    info = {
        "engine": type(player).__name__,
        "variant": tune.variant(),
        "byte_exact": tune.byte_exact(),
        "wrapped": tune.play != (tune.base + STD_PLAY_REL) & 0xFFFF,
        "stub": tune.base != tune.load,
        **cadence_info(data, song),
    }
    info.update({k: getattr(player, a, None) for k, a in _BUILD.items()})
    return np.asarray(player.render_grid(nframes), np.int16), info
