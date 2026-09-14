"""Figures: channel stacks, per-station pairs, and the velocity summary."""
from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np

from .analysis import analyse, analyse_pdt
from .core import (CFG, COL_EVEN, COL_ODD, PAIRS, SENSORS, find_spark,
                   read_group, smooth)


def _mpl(show: bool):
    import matplotlib
    if not show:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    return plt


def stack_figure(shot_dir: Path, sensor: str, gain: float | None,
         t0_ms: float | None, t1_ms: float | None, show: bool) -> Path:
    plt = _mpl(show)
    from matplotlib.lines import Line2D

    cfg = SENSORS[sensor]
    gain = cfg["gain"] if gain is None else gain
    t0_ms = cfg["win"][0] if t0_ms is None else t0_ms
    t1_ms = cfg["win"][1] if t1_ms is None else t1_ms
    # Checked after the rig defaults are filled in, because only one of the two
    # may have been given on the command line. An inverted window selects no
    # samples, and the figure it saves is an empty frame — which would overwrite
    # a good one from an earlier run without a word.
    if t0_ms >= t1_ms:
        raise SystemExit(
            f"The time window runs backwards: --from-ms {t0_ms:g} is not before "
            f"--to-ms {t1_ms:g}.\n  Nothing would be plotted, and the empty figure "
            "would overwrite the last good one.")

    files = sorted(shot_dir.glob(cfg["glob"]))
    if not files:
        raise SystemExit(f"No '{cfg['glob']}' in {shot_dir}")
    pt_files = sorted(shot_dir.glob(SENSORS["pt"]["glob"]))
    if not pt_files:
        raise SystemExit(f"No '{SENSORS['pt']['glob']}' in {shot_dir} — "
                         "it is needed to locate the ignitor spike")

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        pg, pinc, pt0 = read_group(pt_files[0], SENSORS["pt"]["group"], work)
        spark_pt = find_spark(pg, pinc)

        if sensor == "pt":
            group, inc, spark = pg, pinc, spark_pt
            skew = 0.0
        else:
            group, inc, gt0 = read_group(files[0], cfg["group"], work)
            skew = (pt0 - gt0).total_seconds()
            spark = spark_pt + skew

        have = {c.name for c in group.channels()}
        nsm = max(1, int(cfg["smooth_ms"] * 1e-3 / inc)) if cfg["smooth_ms"] else 1

        fig, ax = plt.subplots(figsize=(12, 8))

        # Channels in one group are NOT all the same length — on this rig the
        # last pair typically runs a few thousand samples short. Build the time
        # axis and the masks per channel, never once from a reference channel.
        def windows(n: int):
            tt = np.arange(n) * inc
            return (tt, (tt > 0.05) & (tt < spark - 0.05),
                    (tt >= spark + t0_ms / 1e3) & (tt <= spark + t1_ms / 1e3))

        extra = "   glow width" if sensor == "pdt" else ""
        print(f"  {'station':<10}{'x (m)':>7}   {'channel':<9}"
              f"{'peak (' + cfg['unit'] + ')':>14}{'noise/peak':>12}{extra}")
        notes = []
        for a, b, x, name in PAIRS:
            ax.axhline(x, color="#e9e9e9", lw=0.8, zorder=0)
            peaks, usable = {}, {}
            for ch, colour in ((a, COL_ODD), (b, COL_EVEN)):
                cname = f"{cfg['prefix']}-{ch:02d}"
                if cname not in have:
                    continue
                s = group[cname][:].astype(float)
                t, base, win = windows(len(s))
                if base.sum() < 100 or win.sum() < 100:
                    print(f"  {name:<10}{x:>7.2f}   {cname:<9}  record too short — skipped")
                    continue
                tw = (t[win] - spark) * 1e3
                sd = float(s[base].std())
                y = s[win] - s[base].mean()
                if sensor == "pdt":
                    y = smooth(np.abs(y), nsm)
                ax.plot(tw, x + y * gain, lw=0.8, color=colour, alpha=0.9, zorder=3)
                pk_win = (t >= spark + 1.5e-3) & (t <= spark + 0.10)
                peaks[ch] = float(np.abs(s[pk_win] - s[base].mean()).max())
                ratio = sd / peaks[ch] if peaks[ch] > 0 else float("nan")
                extra_col = ""
                if sensor == "pdt":
                    pk_max = y.max() if y.size else 0.0
                    fwhm = int((y > 0.5 * pk_max).sum()) * inc * 1e3 if pk_max > 0 else 0.0
                    usable[ch] = pk_max >= CFG["pdt_min_peak"] and fwhm >= CFG["pdt_min_fwhm_ms"]
                    extra_col = f"{fwhm:>10.1f} ms  {'usable' if usable[ch] else '--'}"
                print(f"  {name:<10}{x:>7.2f}   {cname:<9}{peaks[ch]:>14.3f}"
                      f"{ratio * 100:>11.1f}%{extra_col}")
                if ratio > CFG["noise_frac"]:
                    notes.append(f"{cname} baseline noise is {ratio * 100:.0f}% of its peak")
            if peaks:
                body = " / ".join(f"{cfg['prefix']}-{c:02d} {v:.3g}"
                                  for c, v in sorted(peaks.items())) + f" {cfg['unit']}"
                if sensor == "pt":
                    # Two gauges 150 mm apart on one flange ring should not
                    # differ by more than about a factor of two.
                    lo, hi = min(peaks.values()), max(peaks.values())
                    bad = hi > CFG["pair_ratio"] * max(lo, 1e-9)
                    tag = "   << PAIR DISAGREES" if bad else ""
                else:
                    ok = [c for c in sorted(peaks) if usable.get(c)]
                    bad = not ok
                    tag = ("   << NO USABLE CHANNEL — no broad glow here"
                           if bad else
                           "   flame seen by " +
                           ", ".join(f"{cfg['prefix']}-{c:02d}" for c in ok))
                ax.text(t0_ms + 0.02 * (t1_ms - t0_ms), x + 0.09,
                        f"{name}  {x:.2f} m   " + body + tag,
                        fontsize=7.5, va="bottom",
                        color="#c0392b" if bad else "#555555",
                        bbox=dict(fc="white", ec="none", alpha=0.7, pad=0.5))

        ax.axvline(0, color="0.5", lw=0.9, ls="--")
        odd_ch = ", ".join(f"{a:02d}" for a, _, _, _ in PAIRS)
        even_ch = ", ".join(f"{b:02d}" for _, b, _, _ in PAIRS)
        ax.legend(handles=[
            Line2D([], [], color=COL_ODD, lw=2,
                   label=f"odd channel  ({odd_ch})  ·  {cfg['odd_deg']}"),
            Line2D([], [], color=COL_EVEN, lw=2,
                   label=f"even channel ({even_ch})  ·  {cfg['even_deg']}")],
            loc="upper right", fontsize=8.5, framealpha=0.95)

        trace = ("|signal|, " + f"{cfg['smooth_ms']} ms smoothed"
                 if sensor == "pdt" else "signal")
        sub = (f"vertical gain {gain} m per {cfg['unit']}  ·  trace = {trace}"
               + (f"  ·  PDT clock offset {skew * 1e3:+.1f} ms" if sensor == "pdt" else ""))
        ax.set_title(f"{shot_dir.name}\n{cfg['prefix']} channels at true amplitude, "
                     f"offset by station\n{sub}\n"
                     f"t = 0 at the ignitor spike ({spark_pt * 1e3:.1f} ms into the PT record)",
                     fontsize=10.5)
        ax.set_xlabel("time from ignitor spike (ms)")
        ax.set_ylabel("distance from closed head (m)")
        ax.set_xlim(t0_ms, t1_ms)
        ax.set_ylim(0.3, 8.3)
        ax.grid(alpha=0.25)
        fig.tight_layout()

        if notes:
            print("\n  noise warnings:")
            for n in sorted(set(notes)):
                print(f"  ! {n}")

        out = shot_dir / f"{cfg['prefix']}_stack_{shot_dir.name}.png"
        fig.savefig(out, dpi=150)
        if show:
            plt.show()
        plt.close(fig)
        return out


