"""Shared test helpers (constants + frozen-grid loader)."""

from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"
NREG = 25
PW_HI_REGS = {0x03, 0x0A, 0x11}

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
    grid, identical to deplayroutine's ``oracle.grid_from_writes`` (the STANDARD
    framing the rest of the validator pipeline uses): frame 0 is the first PLAY
    call (the first write after the >10000-cycle init gap), with the leading init
    burst forming frame 0's baseline; PW-high registers are masked to 4 bits.
    """
    if not writes:
        return []
    cyc = [w[0] for w in writes]
    t0 = cyc[0]
    for prev, cur in zip(cyc, cyc[1:]):
        if cur - prev > 10000:
            t0 = cur
            break
    cur_row = [0] * NREG
    rows = []
    idx = 0
    while idx < len(writes) and writes[idx][0] < t0:
        _c, reg, val = writes[idx]
        if 0 <= reg < NREG:
            cur_row[reg] = (val & 0x0F) if reg in PW_HI_REGS else val
        idx += 1

    def frame_of(clock):
        return (clock - t0 + cpf // 2) // cpf

    nframes = frame_of(writes[-1][0]) + 1
    for frame in range(nframes):
        while idx < len(writes) and frame_of(writes[idx][0]) == frame:
            _c, reg, val = writes[idx]
            if 0 <= reg < NREG:
                cur_row[reg] = (val & 0x0F) if reg in PW_HI_REGS else val
            idx += 1
        rows.append(cur_row[:])
    return rows
