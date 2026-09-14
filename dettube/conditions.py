"""What was in the tube before the shot — read from the instruments, not the folder name.

Shot folders get named for the mixture that was aimed at. The analyser records
what was actually there, and the two are not always the same: one shot in the
set this was written against sits in a folder called `50%H2` and reads 48.4%.
So read the instrument.

Three things this reports, and why each is needed
-------------------------------------------------
`value ± spread`
    The median over the pre-ignition window, and the median absolute deviation
    around it. The MEDIAN and MAD are used rather than mean and standard
    deviation because these channels carry heavy impulsive noise: on this rig a
    flow transmitter sitting at a true zero throws single-sample spikes to
    9 L/min about 0.4% of the time, which inflates an sd by two orders of
    magnitude and says nothing about the reading. Quote the value to the spread,
    not to the digits the DAQ prints — 50.83 with a spread of 0.31 is 50.8.

`pre range`
    The full span of that same window, spikes included. It is printed next to
    the spread precisely so the impulsive noise is visible rather than averaged
    away; a narrow spread beside a wide range means "steady, but on a noisy
    line", which is a different thing from "steady".

`after ignition`
    Whether the channel shows a SUSTAINED change once the shot fires — a run of
    at least `SUSTAIN_MS` entirely outside the pre-ignition envelope — and how
    long after the spike it starts. Isolated spikes do not count, because these
    channels spike at the same rate before ignition as after; anything that
    counted them would report a response on every channel and mean nothing.

    A sustained response arriving in under `SUSPECT_MS` is flagged. No gas
    analyser or sheathed thermocouple responds in a millisecond, so a change
    that fast is the ignitor's electrical pickup or a wiring fault, not the
    process. The flag is the whole reason the timing is measured.
"""
from __future__ import annotations

import csv
import datetime as dt
import math
import re
from pathlib import Path

import numpy as np

from .core import COND, EVENTS, SENSORS, find_spark, read_group

GUARD_S = 0.05          # ignore this much at the start of the record (DAQ settling)
ENVELOPE = 1.2          # "outside the pre-ignition envelope" = this times its worst excursion
SUSTAIN_MS = 5.0        # ...held continuously this long, so single spikes do not count
SUSPECT_MS = 1.0        # a sustained response faster than this cannot be the process


def decimals(spread: float) -> int:
    """Digits to print: one past the spread, so the last one is the uncertain one."""
    if not math.isfinite(spread) or spread <= 0:
        return 2
    return int(min(4, max(0, 1 - math.floor(math.log10(spread)))))


def fmt(value: float, spread: float) -> str:
    d = decimals(spread)
    return f"{value:.{d}f} ± {spread:.{d}f}"


