"""Front picking: arrival times, segment velocities and the checks on them."""
from __future__ import annotations

import numpy as np

from .core import CFG, PAIRS, SENSORS, smooth


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


def summarise(per: dict, notes: list, enforce_causality: bool = False) -> dict:
    pts = sorted((x, r["arrival"]) for x, r in per.items())
    dropped = []
    if enforce_causality and len(pts) > 2:
        keep = set(longest_causal(pts, [per[x]["peak"] for x, _ in pts]))
        if len(keep) < len(pts):
            for i, (x, _) in enumerate(pts):
                if i not in keep:
                    dropped.append(x)
                    notes.append(
                        f"{per[x]['station']} ({x:.2f} m, PDT-{per[x]['ch']:02d}) dropped: "
                        f"its arrival breaks the order of the others")
            pts = [p for i, p in enumerate(pts) if i in keep]
            per = {x: r for x, r in per.items() if x not in dropped}
    segs = []
    for (x1, t1), (x2, t2) in zip(pts, pts[1:]):
        dt_ = (t2 - t1) / 1e3
        segs.append(dict(x1=x1, x2=x2, dt_ms=t2 - t1,
                         v=(x2 - x1) / dt_ if dt_ > 1e-9 else None))
    mono = all(t2 >= t1 for (_, t1), (_, t2) in zip(pts, pts[1:]))
    fit = r2 = None
    if len(pts) >= 2:
        X = np.array([x for x, _ in pts])
        T = np.array([t for _, t in pts]) / 1e3
        fit = float(np.polyfit(T, X, 1)[0])
        r2 = float(np.corrcoef(T, X)[0, 1] ** 2)
    warnings = []
    if enforce_causality:
        if len(dropped) >= CFG["max_dropped"]:
            warnings.append(
                f"{len(dropped)} of {len(dropped) + len(pts)} stations had to be dropped "
                f"to get a causally-ordered set.\n     Treat this shot's flame front as "
                f"UNRELIABLE — check the raw glow traces before using any number.")
        fast = [s for s in segs if s["v"] and s["v"] > CFG["pdt_implausible_mps"]]
        if fast:
            warnings.append(
                "a flame segment exceeds " + f"{CFG["pdt_implausible_mps"]:.0f} m/s "
                f"({max(s['v'] for s in fast):.0f} m/s over "
                f"{fast[0]['x1']:.2f}-{fast[0]['x2']:.2f} m).\n     That is at or above "
                "the choked-flame speed and is almost certainly a mis-picked glow, not a "
                "measurement.\n     A high R2 here means the wrong points fit a line "
                "well, not that the answer is right.")
    return dict(per=per, points=pts, segments=segs, monotonic=mono,
                fit=fit, r2=r2, notes=notes, dropped=dropped, warnings=warnings)


def analyse_pdt(group, inc: float, spark: float):
    """Flame front: the arrival is the PEAK of the glow — see the module docstring."""
    names = {c.name for c in group.channels()}
    nsm = max(1, int(CFG["pdt_smooth_ms"] * 1e-3 / inc))
    per, notes = {}, []
    for a, b, x, name in PAIRS:
        cand = {}
        for ch in (a, b):
            cname = f"{SENSORS['pdt']['prefix']}-{ch:02d}"
            if cname not in names:
                continue
            s = group[cname][:].astype(float)
            t = np.arange(len(s)) * inc
            base = (t > 0.05) & (t < spark - 0.05)
            win = (t >= spark - 0.005) & (t <= spark + CFG["pdt_search_ms"] / 1e3)
            if base.sum() < 1000 or win.sum() < 100:
                continue
            sd = float(s[base].std()) or 1e-12
            w = smooth(np.abs(s[win] - s[base].mean()), nsm)
            tw = (t[win] - spark) * 1e3
            pk = float(w.max())
            fwhm = int((w > 0.5 * pk).sum()) * inc * 1e3 if pk > 0 else 0.0
            if pk < CFG["pdt_min_peak"] or fwhm < CFG["pdt_min_fwhm_ms"]:
                notes.append(f"{cname} rejected: peak {pk:.3f} V, glow {fwhm:.1f} ms "
                             f"(need {CFG["pdt_min_peak"]} V and {CFG["pdt_min_fwhm_ms"]} ms)")
                continue
            cand[ch] = dict(sd=sd, peak=pk, fwhm=fwhm, snr=pk / sd,
                            arrival=float(tw[int(np.argmax(w))]))
        if not cand:
            continue
        chosen = max(cand, key=lambda c: cand[c]["snr"])
        if len(cand) == 2:
            other = [c for c in cand if c != chosen][0]
            notes.append(f"{name} ({x:.2f} m): both channels usable, took "
                         f"PDT-{chosen:02d} (S/N {cand[chosen]['snr']:.0f}) over "
                         f"PDT-{other:02d} (S/N {cand[other]['snr']:.0f})")
        per[x] = dict(station=name, ch=chosen, **cand[chosen])
    return summarise(per, notes, enforce_causality=True)


def analyse(group, inc: float, spark: float):
    names = {c.name for c in group.channels()}
    per, notes = {}, []
    for a, b, x, name in PAIRS:
        cand = {}
        for ch in (a, b):
            if f"{SENSORS['pt']['prefix']}-{ch:02d}" not in names:
                continue
            s = group[f"{SENSORS['pt']['prefix']}-{ch:02d}"][:].astype(float)
            t = np.arange(len(s)) * inc
            base = (t > 0.05) & (t < spark - 0.05)
            if base.sum() < 1000:
                continue
            y = s - s[base].mean()
            sd = float(s[base].std()) or 1e-12
            win = (t >= spark + CFG["blank_ms"] / 1e3) & (t <= spark + CFG["search_ms"] / 1e3)
            w, tw = np.abs(y[win]), t[win]
            thr = max(CFG["floor"], CFG["sigma"] * sd)
            arr = None
            if w.max() >= 1.5 * thr:
                hold = max(1, int(CFG["hold_ms"] * 1e-3 / inc))
                over = w > thr
                for k in range(len(over) - hold):
                    if over[k] and over[k:k + hold].all():
                        arr = float((tw[k] - spark) * 1e3)
                        break
            cand[ch] = dict(sd=sd, peak=float(w.max()), arrival=arr)
        if not cand:
            continue
        peaks = {c: v["peak"] for c, v in cand.items()}
        chosen = None
        if len(peaks) == 2:
            lo_ch = min(peaks, key=peaks.get)
            hi_ch = max(peaks, key=peaks.get)
            if peaks[hi_ch] > CFG["pair_ratio"] * max(peaks[lo_ch], 1e-6):
                chosen = lo_ch
                notes.append(f"{name} ({x:.2f} m): PT-{hi_ch:02d} reads "
                             f"{peaks[hi_ch]:.1f} barg against PT-{lo_ch:02d}'s "
                             f"{peaks[lo_ch]:.1f} — dropped, using PT-{lo_ch:02d}")
        if chosen is None:
            usable = {c: v for c, v in cand.items() if v["arrival"] is not None}
            if not usable:
                continue
            chosen = min(usable, key=lambda c: usable[c]["sd"])
        if cand[chosen]["arrival"] is None:
            continue
        per[x] = dict(station=name, ch=chosen, **cand[chosen])

    return summarise(per, notes)


