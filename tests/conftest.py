"""Shared test fixtures: on-demand HVSC tune access.

DMC ``.sid`` tunes are HVSC copyright works, never committed.  ``tune_path``
FETCHES + CACHES the tune on demand (HVSC mirror, honouring a local ``$HVSC``
tree) into the gitignored cache and the tune-driven tests RUN for real -- they do
not skip (a fetch failure is a test failure).
"""

import os
import sys
from pathlib import Path

from pysidtracker.testing import make_tune_fixtures

from helpers import TUNES

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

CACHE = Path(os.environ.get("DMC_TUNECACHE", str(REPO / "tests" / ".tunecache")))

# A missing tune is a test failure, not a skip (``skip_if_unavailable=False``).
tune_id, tune_path = make_tune_fixtures(TUNES, CACHE, skip_if_unavailable=False)
