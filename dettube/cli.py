"""Command-line entry points.

The user-facing text lives in OVERVIEW and in each subparser's description and
epilog below, not here: this docstring is for whoever maintains the file, and
the two audiences want different things said.

Each subcommand also installs as its own console script — `dettube-plot`,
`dettube-velocity`, `dettube-conditions`, `dettube-export` — for anyone who
prefers that.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from . import __version__
from .analysis import analyse, analyse_pdt
from .conditions import (SUSPECT_MS, decimals, fmt, read_conditions,
                         write_conditions_csv, write_events_csv)
from .core import RIG, SENSORS, choose_folder, find_spark, load_rig, read_group
from .export import export_raw, export_results
from .plots import plot_stations, stack_figure, velocity_figure
from .rig import TEMPLATE


def _resolve(shot_dir: Path | None) -> Path:
    shot = shot_dir or choose_folder()
    if shot is None:
        raise SystemExit("No folder chosen.")
    if not shot.is_dir():
        raise SystemExit(f"Not a folder: {shot}")
    return shot


def cmd_rig(args) -> int:
    if args.template:
        out = Path(args.template)
        if out.exists() and not args.force:
            raise SystemExit(f"{out} exists — pass --force to overwrite")
        out.write_text(TEMPLATE, encoding="utf-8")
        print(f"Wrote {out}\n"
              f"Edit it for your tube, then either pass --rig {out} or set\n"
              f"  export DETTUBE_RIG={out.resolve()}")
        return 0
    r = load_rig(args.rig)
    print(f"\n{r['name']}\n  from {r['path']}\n  x measured from {r['origin']}\n")
    print(f"  {'station':<12}{'x':>8}   channels")
    for odd, even, x, name in r["pairs"]:
        print(f"  {name:<12}{x:>8.2f}   odd {odd:02d} / even {even:02d}")
    print(f"\n  sensor groups: " + ", ".join(
        f"{k} ({v['prefix']}, {v['unit']})" for k, v in r["sensors"].items()))
    print(f"  raw-export groups: " + ", ".join(g[0] for g in r["groups"]))
    return 0


def cmd_plot(args) -> int:
    load_rig(args.rig)
    shot = _resolve(args.shot_dir)
    if args.per_station:
        print(f"\nReading {shot}  (one figure per station)")
        files = plot_stations(shot, args.show)
        print(f"\nSaved {len(files)} files:")
        for f in files:
            print(f"  {f.name}")
        return 0
    print(f"\nReading {shot}  ({SENSORS[args.sensor]['label']})")
    out = stack_figure(shot, args.sensor, args.gain, args.t0, args.t1, args.show)
    print(f"\nSaved {out}")
    return 0


def cmd_velocity(args) -> int:
    load_rig(args.rig)
    shot = _resolve(args.shot_dir)
    cfg = SENSORS[args.sensor]
    pt_files = sorted(shot.glob(SENSORS["pt"]["glob"]))
    if not pt_files:
        raise SystemExit(f"No '{SENSORS['pt']['glob']}' in {shot} — "
                         "it is needed to locate the ignitor spike")
    if not sorted(shot.glob(cfg["glob"])):
        raise SystemExit(f"No '{cfg['glob']}' in {shot}")

    print(f"\nReading {shot}  ({cfg['front']} front)")
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        pg, pinc, pt0 = read_group(pt_files[0], SENSORS["pt"]["group"], work)
        spark_pt = find_spark(pg, pinc)
        if args.sensor == "pt":
            res = analyse(pg, pinc, spark_pt)
        else:
            g, inc, gt0 = read_group(sorted(shot.glob(cfg["glob"]))[0],
                                     cfg["group"], work)
            res = analyse_pdt(g, inc, spark_pt + (pt0 - gt0).total_seconds())

    print(f"  ignitor spike at {spark_pt * 1e3:.2f} ms into the PT record\n")
    for note in res["notes"]:
        print(f"  ! {note}")
    if res["notes"]:
        print()
    print(f"  {'station':<10}{'x (m)':>7}{'channel':>10}{'arrival (ms)':>14}"
          f"{'peak (' + cfg['unit'] + ')':>14}")
    for x, _ in res["points"]:
        r = res["per"][x]
        tag = f"{cfg['prefix']}-{r['ch']:02d}"
        print(f"  {r['station']:<10}{x:>7.2f}{tag:>10}{r['arrival']:>14.2f}"
              f"{r['peak']:>14.3f}")
    print(f"\n  {'segment':<16}{'dt (ms)':>9}{'velocity (m/s)':>16}")
    for s in res["segments"]:
        v = "  n/a" if not s["v"] else f"{s['v']:.0f}"
        seg = f"{s['x1']:.2f} -> {s['x2']:.2f} m"
        print(f"  {seg:<16}{s['dt_ms']:>9.2f}{v:>16}")
    if res["fit"]:
        print(f"\n  straight-line fit {res['fit']:.0f} m/s   R² {res['r2']:.3f}")
    if not res["monotonic"]:
        print("\n  ** CAUSALITY WARNING: a downstream station is timed BEFORE an "
              "upstream one.\n     One pick is wrong — look at the raw traces "
              "with `dettube plot`. **")
    for w in res.get("warnings", []):
        print(f"\n  ** {w} **")

    out = velocity_figure(shot, res, args.sensor, spark_pt * 1e3, args.show)
    print(f"\nSaved {out}")
    return 0


def cmd_conditions(args) -> int:
    load_rig(args.rig)
    shot = _resolve(args.shot_dir)
    print(f"\nReading {shot}")
    with tempfile.TemporaryDirectory() as tmp:
        res = read_conditions(shot, Path(tmp))

    a, b = res["window"]
    print(f"\nPre-ignition conditions — {res['shot']}")
    print(f"  '{res['group']}' in {res['file']}")
    print(f"  ignitor spike {res['spark_s']:.2f} s into a {res['record_s']:.2f} s record")
    print(f"  averaged over {a:.2f} – {b:.2f} s before it "
          f"({b - a:.2f} s); {res['post_ms']:.0f} ms recorded after")
    if res["settle_shrunk"]:
        print(f"  drift windows shortened to {res['settle_s']:.2f} s to fit")

    print(f"\n  {'channel':<16}{'pre-ignition value':<24}{'pre range':<20}"
          f"{'drift':>9}   after ignition")
    for r in res["rows"]:
        name = r["name"] + ("  !" if r["frozen"] else " *" if r["at_floor"] else "")
        val = fmt(r["median"], r["spread"]) + (f" {r['unit']}" if r["unit"] else "")
        d = decimals(r["spread"])
        rng = f"{r['lo']:.{d}f} .. {r['hi']:.{d}f}"
        dv = r["drift"]
        # Round a drift that is zero to the printed precision to exactly zero,
        # so a settled channel never shows as "-0.00".
        dv = 0.0 if dv == dv and abs(dv) < 0.5 * 10 ** -d else dv
        drift = "      n/a" if dv != dv else f"{dv:+.{d}f}"
        if not r["responds"]:
            after = "no sustained change"
        else:
            after = (f"{r['shift']:+.{d}f} from {r['respond_ms']:.1f} ms"
                     + ("   TOO FAST" if r["suspect"] else ""))
        print(f"  {name:<16}{val:<24}{rng:<20}{drift:>9}   {after}")

    print(f"\n  value ± median absolute deviation, which ignores the spikes the "
          f"range shows;\n  drift = change over the last {res['settle_s']:.1f} s "
          f"against the {res['settle_s']:.1f} s before that, so near zero\n  means "
          "filling had finished. Quote each value to its spread, not to the digits\n"
          "  the DAQ prints.")

    floored = [r for r in res["rows"] if r["at_floor"] and not r["frozen"]]
    if floored:
        n = min(r["uniq"] for r in floored)
        print(f"\n  * sits at the bottom of its span. The input is still being "
              f"digitised — it\n    dithers over {n} or more distinct values — so the "
              "reading really is at that\n    floor and the channel has not stopped. "
              "Whether the instrument behind it is\n    powered and spanned is a "
              "separate question this record cannot answer.")

    stuck = [r["name"] for r in res["rows"] if r["frozen"]]
    if stuck:
        print(f"\n  ! NOT READING: {', '.join(stuck)} — one value for the entire "
              "window, with no\n    dither at all. That is a channel that is not "
              "being digitised, and its\n    value means nothing.")

    fast = [r["name"] for r in res["rows"] if r["suspect"]]
    if fast:
        print(f"\n  ** Sustained response within {SUSPECT_MS:.0f} ms of the ignitor "
              f"spike: {', '.join(fast)}.\n     No analyser or sheathed thermocouple "
              "responds that fast. This is the ignitor's\n     electrical pickup or a "
              "wiring fault, not the process — use the channel's\n     pre-ignition "
              "value only, and disregard what it reads afterwards. **")

    noted = [r for r in res["rows"] if r["on"] or r["note"]]
    if noted:
        print("\n  From the rig file:")
        for r in noted:
            where = f" (on {r['on']})" if r["on"] else ""
            print(f"    {r['name']}{where}"
                  + (f"\n      {r['note']}" if r["note"] else ""))

    _print_events(res)

    if args.csv is not None:
        out = Path(args.csv)
        if out.is_dir():
            out = out / f"{res['shot']}_conditions.csv"
        out.parent.mkdir(parents=True, exist_ok=True)
        write_conditions_csv(res, out)
        print(f"\nSaved {out}")
        ev = res.get("events")
        if ev:
            # A sibling file rather than extra rows: one fact per row does not fit
            # the one-channel-per-row shape of the other file without warping both.
            sib = out.with_name(out.stem.replace("_conditions", "") + "_events.csv")
            write_events_csv(ev, sib)
            print(f"Saved {sib}")
    return 0


def _print_events(res) -> None:
    ev = res.get("events")
    if ev is None:
        return
    print(f"\nHow the shot was set up — {ev['file']}")
    if ev["problem"]:
        print(f"  ** This log could not be read: {ev['problem']} **")
        return

    print(f"  ignition logged at   {ev['ignition'].strftime('%H:%M:%S.%f')[:-3]}")
    for f in ev["flags"]:
        print(f"  {('OPEN' if f['on'] else 'shut'):<7} {f['label']}")
    for l in ev["leads"]:
        lead = ("" if l["lead_s"] != l["lead_s"]
                else f"   {l['lead_s']:.2f} s before ignition")
        print(f"  {l['label']} opened at {l['raw']}{lead}")
    if ev["note"]:
        print(f"  ({ev['note']})")

    h, r = ev["whole_hours"], ev["residual_s"]
    if h:
        print(f"\n  ** This log and the DAQ are on clocks {abs(h)} h apart — the "
              f"logged\n     ignition time reads {ev['ignition'].strftime('%H:%M:%S')} "
              f"while the measured spike is at "
              f"{res['spark_wall'].strftime('%H:%M:%S')}.\n     One is local time and "
              "the other UTC. Nothing in either file says so, so\n     anything lining "
              f"the two up without allowing for it is out by {abs(h)} hours. **")
    print(f"\n  Past that, the logged time is {abs(r):.2f} s "
          f"{'early' if r > 0 else 'late'} against the ignitor spike\n"
          "  measured in the pressure record, which is why the spike and not this\n"
          "  timestamp is used as t = 0.")


def cmd_export(args) -> int:
    load_rig(args.rig)
    # Checked before any file is read, so a typo costs a second rather than the
    # minutes it takes to decode four TDMS groups.
    if args.every < 1:
        raise SystemExit(f"--every {args.every} makes no sense: it is how many "
                         "samples to step by,\n  so the smallest useful value is 1 "
                         "(keep everything).")
    if args.raw and not args.full and args.t0 >= args.t1:
        raise SystemExit(
            f"The time window runs backwards: --from-ms {args.t0:g} is not before "
            f"--to-ms {args.t1:g}.\n  No samples fall inside it, so the raw files "
            "would come out empty.")
    shot = _resolve(args.shot_dir)
    outdir = args.outdir or (shot / "csv")
    outdir.mkdir(parents=True, exist_ok=True)
    print(f"\nReading {shot}")
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        files, spark_pt, pt0 = export_results(shot, outdir, work)
        print(f"  ignitor spike at {spark_pt * 1e3:.2f} ms into the PT record")
        if args.raw:
            win = None if args.full else (args.t0, args.t1)
            where = "whole record" if args.full else f"{args.t0:g} to {args.t1:g} ms"
            print(f"\n  raw samples, {where}"
                  + (f", every {args.every}th sample" if args.every > 1 else ""))
            files += export_raw(shot, outdir, work, spark_pt, pt0, win,
                                max(1, args.every))
    print(f"\nSaved {len(files)} files to {outdir}")
    return 0


OVERVIEW = """\
Read a detonation-tube shot recorded as LabVIEW TDMS: plot every channel, pick
the pressure and flame fronts, report what was in the tube beforehand, and write
it all out as CSV.

