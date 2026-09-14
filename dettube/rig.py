"""Rig definition: where the sensors are, and the thresholds that suit them.

Nothing in this package hard-codes a particular tube. A rig file describes one,
and every command takes `--rig`. Write a starting point with:

    dettube rig --template myrig.toml

Where the rig file is found, in order:

    1. --rig PATH
    2. $DETTUBE_RIG
    3. ./rig.toml
    4. ~/.config/dettube/rig.toml

Set DETTUBE_RIG once in the lab and no one has to pass --rig again.
"""
from __future__ import annotations

import os
from pathlib import Path

try:                                      # Python 3.11+
    import tomllib
except ModuleNotFoundError:               # 3.9, 3.10
    import tomli as tomllib               # type: ignore[no-redef]

SEARCH = ("rig.toml", "~/.config/dettube/rig.toml")

EVENT_LOG_TEMPLATE = '''
# ---------------------------------------------------------------------------
# The control system's own log of the shot, if it writes one beside the data.
# It records what was ARMED — which valves, how long before ignition — and none
# of that is anywhere in the TDMS. Without it the only record of how a shot was
# set up is the folder name, which is a statement of intent rather than of fact.
#
# `time_column`  the moment of ignition according to the control system. It is
#                compared against the ignitor spike measured in the pressure
#                record, and the disagreement is reported: on the rig this was
#                written for the two clocks sit whole hours apart because one
#                logs local time and the other UTC, which is silent and large.
# `flag_columns` TRUE/FALSE columns saying what was open or enabled.
# `lead_columns` timestamps whose LEAD before ignition is the quantity of
#                interest — how long a spray ran before the shot, say.
# `[event_log.label]` optional plain-English names, printed instead of the
#                column heading. Worth filling in: "SV04 Open" means nothing to
#                anyone reading the report a year later.
# ---------------------------------------------------------------------------
[event_log]
glob = ["Ignition Report*.csv", "Ignition Time*.csv"]
time_column = "Ignition Time"
flag_columns = ["SV01 Open", "SV02 Open"]
lead_columns = ["Mist Valve Open Time"]

[event_log.label]
"SV01 Open" = "water curtain at the first station"
"SV02 Open" = "water curtain at the second station"
"Mist Valve Open Time" = "mist valve"
'''

TEMPLATE = '''# dettube rig definition
#
# Describes one tube: where its gauges are, and the thresholds that suit them.
# Copy, edit, and point dettube at it with --rig or by setting $DETTUBE_RIG.

name = "example tube"

# ---------------------------------------------------------------------------
# Stations, in order along the tube.
#
# The three below are placeholders with deliberately irregular spacing — they are
# not any real tube, so replace them rather than adapting them.
#
# `x` is the axial position in metres, measured from whatever datum you choose —
# be explicit about it in `origin` below, because it is easy to assume the wrong
# one and every distance then inherits the error.
#
# `odd` and `even` are the channel NUMBERS of the two gauges at that station.
# Give both even if only one is fitted; a missing channel is simply skipped.
# Channel numbers need not be contiguous: if a section of tube is removed, its
# numbers just do not appear here.
# ---------------------------------------------------------------------------
origin = "closed head"

[[station]]
name = "S1"
x = 0.75
odd = 1
even = 2

[[station]]
name = "S2"
x = 1.85
odd = 3
even = 4

[[station]]
name = "S3"
x = 3.05
odd = 5
even = 6

# ---------------------------------------------------------------------------
# Sensor groups: how to find each kind of channel inside the TDMS files.
# `glob` matches the file, `group` is the TDMS group name, `prefix` is the
# channel-name stem, so prefix "PT" plus channel 3 means "PT-03".
# `odd_deg` / `even_deg` are only labels — the mounting angles of the two gauges.
# ---------------------------------------------------------------------------
[sensor.pt]
glob = "Pressure Output *.tdms"
group = "Pressure Transmitter Outputs"
prefix = "PT"
unit = "barg"
gain = 0.045          # metres of plot offset per unit
win = [-2.0, 55.0]    # default plot window, ms from the ignitor spike
odd_deg = "45°"
even_deg = "225°"
smooth_ms = 0.0
label = "pressure"
front = "pressure"

[sensor.pdt]
glob = "Volt PDT Output *.tdms"
group = "Photodiode-Volt Outputs"
prefix = "PDT"
unit = "V"
gain = 1.2
win = [-5.0, 90.0]
odd_deg = "135°"
even_deg = "315°"
smooth_ms = 0.2
label = "light"
front = "flame"

# Extra groups to include in a raw CSV export. The first two are taken from
# [sensor] above; list any others here.
[[extra_group]]
tag = "Mixed"
glob = "Mixed Output *.tdms"
group = "Mixed Outputs"

# ---------------------------------------------------------------------------
# Process instruments — the slow channels that say what was in the tube before
# the shot: mixture, flow, temperature. `dettube conditions` reports these.
#
# Every channel in the group is reported; the [[conditions.channel]] entries
# below are optional annotations. `on` is the system the instrument is mounted
# on, and it matters: an analyser on a supply header measures the supply, which
# equals the tube only if the tube was purged to equilibrium. Write down what
# you know so nobody has to infer it from a tag name later.
#
# `settle_s` is the length of the two windows compared to test whether the
# reading had stopped drifting before ignition.
# ---------------------------------------------------------------------------
[conditions]
glob = "Mixed Output *.tdms"
group = "Mixed Outputs"
settle_s = 1.0

[[conditions.channel]]
name = "AIT-EX01-H2"
on = "the supply header"
note = "sampling point is upstream of the tube — confirm it before quoting this as the in-tube mixture"

# ---------------------------------------------------------------------------
# Thresholds. The defaults below suit a metre-scale tube firing hydrogen/air at
# a few barg. Re-tune them for anything else — and read the README section on
# how the fronts are picked before changing them, because several of these
# numbers exist to defeat a specific artefact.
# ---------------------------------------------------------------------------
[threshold]
spark_floor = 0.5      # ignitor spike clears this on every pressure channel
spark_sigma = 20.0
blank_ms = 1.5         # ignore this long after the spike: it IS the spike
floor = 0.30           # absolute arrival threshold, same unit as the pressure gauges
sigma = 10.0           # or this many baseline standard deviations, whichever is larger
hold_ms = 0.10         # the crossing must persist this long to count
search_ms = 150.0
pair_ratio = 2.0       # flag a station whose two gauges differ by more than this
noise_frac = 0.10      # flag a channel whose baseline noise exceeds this of its peak

# Optical front. A flame crossing a viewport is a BROAD glow; an ignitor's
# electrical pickup is a sub-millisecond spike on every channel at once. The
# width gate is what separates them.
pdt_min_peak = 0.05
pdt_min_fwhm_ms = 2.0
pdt_smooth_ms = 0.2
pdt_search_ms = 120.0
# A flame at the sound speed of its own products is the last step before
# detonation. Above that, in a tube producing far slower flames, a mis-picked
# glow is likelier than a real event — so it is flagged, not reported quietly.
pdt_implausible_mps = 1000.0
max_dropped = 2
''' + EVENT_LOG_TEMPLATE