def read_conditions(shot: Path, work: Path) -> dict:
    """Pre-ignition statistics for every channel in the rig's conditions group."""
    if not COND:
        raise SystemExit(
            "This rig defines no [conditions] group, so there is nothing to report.\n"
            "  Add one naming the TDMS group that holds the slow process channels —\n"
            "  see the commented example in `dettube rig --template`.")

    pt_files = sorted(shot.glob(SENSORS["pt"]["glob"]))
    if not pt_files:
        raise SystemExit(f"No '{SENSORS['pt']['glob']}' in {shot} — "
                         "it is needed to locate the ignitor spike")
    files = sorted(shot.glob(COND["glob"]))
    if not files:
        raise SystemExit(f"No '{COND['glob']}' in {shot}")

    pg, pinc, pt0 = read_group(pt_files[0], SENSORS["pt"]["group"], work)
    spark_pt = find_spark(pg, pinc)
    g, inc, t0 = read_group(files[0], COND["group"], work)
    # Separate DAQ task, separate start time: put the spike on this group's clock.
    spark = spark_pt + (pt0 - t0).total_seconds()

    chans = [c for c in g.channels() if c.name != "Time[s]"]
    n = min(len(c) for c in chans)
    dur = n * inc
    if not 0 < spark <= dur:
        raise SystemExit(
            f"The ignitor spike falls at {spark:.2f} s but the '{COND['group']}' "
            f"record is {dur:.2f} s long.\n  The two files do not cover the same "
            "event — check the folder holds one shot only.")

    pre_end = spark - GUARD_S
    span = pre_end - GUARD_S
    if span <= 0.1:
        raise SystemExit(
            f"Only {max(span, 0.0) * 1e3:.0f} ms of record precedes the ignitor spike. "
            "There is no\n  pre-ignition window to average over.")

    # Two adjacent windows ending at ignition, for the drift test. Shrink them
    # together if the record is too short to hold both, and say so.
    settle = min(COND.get("settle_s", 1.0), span / 2)
    t = np.arange(n) * inc
    pre = (t >= GUARD_S) & (t <= pre_end)
    late = (t > pre_end - settle) & (t <= pre_end)
    early = (t > pre_end - 2 * settle) & (t <= pre_end - settle)
    spark_i = int(spark / inc)
    hold = max(1, int(round(SUSTAIN_MS / 1e3 / inc)))
    post_ms = (n - spark_i) * inc * 1e3

    rows = []
    for c in chans:
        s = c[:][:n].astype(float)
        a = s[pre]
        med = float(np.median(a))
        mad = float(1.4826 * np.median(np.abs(a - med)))
        lo, hi = float(a.min()), float(a.max())
        env = float(np.max(np.abs(a - med)))
        drift = (float(np.median(s[late])) - float(np.median(s[early]))
                 if early.sum() > 10 and late.sum() > 10 else float("nan"))

        # A channel pinned to one end of its span. Two very different things look
        # like this, and the number of distinct values separates them: an input
        # still being digitised dithers over hundreds of codes even when the
        # reading it carries is a true zero, whereas a channel that is not being
        # read at all repeats one value exactly. Counting is enough to tell them
        # apart, so the tool does not have to leave it ambiguous.
        #
        # What this cannot show is whether the INSTRUMENT behind a live input is
        # powered and calibrated: a transmitter sitting at the bottom of its
        # output range looks the same as one correctly reporting zero. Only a
        # span check or a shot that actually contains the species settles that.
        uniq = int(np.unique(a).size)
        at_floor = mad == 0.0 and (med == lo or med == hi)
        frozen = uniq <= 1

        post = s[spark_i:]
        shift = float(np.median(post) - med) if post.size else float("nan")
        respond = float("nan")
        if post.size >= hold and env > 0:
            out = (np.abs(post - med) > ENVELOPE * env).astype(int)
            run = np.convolve(out, np.ones(hold, dtype=int), mode="valid")
            hit = np.flatnonzero(run == hold)
            if hit.size:
                respond = float(hit[0] * inc * 1e3)

        ann = COND.get("annotation", {}).get(c.name, {})
        rows.append(dict(
            name=c.name,
            unit=str(c.properties.get("unit_string", "")).strip(),
            median=med, spread=mad, lo=lo, hi=hi, drift=drift,
            at_floor=at_floor, frozen=frozen, uniq=uniq,
            shift=shift, respond_ms=respond,
            responds=math.isfinite(respond),
            suspect=math.isfinite(respond) and respond < SUSPECT_MS,
            on=ann.get("on", ""), note=ann.get("note", ""),
        ))

    # The ignitor spike as a wall-clock moment, for comparison with any log the
    # control system wrote. The spike is found in the PRESSURE record, so it is
    # that file's start time it has to be added to.
    spark_wall = pt0 + dt.timedelta(seconds=spark_pt)
    events = read_event_log(shot, spark_wall)
    if events is not None:
        events["shot"] = shot.name

    return dict(shot=shot.name, rows=rows, spark_s=spark, record_s=dur,
                window=(GUARD_S, pre_end), post_ms=post_ms, settle_s=settle,
                settle_shrunk=settle < COND.get("settle_s", 1.0),
                group=COND["group"], file=files[0].name,
                spark_wall=spark_wall, events=events)


_CLOCK_FORMATS = ("%d/%m/%Y %H:%M:%S.%f", "%d/%m/%Y %H:%M:%S",
                  "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S",
                  "%H:%M:%S.%f", "%H:%M:%S")


def _clock(text: str, ref: dt.datetime):
    """Parse a timestamp, borrowing `ref`'s date when the log gives only a time."""
    text = text.strip()
    for f in _CLOCK_FORMATS:
        try:
            v = dt.datetime.strptime(text, f)
        except ValueError:
            continue
        if v.year == 1900:              # the format carried no date
            v = v.replace(year=ref.year, month=ref.month, day=ref.day)
        return v
    return None


def _fields(line: str) -> list:
    """Split a row. The logs seen here separate with a comma, a tab, or both."""
    return [f.strip() for f in re.split(r"[,\t]+", line.strip())]


