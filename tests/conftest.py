"""Shared test fixtures: tune access + the committed byte-exact oracle grids.

DMC ``.sid`` tunes are HVSC copyright works, never committed.  ``tune_path``
FETCHES + CACHES the tune on demand (HVSC mirror, honouring a local ``$HVSC``
tree) into the gitignored cache and the byte-exact tests RUN for real -- they do
not skip (a fetch failure is a test failure).  The ground-truth per-frame
register grids are committed frozen (``fixtures/*.grid.txt``), framed per VBI
play call (the DMC framing), so no emulator binary is needed.
"""

import os
import sys
from pathlib import Path

import pytest

from pysidtracker.testing import make_tune_fixtures

from helpers import TUNES, load_grid

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

CACHE = Path(os.environ.get("DMC_TUNECACHE", str(REPO / "tests" / ".tunecache")))

# id -> HVSC relative path for the byte-exact tunes (helpers pairs each id with a
# frozen oracle grid).  The byte-exact validation REQUIRES the tune, so an
# unreachable fetch is a test failure, NOT a skip (``skip_if_unavailable=False``).
_TUNE_RELPATHS = {tid: rel for tid, (rel, _grid) in TUNES.items()}

tune_id, tune_path = make_tune_fixtures(
    _TUNE_RELPATHS, CACHE, skip_if_unavailable=False
)


@pytest.fixture
def oracle_grid(tune_id):  # pylint: disable=redefined-outer-name
    """The committed frozen per-call register grid for ``tune_id``."""
    return load_grid(tune_id)
