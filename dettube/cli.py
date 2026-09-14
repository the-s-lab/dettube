"""Command-line entry points.

    dettube plot        <shot> [--sensor pt|pdt] [--per-station]
    dettube velocity    <shot> [--sensor pt|pdt]
    dettube conditions  <shot> [--csv FILE]
    dettube export      <shot> [--raw] [--full] [--every N]

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
        print(f"  {f'{s[chr(120)+chr(49)]:.2f} -> {s[chr(120)+chr(50)]:.2f} m':<16}"
              f"{s['dt_ms']:>9.2f}{v:>16}")
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


def _add_shot(p):
    p.add_argument("shot_dir", nargs="?", type=Path,
                   help="folder holding the shot's .tdms files (omit for a picker)")
    p.add_argument("--rig", type=Path, default=None,
                   help="rig definition (default: $DETTUBE_RIG, ./rig.toml, "
                        "~/.config/dettube/rig.toml)")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="dettube", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=f"dettube {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("plot", help="channel stacks, or one figure per station")
    _add_shot(p)
    p.add_argument("--sensor", choices=("pt", "pdt"), default="pt")
    p.add_argument("--per-station", action="store_true",
                   help="one PNG per station, pressure and light together")
    p.add_argument("--gain", type=float, default=None)
    p.add_argument("--from-ms", type=float, default=None, dest="t0")
    p.add_argument("--to-ms", type=float, default=None, dest="t1")
    p.add_argument("--show", action="store_true")
    p.set_defaults(func=cmd_plot)

    p = sub.add_parser("velocity", help="arrival times and segment velocities")
    _add_shot(p)
    p.add_argument("--sensor", choices=("pt", "pdt"), default="pt")
    p.add_argument("--show", action="store_true")
    p.set_defaults(func=cmd_velocity)

    p = sub.add_parser("conditions",
                       help="what the process instruments read before ignition")
    _add_shot(p)
    p.add_argument("--csv", nargs="?", const=".", default=None, metavar="FILE",
                   help="also write a CSV (a folder is accepted; default ./)")
    p.set_defaults(func=cmd_conditions)

    p = sub.add_parser("rig", help="show the rig in use, or write a template")
    p.add_argument("--rig", type=Path, default=None)
    p.add_argument("--template", nargs="?", const="rig.toml", default=None,
                   metavar="FILE", help="write a documented example rig file")
    p.add_argument("--force", action="store_true", help="overwrite an existing file")
    p.set_defaults(func=cmd_rig)

    p = sub.add_parser("export", help="write CSVs of results and raw samples")
    _add_shot(p)
    p.add_argument("--raw", action="store_true")
    p.add_argument("--full", action="store_true")
    p.add_argument("--every", type=int, default=1, metavar="N")
    p.add_argument("--from-ms", type=float, default=-20.0, dest="t0")
    p.add_argument("--to-ms", type=float, default=120.0, dest="t1")
    p.add_argument("--outdir", type=Path, default=None)
    p.set_defaults(func=cmd_export)
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
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