def load(shot_dir: Path, sensor: str, work: Path, spark_pt: float, pt0):
    """Return (group, inc, spark-on-this-clock) for one sensor of a shot."""
    cfg = SENSORS[sensor]
    files = sorted(shot_dir.glob(cfg["glob"]))
    if not files:
        raise SystemExit(f"No '{cfg['glob']}' in {shot_dir}")
    g, inc, t0 = read_group(files[0], cfg["group"], work)
    return g, inc, spark_pt + (pt0 - t0).total_seconds()


def plot_stations(shot_dir: Path, show: bool) -> list[Path]:
    """One figure per station: its pressure pair above, its photodiode pair below.

    Two stacked panels, not two y-axes on one panel — barg and volts share no
    scale, and overlaying them on a single axis invites reading a crossing as
    meaningful when it is an artefact of the scaling.
    """
    plt = _mpl(show)

    out = []
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        pt_files = sorted(shot_dir.glob(SENSORS["pt"]["glob"]))
        if not pt_files:
            raise SystemExit(f"No '{SENSORS['pt']['glob']}' in {shot_dir}")
        pg, pinc, pt0 = read_group(pt_files[0], SENSORS["pt"]["group"], work)
        spark_pt = find_spark(pg, pinc)
        dg, dinc, spark_pdt = load(shot_dir, "pdt", work, spark_pt, pt0)
        nsm = max(1, int(SENSORS["pdt"]["smooth_ms"] * 1e-3 / dinc))

        t0_ms, t1_ms = -5.0, 90.0
        for a, b, x, name in PAIRS:
            fig, (axp, axd) = plt.subplots(2, 1, figsize=(11, 7.5), sharex=True)
            rows = []
            for ax, group, inc, spark, sensor in ((axp, pg, pinc, spark_pt, "pt"),
                                                  (axd, dg, dinc, spark_pdt, "pdt")):
                cfg = SENSORS[sensor]
                have = {c.name for c in group.channels()}
                for ch, colour in ((a, COL_ODD), (b, COL_EVEN)):
                    cname = f"{cfg['prefix']}-{ch:02d}"
                    if cname not in have:
                        continue
                    s = group[cname][:].astype(float)
                    tt = np.arange(len(s)) * inc
                    base = (tt > 0.05) & (tt < spark - 0.05)
                    win = (tt >= spark + t0_ms / 1e3) & (tt <= spark + t1_ms / 1e3)
                    if base.sum() < 100 or win.sum() < 100:
                        continue
                    y = s[win] - s[base].mean()
                    if sensor == "pdt":
                        y = smooth(np.abs(y), nsm)
                    deg = cfg["odd_deg"] if ch % 2 else cfg["even_deg"]
                    pw = (tt >= spark + 1.5e-3) & (tt <= spark + 0.10)
                    pk = float(np.abs(s[pw] - s[base].mean()).max())
                    ax.plot((tt[win] - spark) * 1e3, y, lw=0.9, color=colour,
                            label=f"{cname} · {deg} · peak {pk:.3g} {cfg['unit']}")
                    rows.append((cname, pk, cfg["unit"]))
                ax.axvline(0, color="0.5", lw=0.9, ls="--")
                ax.axhline(0, color="0.85", lw=0.7)
                ax.legend(fontsize=8.5, loc="upper right")
                ax.grid(alpha=0.25)
            axp.set_ylabel("pressure (barg)")
            axd.set_ylabel("|light| (V, 0.2 ms smoothed)")
            axd.set_xlabel("time from ignitor spike (ms)")
            axd.set_xlim(t0_ms, t1_ms)
            axp.set_title(f"{shot_dir.name}   ·   station {name} at x = {x:.2f} m\n"
                          f"pressure above, light below — same station, same time base\n"
                          f"t = 0 at the ignitor spike ({spark_pt * 1e3:.1f} ms into "
                          f"the PT record)", fontsize=10.5)
            fig.tight_layout()
            f = shot_dir / f"station_{name}_{x:.2f}m_{shot_dir.name}.png"
            fig.savefig(f, dpi=150)
            if show:
                plt.show()
            plt.close(fig)
            out.append(f)
            print(f"  {name:<10} x={x:.2f} m   " +
                  "  ".join(f"{c} {v:.3g} {u}" for c, v, u in rows))
    return out


