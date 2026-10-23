"""Shared test fixtures: on-demand HVSC tune access.

DMC ``.sid`` tunes are HVSC copyright works, never committed.  ``tune_path``
fetches the tune on demand into pysidtracker's shared tune cache (honouring a
local ``$HVSC`` tree); a fetch failure is a test failure, not a skip.
"""

import sys
from pathlib import Path

from pysidtracker.testing import make_tune_fixtures

from helpers import TUNES

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

tune_id, tune_path = make_tune_fixtures(TUNES, skip_if_unavailable=False)