def read_event_log(shot: Path, spark_wall: dt.datetime) -> dict | None:
    """The control system's record of how the shot was set up.

    Which valves were open, and how long a spray had been running — none of it
    is in the TDMS, so without this file the only surviving record of a shot's
    configuration is the folder name, which states the intent rather than what
    happened.

    The log's own ignition timestamp is compared against the ignitor spike found
    in the pressure record, and the two are usually not the same moment. On this
    rig they sit a whole number of hours apart, because the control system logs
    local time and the DAQ logs UTC; nothing in either file says so. That is why
    the comparison is made and reported rather than assumed away.
    """
    if not EVENTS:
        return None
    files = sorted({f for g in EVENTS["glob"] for f in shot.glob(g)})
    if not files:
        return dict(file="", problem="no file here matches "
                    + " or ".join(repr(g) for g in EVENTS["glob"])
                    + ". Which valves were open and how long any spray ran is "
                      "recorded nowhere else, so for this shot it is simply unknown.")
    path = files[0]
    lines = [l for l in path.read_text(encoding="utf-8-sig").splitlines() if l.strip()]
    if not lines:
        return dict(file=path.name, problem="the file is empty")

    flags, leads = [], []
    if len(lines) == 1:
        # Older logs are a bare timestamp with no header at all.
        ign = _clock(_fields(lines[0])[-1] if len(_fields(lines[0])) == 1
                     else lines[0], spark_wall)
        if ign is None:
            ign = _clock(lines[0], spark_wall)
        note = ("no column headings — an older log format that records only the "
                "ignition time, so nothing is known here about valves or timing")
    else:
        head, row = _fields(lines[0]), _fields(lines[1])
        note = ""
        if len(head) != len(row):
            return dict(file=path.name,
                        problem=f"{len(head)} column headings but {len(row)} values "
                                "in the data row — the columns cannot be matched up "
                                "safely, so nothing is read from it")
        rec = dict(zip(head, row))
        tcol = EVENTS["time_column"]
        if tcol not in rec:
            return dict(file=path.name,
                        problem=f"no '{tcol}' column; the rig file names that as the "
                                f"ignition time. Columns present: {', '.join(head)}")
        ign = _clock(rec[tcol], spark_wall)
        for col in EVENTS["flag_columns"]:
            if col in rec:
                flags.append(dict(column=col, label=EVENTS["label"].get(col, col),
                                  on=rec[col].strip().upper() in ("TRUE", "1", "YES", "ON"),
                                  raw=rec[col].strip()))
        for col in EVENTS["lead_columns"]:
            if col in rec:
                when = _clock(rec[col], spark_wall)
                leads.append(dict(column=col, label=EVENTS["label"].get(col, col),
                                  when=when, raw=rec[col].strip(),
                                  lead_s=(ign - when).total_seconds()
                                  if when and ign else float("nan")))

    if ign is None:
        return dict(file=path.name,
                    problem="the ignition timestamp could not be parsed; recognised "
                            "formats are " + ", ".join(_CLOCK_FORMATS))

    # Wrap into ±12 h so a date rollover does not masquerade as a day's error.
    off = ((spark_wall - ign).total_seconds() + 43200) % 86400 - 43200
    whole_h = round(off / 3600)
    return dict(file=path.name, ignition=ign, flags=flags, leads=leads, note=note,
                offset_s=off, whole_hours=whole_h, residual_s=off - whole_h * 3600,
                problem="")


def write_events_csv(ev: dict, path: Path) -> Path:
    """One row per fact, so a log with different columns still exports cleanly."""
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["shot", "item", "value", "seconds_before_ignition", "note"])
        shot = ev.get("shot", "")
        if ev.get("problem"):
            w.writerow([shot, "problem", "", "", ev["problem"]])
            return path
        w.writerow([shot, "ignition_time_logged",
                    ev["ignition"].strftime("%Y-%m-%d %H:%M:%S.%f")[:-3], "",
                    ev.get("note", "")])
        for f in ev["flags"]:
            w.writerow([shot, f["label"], "TRUE" if f["on"] else "FALSE", "",
                        f"column '{f['column']}'"])
        for l in ev["leads"]:
            w.writerow([shot, l["label"], l["raw"],
                        "" if l["lead_s"] != l["lead_s"] else f"{l['lead_s']:.3f}",
                        f"column '{l['column']}'"])
        w.writerow([shot, "measured_spark_minus_logged_time", f"{ev['offset_s']:.3f}",
                    "", "whole-hour part "
                    f"{ev['whole_hours']:+d} h, residual {ev['residual_s']:+.3f} s; "
                    "the logged time is not usable as t = 0"])
    return path


def write_conditions_csv(res: dict, path: Path) -> Path:
    """One row per channel. `usable` carries the pre-ignition-only restriction into
    the file, so it survives being opened by someone who did not see the warning
    the command printed."""
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["shot", "channel", "unit", "value", "spread", "pre_min",
                    "pre_max", "distinct_values", "drift", "settle_window_s",
                    "window_from_s", "window_to_s", "post_ignition_shift",
                    "responds_after_ms", "usable", "mounted_on", "note"])
        for r in res["rows"]:
            if r["suspect"]:
                usable = ("pre-ignition only — sustained response too fast to be "
                          "the process")
            elif r["responds"]:
                usable = "pre-ignition; changes after ignition, interpret with care"
            else:
                usable = "pre-ignition; no sustained change after ignition"
            if r["frozen"]:
                usable = ("NOT READING — one value for the whole window; ") + usable
            elif r["at_floor"]:
                usable = (f"at the bottom of its span but the input is live "
                          f"({r['uniq']} distinct values), so this reads as a true "
                          f"zero — whether the instrument itself is spanned is "
                          f"untested; ") + usable
            w.writerow([
                res["shot"], r["name"], r["unit"],
                f"{r['median']:.6g}", f"{r['spread']:.4g}",
                f"{r['lo']:.6g}", f"{r['hi']:.6g}", r["uniq"],
                "" if r["drift"] != r["drift"] else f"{r['drift']:.4g}",
                f"{res['settle_s']:.3f}",
                f"{res['window'][0]:.3f}", f"{res['window'][1]:.3f}",
                "" if r["shift"] != r["shift"] else f"{r['shift']:.4g}",
                "" if not r["responds"] else f"{r['respond_ms']:.2f}",
                usable, r["on"], r["note"],
            ])
    return path
