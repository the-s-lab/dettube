"""Shared rig knowledge and TDMS reading for the detonation tube.

Everything that describes the RIG lives here and nowhere else. The station
positions in this project have already been wrong once because the same numbers
were kept in several files and drifted apart; keeping them in one module is the
fix, not a tidiness preference.
"""
from __future__ import annotations

import datetime as dt
import mmap
import os
import shutil
import struct
from pathlib import Path

import numpy as np

# --- rig, loaded at run time -----------------------------------------------
# These containers are MUTATED IN PLACE by load_rig() rather than rebound, so
# that `from .core import PAIRS` elsewhere keeps pointing at the live object.
# Nothing about any particular tube is built in; see dettube/rig.py.
PAIRS: list = []            # (odd_ch, even_ch, x_m, station_name), ordered along the tube
SENSORS: dict = {}          # "pt" / "pdt" -> how to find and draw that group
GROUPS: list = []           # (tag, file glob, TDMS group) for raw export
CFG: dict = {}              # thresholds
COND: dict = {}             # process-instrument group, empty if the rig omits it
EVENTS: dict = {}           # control-system shot log, empty if the rig omits it
RIG: dict = {}              # the whole loaded definition, for titles and provenance

# Colour by side of the tube, not by station: one colour per side means a fault
# affecting a whole side shows up as a pattern down the plot rather than as one
# odd-looking station. Checked for colour-vision safety (dE 18.0 deutan/protan,
# 18.7 normal vision, both above the 3:1 contrast floor on white).
COL_ODD, COL_EVEN = "#009E73", "#0072B2"


def load_rig(path=None) -> dict:
    """Load a rig definition and install it for the rest of the package."""
    from . import rig as _rig

    r = _rig.load(path)
    PAIRS[:] = r["pairs"]
    SENSORS.clear(); SENSORS.update(r["sensors"])
    GROUPS[:] = r["groups"]
    CFG.clear(); CFG.update(r["threshold"])
    COND.clear(); COND.update(r.get("conditions") or {})
    EVENTS.clear(); EVENTS.update(r.get("event_log") or {})
    RIG.clear(); RIG.update(r)
    return r


def read_group(path: Path, group_name: str, workdir: Path):
    """Open a rig TDMS file, trimming the trailing zero padding the VI leaves."""
    import datetime as dt

    from nptdms import TdmsFile

    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        mm = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
        pos = last = 0
        while pos + 28 <= size and mm[pos:pos + 4] == b"TDSm":
            _toc, _ver, nxt, _raw = struct.unpack_from("<IIQQ", mm, pos + 4)
            if nxt == 0 or nxt > size:
                break
            pos = pos + 28 + nxt
            last = pos
        mm.close()
    clean = workdir / path.name
    with open(path, "rb") as fi, open(clean, "wb") as fo:
        shutil.copyfileobj(fi, fo, length=1 << 20)
    os.truncate(clean, last)
    g = TdmsFile.read(str(clean))[group_name]
    data = [c for c in g.channels() if c.name != "Time[s]"]
    # An aborted acquisition leaves a group with channels but no waveform
    # properties and no samples — one September shot recorded an empty Pitot
    # file this way. Say so plainly instead of dying on a KeyError.
    if not data or "wf_increment" not in data[0].properties:
        raise ValueError(f"'{group_name}' in {path.name} has no sampled data "
                         "(the acquisition looks to have been aborted)")
    first = data[0]
    inc = float(first.properties["wf_increment"])
    t0 = first.properties["wf_start_time"].astype("datetime64[us]").astype(dt.datetime)
    return g, inc, t0


def find_spark(group, inc: float) -> float:
    names = {c.name for c in group.channels()}
    pref = SENSORS["pt"]["prefix"]
    first = []
    for a, b, _, _ in PAIRS:
        for ch in (a, b):
            if f"{pref}-{ch:02d}" not in names:
                continue
            s = group[f"{pref}-{ch:02d}"][:].astype(float)
            base = s[:20_000]
            hit = np.flatnonzero(np.abs(s - np.median(base))
                                 > max(CFG["spark_floor"], CFG["spark_sigma"] * np.std(base)))
            if len(hit):
                first.append(hit[0] * inc)
    if not first:
        raise SystemExit("No pressure event found — misfire, or the wrong folder?")
    return min(first)


def smooth(x: np.ndarray, n: int) -> np.ndarray:
    return np.convolve(x, np.ones(n) / n, mode="same") if n > 1 else x


def longest_causal(pts: list, weight: list) -> list:
    """Indices of the best run of stations whose arrival times never go backwards.

    The front cannot reach a downstream station before an upstream one, so a
    station that breaks that order is a mis-pick. Rather than discard the whole
    shot, keep the largest self-consistent set.

    Several different sets are often the same size, so length alone does not
    decide it — and choosing badly throws away the good stations instead of the
    bad one. The tie is broken on WEIGHT, the glow amplitude: the station that
    saw the most light is the one most likely to be genuinely timing the flame.
    Signal-to-noise would be the wrong weight here, because a quiet photodiode
    that barely sees the flame scores well on it.
    """
    n = len(pts)
    if n == 0:
        return []
    best = [(1, weight[i]) for i in range(n)]
    prev = [-1] * n
    for i in range(n):
        for j in range(i):
            if pts[i][1] >= pts[j][1]:
                cand = (best[j][0] + 1, best[j][1] + weight[i])
                if cand > best[i]:
                    best[i], prev[i] = cand, j
    i = max(range(n), key=lambda k: best[k])
    out = []
    while i != -1:
        out.append(i)
        i = prev[i]
    return out[::-1]



def choose_folder():
    """Ask for a shot folder with a GUI picker; None if tkinter is unavailable."""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception:
        return None
    root = tk.Tk()
    root.withdraw()
    d = filedialog.askdirectory(
        title="Select the shot folder (the one holding the .tdms files)")
    root.destroy()
    return Path(d) if d else None