def velocity_figure(shot_dir: Path, res: dict, sensor: str,
                    spark_ms: float, show: bool) -> Path:
    plt = _mpl(show)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))
    t = [p[1] for p in res["points"]]
    x = [p[0] for p in res["points"]]

    ax1.plot(t, x, "o-", color="#c0392b", ms=7, lw=1.6)
    # Room for the station labels, then flip the label to the left of its marker
    # for any point in the right-hand third — otherwise the last one (usually
    # "station name · PT-16") runs off the axes.
    span = (max(t) - min(t)) or 1.0
    ax1.set_xlim(min(t) - 0.10 * span, max(t) + 0.28 * span)
    flip_after = min(t) + 0.66 * span
    for (xx, tt), r in zip(res["points"], [res["per"][p[0]] for p in res["points"]]):
        left = tt > flip_after
        ax1.annotate(f"{r['station']} · {SENSORS[sensor]['prefix']}-{r['ch']:02d}", xy=(tt, xx),
                     xytext=(-9 if left else 9, 0), textcoords="offset points",
                     fontsize=8, color="#555555", va="center",
                     ha="right" if left else "left")
    ax1.set_title(f"{SENSORS[sensor]['front'].capitalize()}-front arrival", fontsize=11)
    ax1.set_xlabel("time from ignitor spike (ms)")
    ax1.set_ylabel("distance from closed head (m)")
    ax1.grid(alpha=0.3)
    if res["fit"]:
        ax1.text(0.03, 0.97, f"straight-line fit {res['fit']:.0f} m/s   R² {res['r2']:.3f}",
                 transform=ax1.transAxes, va="top", fontsize=9,
                 bbox=dict(fc="white", ec="0.8"))
    if res.get("warnings"):
        ax1.text(0.03, 0.06,
                 "UNRELIABLE — see the terminal output.\n"
                 "Too many stations dropped, or a flame speed\n"
                 "above the choked-flame limit.",
                 transform=ax1.transAxes, fontsize=9, color="#c0392b",
                 bbox=dict(fc="#fff0f0", ec="#c0392b"))
    elif not res["monotonic"]:
        ax1.text(0.03, 0.06,
                 "CAUSALITY WARNING: the front appears to reach a\n"
                 "downstream station BEFORE an upstream one.\n"
                 "One of the picks is wrong — check the raw traces.",
                 transform=ax1.transAxes, fontsize=9, color="#c0392b",
                 bbox=dict(fc="#fff0f0", ec="#c0392b"))

    mids = [(s["x1"] + s["x2"]) / 2 for s in res["segments"] if s["v"]]
    vels = [s["v"] for s in res["segments"] if s["v"]]
    ax2.plot(mids, vels, "s-", color="#1f6aa5", ms=8, lw=1.6)
    for m, v in zip(mids, vels):
        ax2.annotate(f"{v:.0f}", xy=(m, v), xytext=(0, 9),
                     textcoords="offset points", ha="center", fontsize=8.5)
    # Headroom for the value labels: they sit 9 pt above their marker, so the
    # top of the data range must not be the top of the axes.
    if vels:
        lo, hi = min(vels), max(vels)
        rng = (hi - lo) or max(abs(hi), 1.0)
        ax2.set_ylim(lo - 0.10 * rng, hi + 0.18 * rng)
    ax2.set_title("Segment velocity", fontsize=11)
    ax2.set_xlabel("distance from closed head (m)   [segment mid-point]")
    ax2.set_ylabel("velocity (m/s)")
    ax2.set_xlim(0, 7.3)
    ax2.grid(alpha=0.3)

    sub = ("flame front — arrival timed at the PEAK of the glow, so read the "
           "speeds as ±30 %" if sensor == "pdt" else
           "pressure front — arrival timed on the leading edge")
    fig.suptitle(f"{shot_dir.name}   ·   {sub}\n"
                 f"ignitor spike at {spark_ms:.1f} ms into the PT record", fontsize=11.5)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    out = shot_dir / f"velocity_{SENSORS[sensor]['prefix']}_{shot_dir.name}.png"
    fig.savefig(out, dpi=150)
    if show:
        plt.show()
    plt.close(fig)
    return out