START HERE
  Nothing about any tube is built in, so the first thing to do is point dettube
  at a rig file describing yours. If your lab already has one, name it once:

      export DETTUBE_RIG=/path/to/yourrig.toml      (Windows: set DETTUBE_RIG=...)

  and every command below finds it. If you need to make one:

      dettube rig --template yourrig.toml           write a commented example
      <edit it: station positions, channel numbers>
      dettube rig --rig yourrig.toml                check it reads back correctly

THEN, on a shot folder — the one holding that shot's .tdms files
      dettube conditions SHOT      what the instruments read before ignition
      dettube plot SHOT            every pressure channel, stacked by position
      dettube velocity SHOT        arrival times and segment velocities
      dettube export SHOT --raw    CSVs of the results and the raw samples

  Leave SHOT off any of them to get a folder picker instead.
"""

EPILOG = """\
examples
  dettube conditions "D:\\shots\\03.100926"
  dettube plot "D:\\shots\\03.100926" --sensor pdt
  dettube velocity "D:\\shots\\03.100926" --show
  dettube export "D:\\shots\\03.100926" --raw --every 10

  dettube plot            pick the folder from a dialog

Run `dettube <command> --help` for what each one does and what it writes.
Full documentation: https://github.com/the-s-lab/dettube
"""

RIG_HELP = ("rig file describing the tube. Default: $DETTUBE_RIG, then "
            "./rig.toml, then ~/.config/dettube/rig.toml")
SHOT_HELP = "folder holding this shot's .tdms files (omit it for a folder picker)"


def _add_shot(p):
    p.add_argument("shot_dir", nargs="?", type=Path, metavar="SHOT", help=SHOT_HELP)
    p.add_argument("--rig", type=Path, default=None, metavar="FILE", help=RIG_HELP)


def _sub(sub, name, help_, description, epilog):
    return sub.add_parser(name, help=help_, description=description, epilog=epilog,
                          formatter_class=argparse.RawDescriptionHelpFormatter)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="dettube", description=OVERVIEW, epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=f"dettube {__version__}")
    sub = ap.add_subparsers(dest="cmd", metavar="COMMAND")

    p = _sub(sub, "plot", "channel stacks, or one figure per station",
             "Every channel drawn at its own position along the tube, so a front "
             "shows up as a\ndiagonal march down the page and a faulty gauge as the "
             "one trace out of step.\nOdd and even channels are coloured "
             "differently: a fault affecting one side of\nthe tube then reads as a "
             "pattern rather than as one odd-looking station.\n\nWrites a PNG beside "
             "the data.",
             "examples\n"
             "  dettube plot SHOT                     pressure, all channels\n"
             "  dettube plot SHOT --sensor pdt        photodiodes instead\n"
             "  dettube plot SHOT --per-station       one PNG per station, both "
             "sensors\n"
             "  dettube plot SHOT --from-ms 0 --to-ms 20     zoom on the first 20 ms\n")
    _add_shot(p)
    p.add_argument("--sensor", choices=("pt", "pdt"), default="pt",
                   help="pt = pressure transmitters (default), pdt = photodiodes")
    p.add_argument("--per-station", action="store_true",
                   help="one PNG per station with pressure and light together, "
                        "instead of one stacked figure")
    p.add_argument("--gain", type=float, default=None, metavar="G",
                   help="vertical scale: metres of offset per unit. Raise it to "
                        "separate crowded traces, lower it if they overlap")
    p.add_argument("--from-ms", type=float, default=None, dest="t0", metavar="MS",
                   help="start of the time window, ms from the ignitor spike "
                        "(default: from the rig file)")
    p.add_argument("--to-ms", type=float, default=None, dest="t1", metavar="MS",
                   help="end of the time window, ms from the ignitor spike")
    p.add_argument("--show", action="store_true",
                   help="open the figure in a window as well as saving it")
    p.set_defaults(func=cmd_plot)

    p = _sub(sub, "velocity", "arrival times and segment velocities",
             "When the front reached each station, and how fast it travelled "
             "between them.\n\nt = 0 is the ignitor's own electrical spike in the "
             "pressure record, not any\ntimestamp written by the control software — "
             "those have been seconds out.\n\nPicks that cannot be right are called "
             "out rather than reported quietly: a\nstation timed before one upstream "
             "of it breaks causality and is flagged, as is\na speed too high to be "
             "the flame it claims to be. Prints two tables and writes\na PNG.",
             "examples\n"
             "  dettube velocity SHOT                 pressure front\n"
             "  dettube velocity SHOT --sensor pdt    flame front, from the "
             "photodiodes\n")
    _add_shot(p)
    p.add_argument("--sensor", choices=("pt", "pdt"), default="pt",
                   help="pt = pressure front (default), pdt = flame front")
    p.add_argument("--show", action="store_true",
                   help="open the figure in a window as well as saving it")
    p.set_defaults(func=cmd_velocity)

    p = _sub(sub, "conditions", "what the instruments read before ignition",
             "What was actually in the tube, read from the instruments rather than "
             "from the\nfolder name — folder names record the mixture that was aimed "
             "at, and the two\nhave differed by over a percent.\n\nEach channel gets "
             "its pre-ignition value, the spread around it, and the full\nrange, so "
             "impulsive noise stays visible instead of being averaged away. "
             "Channels\nthat drift before the shot, sit dead at the end of their "
             "range, or respond\nfaster than any real instrument could are each "
             "flagged.\n\nIf the rig file names a control-system log, that is read "
             "too: which valves were\nopen, and how long before ignition.",
             "examples\n"
             "  dettube conditions SHOT               print the table\n"
             "  dettube conditions SHOT --csv out/    also write two CSVs there\n")
    _add_shot(p)
    p.add_argument("--csv", nargs="?", const=".", default=None, metavar="FILE",
                   help="also write <shot>_conditions.csv and <shot>_events.csv. "
                        "Give a folder or a filename; bare --csv means here")
    p.set_defaults(func=cmd_conditions)

    p = _sub(sub, "rig", "show the rig in use, or write a template",
             "Every other command needs to be told where the gauges are. This one "
             "shows the\nrig currently in effect, or writes a commented example to "
             "start from.\n\nKeep rig files out of shared repositories if their "
             "dimensions come from drawings\nunder confidentiality — point dettube at "
             "one with $DETTUBE_RIG instead.",
             "examples\n"
             "  dettube rig                           show the rig now in effect\n"
             "  dettube rig --template myrig.toml     write a commented example\n"
             "  dettube rig --rig myrig.toml          read that one back to check "
             "it\n")
    p.add_argument("--rig", type=Path, default=None, metavar="FILE", help=RIG_HELP)
    p.add_argument("--template", nargs="?", const="rig.toml", default=None,
                   metavar="FILE",
                   help="write a commented example rig file and stop "
                        "(default name: rig.toml)")
    p.add_argument("--force", action="store_true",
                   help="overwrite the file if --template would replace one")
    p.set_defaults(func=cmd_rig)

    p = _sub(sub, "export", "write CSVs of results and raw samples",
             "The picked arrivals and velocities as CSV, and with --raw the samples "
             "they came\nfrom. Every raw file carries a t_ms column measured from the "
             "ignitor spike, so\nthe groups line up with each other despite being "
             "separate DAQ tasks with\ndifferent start times.\n\nRaw exports get "
             "large — a 16-channel photodiode record at 100 kHz is well over\n100 MB "
             "written whole. The default window is the event; --full and --every "
             "change\nthat. Each file's size is printed as it is written.",
             "examples\n"
             "  dettube export SHOT                   results only, into SHOT/csv/\n"
             "  dettube export SHOT --raw             + raw samples around the event\n"
             "  dettube export SHOT --raw --every 10  + raw, decimated 10:1\n"
             "  dettube export SHOT --raw --full      + every sample (large)\n")
    _add_shot(p)
    p.add_argument("--raw", action="store_true",
                   help="also write the raw samples, one CSV per DAQ group")
    p.add_argument("--full", action="store_true",
                   help="with --raw, write the whole record instead of just the "
                        "window around the event")
    p.add_argument("--every", type=int, default=1, metavar="N",
                   help="with --raw, keep every Nth sample to cut the file size")
    p.add_argument("--from-ms", type=float, default=-20.0, dest="t0", metavar="MS",
                   help="with --raw, start of the window, ms from the spike "
                        "(default: -20)")
    p.add_argument("--to-ms", type=float, default=120.0, dest="t1", metavar="MS",
                   help="with --raw, end of the window, ms from the spike "
                        "(default: 120)")
    p.add_argument("--outdir", type=Path, default=None, metavar="DIR",
                   help="where to write (default: a csv/ folder beside the data)")
    p.set_defaults(func=cmd_export)
    return ap


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # Bare `dettube` should teach rather than scold. argparse's own answer to a
    # missing subcommand is a usage line and exit code 2, which tells a first-time
    # user nothing about what the tool is for or that it needs a rig file first.
    if getattr(args, "cmd", None) is None:
        parser.print_help()
        return 0
    return args.func(args)


def main_plot(argv=None) -> int:
    return main(["plot"] + list(argv if argv is not None else sys.argv[1:]))


def main_velocity(argv=None) -> int:
    return main(["velocity"] + list(argv if argv is not None else sys.argv[1:]))


def main_export(argv=None) -> int:
    return main(["export"] + list(argv if argv is not None else sys.argv[1:]))


def main_conditions(argv=None) -> int:
    return main(["conditions"] + list(argv if argv is not None else sys.argv[1:]))


if __name__ == "__main__":
    sys.exit(main())
