"""Shared test helpers (constants + frozen-grid loader)."""

from pathlib import Path

from pysidtracker.oracle import grid_from_writes as _grid_from_writes

FIXTURES = Path(__file__).resolve().parent / "fixtures"
NREG = 25

TUNES = {
    "ode": ("MUSICIANS/A/Ass_It/Ode_to_Music.sid", "Ode_to_Music.grid.txt"),
    "faces": ("DEMOS/A-F/Faces.sid", "Faces.grid.txt"),
    "fear": ("DEMOS/A-F/Fear_Me.sid", "Fear_Me.grid.txt"),
    "wladca": ("GAMES/S-Z/Wladca.sid", "Wladca.grid.txt"),
    # 2-entry (init/play) dispatch, identical body (sidid $37/$85 cluster): the
    # generalised anchor now recognises + plays it byte-exact.
    "summertime": ("MUSICIANS/B/Bart/Summertime.sid", "Summertime.grid.txt"),
    # A relocated build (load=$6600): byte-exact after the absolute-operand fix
    # (previously recognised but mis-played by the double-counted ``rel``).
    "action_tank": ("MUSICIANS/R/Robric/Action_Tank_2.sid", "Action_Tank_2.grid.txt"),
    # init-$1d generation ($7e/$7d/$7f markers), now reproduced byte-exact: the
    # base-layout body, a relocated build (load=$5000), and the relocated-cell
    # sub-layout (note cells at $1630) respectively.
    "glorious": ("MUSICIANS/B/Bakker_Nantco/Glorious.sid", "Glorious.grid.txt"),
    "rocket": (
        "MUSICIANS/B/Bayliss_Richard/Rocket_n_Roll.sid",
        "Rocket_n_Roll.grid.txt",
    ),
    "techno_bah": ("MUSICIANS/D/Doxx/Techno_BAH.sid", "Techno_BAH.grid.txt"),
    # Relocated play entry (dispatch play -> base+$50): the v1d engine with its
    # play entry shifted out of the id-string region, engine unchanged at
    # base+$b0.  Recognised + played byte-exact by the generalised anchor.
    "kordiaukis": ("DEMOS/G-L/Kordiaukis_Mix.sid", "Kordiaukis_Mix.grid.txt"),
    # Scene-modified init-$37 build: the $133d release is patched to also zero
    # AD/SR (an envelope-clearing hard-restart); pydmcsid detects the patch and
    # reproduces it byte-exact.  Stock $37 builds leave AD/SR static here.
    "insider": ("MUSICIANS/W/Willi/Insider_01.sid", "Insider_01.grid.txt"),
    # Hand-patched init-$1d build: the pw_min shift chain at $124b has its third
    # LSR overwritten by an illegal 2-byte no-op ($17), so pw_min = inst[2]>>2
    # (not >>4).  pydmcsid reads the shift from the code and reproduces it.
    "nop_years": ("MUSICIANS/A/Aomeba/20_Years_of_NOP.sid", "20_Years_of_NOP.grid.txt"),
    # $a1 engine (V5-era reorganised body, play at base+$a1): a genuinely
    # different generation reproduced byte-exact by PlayerA1.  Katusha is the
    # canonical body; Dum_Dum relocates its init (order-table base read from the
    # $17cf store site); Blutal_Haldcole patches out the release SR-clear
    # ($16c7 STA->BIT); Short_Fusion exercises the portamento-into-vibrato path.
    "katusha": ("DEMOS/G-L/Katusha.sid", "Katusha.grid.txt"),
    "dum_dum": ("MUSICIANS/F/Froyd/Dum_Dum.sid", "Dum_Dum.grid.txt"),
    "blutal": ("MUSICIANS/C/CreaMD/Blutal_Haldcole.sid", "Blutal_Haldcole.grid.txt"),
    "short_fusion": ("MUSICIANS/P/PRI/Short_Fusion.sid", "Short_Fusion.grid.txt"),
    # $95 engine (compact, self-modifying body, play at base+$95): a distinct
    # earlier lineage reproduced byte-exact by Player95.  Happy_Rave is the
    # canonical body; Popyjava_Pyjakoof and Intro_Music exercise the wildcarded
    # tempo-reload seed ($10bf = $02 / $00); I_Love_DMC is a second author's build.
    "happy_rave": ("DEMOS/G-L/Happy_Rave.sid", "Happy_Rave.grid.txt"),
    "popyjava": (
        "MUSICIANS/B/Booker/Popyjava_Pyjakoof.sid",
        "Popyjava_Pyjakoof.grid.txt",
    ),
    "intro_music": (
        "MUSICIANS/B/Bakewell_Dwayne/Intro_Music.sid",
        "Intro_Music.grid.txt",
    ),
    "i_love_dmc": (
        "MUSICIANS/B/Bayliss_Richard/I_Love_DMC.sid",
        "I_Love_DMC.grid.txt",
    ),
    # $94a family (init-$1d $85 body behind a 2-level PSID dispatch, engine
    # authored at a virtual base): recognised via the dispatch follow and played
    # byte-exact by PlayerNN (a thin PlayerV1D at the derived base).  Day_Noter +
    # High_Balance are canonical (virtual base = load+1); Poeci is the longer-stub
    # layout (virtual base = load+13); 90_Seconds is a second author's build.
    "day_noter": ("MUSICIANS/G/Glover/Day_Noter.sid", "Day_Noter.grid.txt"),
    "high_balance": ("MUSICIANS/G/Glover/High_Balance.sid", "High_Balance.grid.txt"),
    "poeci": ("MUSICIANS/W/Wodnik/Poeci.sid", "Poeci.grid.txt"),
    "ninety_sec": ("MUSICIANS/P/Psych858o/90_Seconds.sid", "90_Seconds.grid.txt"),
}


def load_grid(tune_id):
    """Load the committed frozen per-call oracle grid for ``tune_id``."""
    _rel, grid = TUNES[tune_id]
    rows = []
    with open(FIXTURES / grid, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line and not line.startswith("#"):
                rows.append([int(tok, 16) for tok in line.split()])
    return rows


def grid_from_writes(writes, cpf=19656):
    """Frame a ``(clock, reg, val)`` write-stream into a forward-filled per-frame
    grid via the shared :func:`pysidtracker.oracle.grid_from_writes` (the STANDARD
    framing the validator pipeline uses): frame 0 is the first PLAY call (the first
    write after the >10000-cycle init gap), the leading init burst forms frame 0's
    baseline, and PW-high registers are masked to 4 bits.
    """
    return _grid_from_writes(writes, cycles_per_frame=cpf, reg_count=NREG)
