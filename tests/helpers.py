"""Shared test constants: the HVSC tune list the tune-driven tests parametrize.

DMC ``.sid`` tunes are HVSC copyright works, never committed; each id maps to its
HVSC relative path (fetched + cached on demand).  The tunes span every DMC body
generation pydmcsid plays byte-exact -- init-``$37`` (:class:`~pydmcsid.Player`),
init-``$1d`` (:class:`~pydmcsid.PlayerV1D`), the ``$a1`` engine
(:class:`~pydmcsid.PlayerA1`), the ``$95`` engine (:class:`~pydmcsid.Player95`),
the ``$94a`` family (:class:`~pydmcsid.PlayerNN`) and its ``$937`` multispeed
wrapper (:class:`~pydmcsid.Player937`) -- plus the scene-patched builds each
variant's code-reading detectors reproduce.  The curated byte-exact-vs-oracle
subset lives in ``test_oracle_hvsc.py``.
"""

NREG = 25

TUNES = {
    # init-$37 body (Player): canonical, 2-entry dispatch, relocated build, and
    # the patched release/onset builds the $37 detectors reproduce.
    "ode": "MUSICIANS/A/Ass_It/Ode_to_Music.sid",
    "faces": "DEMOS/A-F/Faces.sid",
    "fear": "DEMOS/A-F/Fear_Me.sid",
    "wladca": "GAMES/S-Z/Wladca.sid",
    "summertime": "MUSICIANS/B/Bart/Summertime.sid",
    "action_tank": "MUSICIANS/R/Robric/Action_Tank_2.sid",
    "insider": "MUSICIANS/W/Willi/Insider_01.sid",
    "rock_zak": "MUSICIANS/B/Brian/Rock_Zak_1.sid",
    # init-$1d body (PlayerV1D): base + relocated + relocated-cell sub-layouts,
    # plus the patched shift/onset/tail-$D418 builds.
    "glorious": "MUSICIANS/B/Bakker_Nantco/Glorious.sid",
    "rocket": "MUSICIANS/B/Bayliss_Richard/Rocket_n_Roll.sid",
    "techno_bah": "MUSICIANS/D/Doxx/Techno_BAH.sid",
    "kordiaukis": "DEMOS/G-L/Kordiaukis_Mix.sid",
    "nop_years": "MUSICIANS/A/Aomeba/20_Years_of_NOP.sid",
    "snowball": "MUSICIANS/B/Bayliss_Richard/Snowball_Caper_2.sid",
    "for_vandalism": "MUSICIANS/R/Rorschach/For_Vandalism_27.sid",
    # $a1 engine (PlayerA1): canonical, relocated init, release-patched, and the
    # portamento-into-vibrato path.
    "katusha": "DEMOS/G-L/Katusha.sid",
    "dum_dum": "MUSICIANS/F/Froyd/Dum_Dum.sid",
    "blutal": "MUSICIANS/C/CreaMD/Blutal_Haldcole.sid",
    "short_fusion": "MUSICIANS/P/PRI/Short_Fusion.sid",
    # $95 engine (Player95): canonical + wildcarded tempo seed + second author.
    "happy_rave": "DEMOS/G-L/Happy_Rave.sid",
    "popyjava": "MUSICIANS/B/Booker/Popyjava_Pyjakoof.sid",
    "intro_music": "MUSICIANS/B/Bakewell_Dwayne/Intro_Music.sid",
    "i_love_dmc": "MUSICIANS/B/Bayliss_Richard/I_Love_DMC.sid",
    # $94a family (PlayerNN): virtual-base layouts + second author.
    "day_noter": "MUSICIANS/G/Glover/Day_Noter.sid",
    "high_balance": "MUSICIANS/G/Glover/High_Balance.sid",
    "poeci": "MUSICIANS/W/Wodnik/Poeci.sid",
    "ninety_sec": "MUSICIANS/P/Psych858o/90_Seconds.sid",
    # $937 CIA-multispeed wrapper (Player937): all-voices + per-voice phase masks.
    "dude": "MUSICIANS/P/Psych858o/Dude_with_Attitude.sid",
    "rusty": "MUSICIANS/P/Psych858o/My_Rusty_Love_C64.sid",
    "losing": "MUSICIANS/P/Psych858o/Losing_Control.sid",
    "coffee": "MUSICIANS/P/Psych858o/Cup_of_Coffee_and_Few_Cigs.sid",
    # Benign play-wrapper builds (resident body follows through the wrapper).
    "krupa_mix": "DEMOS/G-L/Krupa_Mix.sid",
    "sharkz": "MUSICIANS/B/Bayliss_Richard/Sharkz.sid",
    "axel_f": "MUSICIANS/P/PVCF/Axel_F.sid",
    "sun_eyes": "MUSICIANS/B/Bayliss_Richard/Sun_in_My_Eyes.sid",
}
