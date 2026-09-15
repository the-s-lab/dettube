#!/usr/bin/env python3
"""CSV export: processed results always, raw samples on request.

    python dettube_export.py                              # folder picker, results only
    python dettube_export.py "path/to/shot"
    python dettube_export.py "path/to/shot" --raw         # + raw samples, event window
    python dettube_export.py "path/to/shot" --raw --full  # + raw samples, whole record
    python dettube_export.py "path/to/shot" --raw --every 10   # decimate 10:1

Install once:
    pip install npTDMS numpy

What you get
------------
Always, in a `csv/` folder beside the data:

    <shot>_arrivals.csv    one row per station per sensor: the channel used, its
                           arrival time, peak, and whether it was dropped and why
    <shot>_velocities.csv  one row per segment per sensor: length, transit time,
                           velocity, plus the straight-line fit and its warnings

With `--raw`, additionally one file per DAQ group:

    <shot>_raw_PT.csv      pressure, 51.2 kHz
    <shot>_raw_PDT.csv     photodiodes, 100 kHz
    <shot>_raw_Mixed.csv   gas mixing, 25 kHz
    <shot>_raw_Pitot.csv   pitot, 25 kHz

Each raw file carries a `t_ms` column measured from the ignitor spike, so the
four groups line up with each other despite being separate DAQ tasks with
different start times.

A word on size
--------------
The photodiode record is 16 channels at 100 kHz. Written whole, that is roughly
700 000 rows and well over 100 MB as text. The default window is the 120 ms
around the event, which is all anyone has needed so far; `--full` overrides it
and `--every N` decimates. The size of each file is printed as it is written.
"""
from __future__ import annotations

import csv
import tempfile
from pathlib import Path

import numpy as np

from .analysis import analyse, analyse_pdt
from .core import GROUPS, RIG, SENSORS, find_spark, read_group

DEFAULT_WIN_MS = (-20.0, 120.0)


def human(n: int) -> str:
    for unit in ("B", "kB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n / 1:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def export_results(shot: Path, outdir: Path, work: Path) -> list[Path]:
    pt_files = sorted(shot.glob(SENSORS["pt"]["glob"]))
    if not pt_files:
        raise SystemExit(f"No '{SENSORS['pt']['glob']}' in {shot}")
    pg, pinc, pt0 = read_group(pt_files[0], SENSORS["pt"]["group"], work)
    spark_pt = find_spark(pg, pinc)

    results = {"PT": analyse(pg, pinc, spark_pt)}
    pdt_files = sorted(shot.glob(SENSORS["pdt"]["glob"]))
    if pdt_files:
        dg, dinc, dt0 = read_group(pdt_files[0], SENSORS["pdt"]["group"], work)
        results["PDT"] = analyse_pdt(dg, dinc, spark_pt + (pt0 - dt0).total_seconds())

    a_path = outdir / f"{shot.name}_arrivals.csv"
    with open(a_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["shot", "sensor", "station", "x_m", "channel", "arrival_ms",
                    "peak", "unit", "status", "note", "rig", "rig_fingerprint"])
        for sensor, res in results.items():
            used = {x for x, _ in res["points"]}
            for x, r in sorted(res["per"].items()):
                w.writerow([shot.name, sensor, r["station"], f"{x:.2f}",
                            f"{sensor}-{r['ch']:02d}", f"{r['arrival']:.3f}",
                            f"{r['peak']:.4f}", SENSORS[sensor.lower()]["unit"],
                            "used" if x in used else "dropped", "",
                            RIG.get("name", ""), RIG.get("fingerprint", "")])
            for note in res["notes"]:
                w.writerow([shot.name, sensor, "", "", "", "", "", "", "note", note,
                            RIG.get("name", ""), RIG.get("fingerprint", "")])
    print(f"  wrote {a_path.name}  ({human(a_path.stat().st_size)})")

    v_path = outdir / f"{shot.name}_velocities.csv"
    with open(v_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["shot", "sensor", "from_m", "to_m", "length_m", "dt_ms",
                    "velocity_mps", "fit_mps", "fit_r2", "causally_ordered",
                    "warning", "rig", "rig_fingerprint"])
        for sensor, res in results.items():
            warn = " | ".join(res.get("warnings", [])).replace("\n", " ")
            for s in res["segments"]:
                w.writerow([shot.name, sensor, f"{s['x1']:.2f}", f"{s['x2']:.2f}",
                            f"{s['x2'] - s['x1']:.2f}", f"{s['dt_ms']:.3f}",
                            "" if not s["v"] else f"{s['v']:.1f}",
                            "" if res["fit"] is None else f"{res['fit']:.1f}",
                            "" if res["r2"] is None else f"{res['r2']:.4f}",
                            res["monotonic"], warn,
                            RIG.get("name", ""), RIG.get("fingerprint", "")])
    print(f"  wrote {v_path.name}  ({human(v_path.stat().st_size)})")
    return [a_path, v_path], spark_pt, pt0


def export_raw(shot: Path, outdir: Path, work: Path, spark_pt: float, pt0,
               win: tuple[float, float] | None, every: int) -> list[Path]:
    out = []
    for tag, pattern, group_name in GROUPS:
        files = sorted(shot.glob(pattern))
        if not files:
            print(f"  {tag}: no file, skipped")
            continue
        try:
            g, inc, t0 = read_group(files[0], group_name, work)
        except ValueError as exc:
            print(f"  {tag}: {exc} — skipped")
            continue
        # Each group is its own DAQ task with its own start time; convert the
        # spike onto this group's clock so the four files share a time base.
        spark = spark_pt + (pt0 - t0).total_seconds()
        dur = min(len(c) for c in g.channels() if c.name != "Time[s]") * inc
        if not 0 <= spark <= dur:
            print(f"  {tag}: the ignitor spike falls at {spark * 1e3:.0f} ms but this "
                  f"record is only {dur * 1e3:.0f} ms long — skipped")
            continue

        chans = [c for c in g.channels() if c.name != "Time[s]"]
        if not chans:
            print(f"  {tag}: no data channels, skipped")
            continue
        n = min(len(c) for c in chans)
        short = [c.name for c in chans if len(c) != n]
        t = np.arange(n) * inc
        if win is None:
            mask = np.ones(n, dtype=bool)
        else:
            mask = (t >= spark + win[0] / 1e3) & (t <= spark + win[1] / 1e3)
        idx = np.flatnonzero(mask)[::every]
        if idx.size == 0:
            print(f"  {tag}: nothing inside the window, skipped")
            continue

        data = [c[:][:n][idx].astype(float) for c in chans]
        tm = (t[idx] - spark) * 1e3
        path = outdir / f"{shot.name}_raw_{tag}.csv"
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["t_ms"] + [c.name for c in chans])
            for i in range(idx.size):
                w.writerow([f"{tm[i]:.4f}"] + [f"{d[i]:.6g}" for d in data])
        note = f"  (trimmed to {n} samples; shorter: {', '.join(short)})" if short else ""
        print(f"  wrote {path.name}  ({human(path.stat().st_size)}, "
              f"{idx.size} rows × {len(chans)} ch){note}")
        out.append(path)
    return out


