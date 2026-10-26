"""pydmcsid's renderer for the ``pysidtracker.survey`` whole-song HVSC oracle survey.

    PYTHONPATH=scripts python -m pysidtracker.survey run $HVSC tunes.csv \
        --work .hvsc-survey --render oracle_hvsc:render
"""

import numpy as np

from pydmcsid import DmcPlayer, parse
from pydmcsid.format import dmc_format

PLAYER = "^\\(?dmc(_v[45]\\.x\\))?$"

_FORMAT = dmc_format()


def render(data, song, nframes):
    """Render 1-based ``song`` of a DMC ``.sid`` and the build variant it decoded."""
    tune = parse(data)
    player = DmcPlayer(tune, subtune=song - 1)
    info = {"engine": type(player).__name__, **_FORMAT.info(tune, data, song)}
    return np.asarray(player.render_grid(nframes), np.int16), info