DEFAULTS = dict(spark_floor=0.5, spark_sigma=20.0, blank_ms=1.5, floor=0.30,
                sigma=10.0, hold_ms=0.10, search_ms=150.0, pair_ratio=2.0,
                noise_frac=0.10, pdt_min_peak=0.05, pdt_min_fwhm_ms=2.0,
                pdt_smooth_ms=0.2, pdt_search_ms=120.0,
                pdt_implausible_mps=1000.0, max_dropped=2)


class RigError(SystemExit):
    pass


def find_rig(explicit: str | Path | None = None) -> Path:
    if explicit:
        p = Path(explicit).expanduser()
        if not p.is_file():
            raise RigError(f"No rig file at {p}")
        return p
    env = os.environ.get("DETTUBE_RIG")
    if env:
        p = Path(env).expanduser()
        if not p.is_file():
            raise RigError(f"$DETTUBE_RIG points at {p}, which does not exist")
        return p
    for cand in SEARCH:
        p = Path(cand).expanduser()
        if p.is_file():
            return p
    raise RigError(
        "No rig file found.\n"
        "  dettube needs to be told where the gauges are; nothing about a tube is\n"
        "  built in. Pass --rig FILE, set $DETTUBE_RIG, or put rig.toml in this\n"
        "  folder. To start from a documented example:\n\n"
        "      dettube rig --template myrig.toml\n")


def load(path: str | Path | None = None) -> dict:
    """Read a rig file and return it in the shape the rest of the package uses."""
    p = find_rig(path)
    with open(p, "rb") as fh:
        raw = tomllib.load(fh)

    stations = raw.get("station") or []
    if not stations:
        raise RigError(f"{p} defines no [[station]] entries")
    pairs = []
    for s in stations:
        try:
            pairs.append((int(s["odd"]), int(s["even"]), float(s["x"]), str(s["name"])))
        except KeyError as exc:
            raise RigError(f"{p}: a [[station]] is missing {exc}")
    pairs.sort(key=lambda t: t[2])

    sensors = raw.get("sensor") or {}
    for key in ("pt", "pdt"):
        if key not in sensors:
            raise RigError(f"{p}: missing [sensor.{key}]")
        sensors[key]["win"] = tuple(sensors[key].get("win", (-2.0, 55.0)))

    groups = [(sensors["pt"]["prefix"], sensors["pt"]["glob"], sensors["pt"]["group"]),
              (sensors["pdt"]["prefix"], sensors["pdt"]["glob"], sensors["pdt"]["group"])]
    for g in raw.get("extra_group", []):
        groups.append((g["tag"], g["glob"], g["group"]))

    cond = raw.get("conditions")
    if cond:
        for key in ("glob", "group"):
            if key not in cond:
                raise RigError(f"{p}: [conditions] is missing {key}")
        cond["settle_s"] = float(cond.get("settle_s", 1.0))
        cond["annotation"] = {c["name"]: c for c in cond.pop("channel", [])
                              if "name" in c}

    ev = raw.get("event_log")
    if ev:
        for key in ("glob", "time_column"):
            if key not in ev:
                raise RigError(f"{p}: [event_log] is missing {key}")
        # Control software gets renamed. `glob` takes a list so that a rig can
        # keep matching the older name as well as the current one, and old shots
        # stay readable instead of quietly reporting nothing.
        ev["glob"] = [ev["glob"]] if isinstance(ev["glob"], str) else list(ev["glob"])
        ev["flag_columns"] = list(ev.get("flag_columns", []))
        ev["lead_columns"] = list(ev.get("lead_columns", []))
        ev["label"] = dict(ev.get("label", {}))

    th = dict(DEFAULTS)
    th.update(raw.get("threshold", {}))
    return dict(path=p, name=raw.get("name", p.stem), origin=raw.get("origin", "the datum"),
                pairs=pairs, sensors=sensors, groups=groups, threshold=th,
                conditions=cond or None, event_log=ev or None)
