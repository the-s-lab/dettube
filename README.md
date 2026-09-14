# dettube — detonation-tube quick-look tools

Reads a detonation- or shock-tube shot recorded as LabVIEW TDMS, plots every
channel, picks the pressure and flame fronts, and exports both to CSV.

Nothing about any particular tube is built in. A **rig file** says where the
gauges are and what thresholds suit them, so the same package serves any tube
whose DAQ writes TDMS.

## Install

```
pip install -e .            # from this folder, for development
pip install .               # or a plain install
```

Needs Python 3.9+. Dependencies (npTDMS, numpy, matplotlib) come with it. The
folder picker uses tkinter, which ships with most Python builds; on some Linux
distributions it is a separate OS package (`apt install python3-tk`).

## First run

```
dettube rig --template myrig.toml     # write a documented example
$EDITOR myrig.toml                    # station positions, channel numbers, thresholds
export DETTUBE_RIG=$PWD/myrig.toml    # set once, or pass --rig every time
dettube rig                           # check what got loaded
```

The rig file is searched for in this order: `--rig PATH`, `$DETTUBE_RIG`,
`./rig.toml`, `~/.config/dettube/rig.toml`. Without one, every command stops and
says so rather than guessing — a wrong station position quietly corrupts every
velocity downstream of it, so guessing is the one thing this must not do.

## Commands

```
dettube rig        [--template FILE]
dettube plot       <shot> [--sensor pt|pdt] [--per-station] [--show]
dettube velocity   <shot> [--sensor pt|pdt] [--show]
dettube conditions <shot> [--csv FILE]
dettube export     <shot> [--raw] [--full] [--every N]
```

All of them take `--rig FILE`.

Omit `<shot>` to get a folder picker. Each subcommand is also installed on its
own as `dettube-plot`, `dettube-velocity`, `dettube-conditions` and
`dettube-export`.

| Command | Writes |
|---|---|
| `dettube plot <shot>` | `PT_stack_<shot>.png` |
| `dettube plot <shot> --sensor pdt` | `PDT_stack_<shot>.png` |
| `dettube plot <shot> --per-station` | six `station_<name>_<x>m_<shot>.png` |
| `dettube velocity <shot>` | `velocity_PT_<shot>.png` |
| `dettube velocity <shot> --sensor pdt` | `velocity_PDT_<shot>.png` |
| `dettube conditions <shot>` | a table on the terminal; `--csv` writes `<shot>_conditions.csv` and `<shot>_events.csv` |
| `dettube export <shot> --raw` | six CSVs in `<shot>/csv/` |

## Layout

```
dettube/
  rig.py       rig-file format, search path, template
  core.py      TDMS reading, ignitor-spike detection, the loaded rig
  analysis.py  front picking, segment velocities, causality and sanity checks
  conditions.py  pre-ignition readings from the slow process instruments
  plots.py     figures
  export.py    CSV
  cli.py       command line
```

Everything that describes a tube lives in the rig file and nowhere else. That
separation is not tidiness: the station positions in the project this grew out of
were wrong for months because the same numbers were copied into four source files
and drifted apart. One file, loaded at run time, cannot drift.

## What the plots show

### What it draws

Every PT channel at **true amplitude**, offset vertically by its axial position,
with both channels of each station overlaid. Nothing is normalised, so a station
reading four times its neighbour looks four times bigger.

Colour is by **side of the tube**, not by station:

| | Channels | Orientation |
|---|---|---|
| **green** | PT-01, 03, 05, 07, 13, 15 (odd) | 45° |
| **blue** | PT-02, 04, 06, 08, 14, 16 (even) | 225° |

The two gauges at a station sit diametrically opposite each other, so in a
healthy shot green and blue lie on top of one another at every station. Colouring
by side rather than by station means a fault affecting one whole side of the tube
shows up as a pattern running down the plot, instead of looking like one odd
station. Green and blue were checked for colour-vision safety (ΔE 18.0 deutan /
protan, 18.7 normal vision).

Station names, positions and channel numbers all come from the rig file — run
`dettube rig` to see the ones in force.

Two things worth getting right there, because both have caught people out:

* **What `x = 0` means.** If the ignitor is not at the closed end, distances from
  the datum are not distances from the ignitor. Velocities are unaffected, since
  they come from differences between stations, but any single distance is.
* **Channel numbers need not be contiguous.** If a section of tube is removed,
  its channels simply do not appear in the rig file, and the numbering jumps.

### `<< PAIR DISAGREES`

Printed in red beside any station whose two gauges differ by more than 2×. Two
transducers 150 mm apart on one flange ring should read within a few tens of
percent of each other, so a flag means one of them is not measuring gas
pressure. It is worth taking seriously. On the rig this was built against, one gauge
tripped it on every shot — reading 4× to 11× its partner, and timing the front
1–2 ms early, which no amount of filtering removed. A median filter heavy enough
to bring its amplitude down also destroyed the rise it was supposed to be
measuring. The flag is what found it.

## `dettube_plot.py --per-station` — one file per station, pressure and light together

```
dettube plot "D:\shots\03.100926" --per-station
```

Writes **six PNGs**, one per station — `station_B2_3.50m_<shot>.png` and so on.
Each has the station's pressure pair on the upper panel and its photodiode pair
on the lower one, sharing a time axis, so you can see what the pressure and the
light did at the SAME place.

Two stacked panels, not two y-axes on one panel: barg and volts share no scale,
and overlaying them invites reading a crossing as meaningful when it is only an
artefact of how the two were scaled.

This is the view that shows the physics directly. At a healthy mid-tube station
you see the gentle precursor compression arrive first, then several milliseconds
later the light jump as the flame itself reaches the viewport, with a local
pressure spike alongside it. The separation between those two is the thing the
whole campaign is about.

## Velocity

```
dettube velocity "D:\shots\03.100926" --sensor pdt
```

Writes `velocity_<folder>.png` and prints an arrival table and a velocity table.

### How the front is picked

`t = 0` is the ignitor's electrical spike. The first **1.5 ms after it are
blanked**, because the spike lands on every channel at once and would otherwise
be picked as the front. The arrival is then the first crossing of
max(0.30 barg, 10σ) that **stays** above for 0.1 ms, so one noisy sample cannot
trigger it.

Where a station's two gauges differ in peak amplitude by more than 2×, the
**louder one is dropped** and the quieter kept, and the choice is printed:

```
! A4 (6.00 m): PT-14 reads 28.6 barg against PT-13's 4.0 — dropped, using PT-13
```

## Photodiodes — `--sensor pdt`

Both tools take `--sensor pdt`. The photodiode path differs in ways that matter:

- **The clocks are different.** Pressure and photodiodes are separate DAQ tasks
  with separate start times; the skew has been as large as 84 ms. The ignitor
  spike is always found in the PRESSURE record, then converted to the photodiode
  clock using each file's own `wf_start_time`. Both files must be present.
- **No pair-disagreement test.** The rig carries a MIXED population of
  photodiodes, so the two channels of a station are often different sensor types
  and are expected to differ. Instead each channel is judged on its own: it must
  produce at least 0.05 V and hold it for at least 2 ms at half height. That
  separates a real flame glow, which is tens of milliseconds wide, from the
  ignitor's sub-millisecond EMI spike. Channels are marked `usable` or `--`.
- **The arrival is the PEAK of the glow, not its leading edge.** Some units carry
  a baseline 10–40 % of their own signal, so an edge threshold lands inside the
  noise. The peak is self-consistent between stations, which is what a velocity
  needs, but it times the brightest part of the burning zone rather than its
  front — so **read flame speeds as good to about ±30 %**, not to the two
  figures the fit prints.
- **Causality filtering.** Stations whose arrival breaks the order of the others
  are dropped and named, keeping the largest self-consistent set. Where several
  sets are the same size, the tie goes to the one with the most light — the
  station that saw the brightest glow is the one most likely to be timing the
  flame. Signal-to-noise is deliberately NOT used for this: a quiet photodiode
  that barely sees the flame scores well on it.

### UNRELIABLE warning

The flame path refuses to look confident when it should not be. It says
UNRELIABLE, on the plot and in the terminal, if either:

- **two or more of the six stations had to be dropped** to get a causally
  ordered set, or
- **any segment exceeds 1000 m/s** — the sound speed of the combustion products,
  and the last step before DDT. In a tube that has produced 100–400 m/s flames,
  a segment above that is far likelier to be a mis-picked glow than a real event.

A high R² alongside these warnings means the wrong points fit a line well. It is
not reassurance.

### CAUSALITY WARNING

If the fitted arrivals put the front at a downstream station *before* an
upstream one, the script says so on the plot and in the terminal. That is
physically impossible, so it means a pick failed — go back to
`dettube plot` and look at the raw traces before believing any velocity.

## Conditions

```
dettube conditions "D:\shots\03.100926"
```

What the process instruments read before the shot, which is not always what the
folder is named. In the set this was built against, a folder called `50%H2`
holds a shot whose analyser read 48.4%. Folder names record the target; the
instrument records the gas.

Each channel gets its pre-ignition median, a median absolute deviation, and the
full range of the same window. The median and MAD are used deliberately: these
channels carry impulsive noise — a flow transmitter on a closed line throwing
single-sample spikes to 9 L/min about 0.4% of the time — and a mean and standard
deviation over that describe the spikes rather than the reading. The range is
printed beside the spread so the spikes stay visible instead of being averaged
out of sight.

Three things are flagged.

**Drift** compares the last second before ignition with the second before that.
Near zero means filling had finished and the mixture had settled, so the single
number means something. Large means it had not, and the number is a snapshot of
something still moving.

**A sustained change after ignition** — a run entirely outside the pre-ignition
envelope, not an isolated spike, since these channels spike just as often before
ignition as after. A sustained response arriving in under a millisecond is
flagged `TOO FAST`: no analyser or sheathed thermocouple responds that quickly,
so it is the ignitor's electrical pickup or a wiring fault, and only the
channel's pre-ignition value can be used.

**A channel pinned at one end of its span** is marked `*` — or `!` if it is not
reading at all. The two are told apart by counting distinct values: an input
still being digitised dithers over hundreds of codes even when the reading it
carries is a true zero, whereas a channel nobody is sampling repeats one value
exactly. What this does *not* establish is whether the instrument behind a live
input is powered and spanned; a transmitter parked at the bottom of its output
range looks identical to one correctly reporting zero. Only a span check, or a
shot that actually contains the species, settles that.

Add `on` and `note` to a `[[conditions.channel]]` entry in the rig file to carry
what you know about an instrument into every report. Where it is mounted is
worth writing down: an analyser on a supply header measures the supply, and that
equals the tube only once the tube has been purged to equilibrium.

### How the shot was set up

If the control system writes a log beside the data, point the rig file at it
with `[event_log]` and `dettube conditions` reads it too. It is worth doing:
which valves were open, and how long a spray had been running before ignition,
are recorded there and **nowhere in the TDMS**. Without it the only surviving
record of a shot's configuration is its folder name, which states what was
intended rather than what happened.

```
How the shot was set up — Ignition Report20260910_110457.csv
  ignition logged at   11:04:58.876
  shut    water curtain SV01 (centre of A1, 1.00 m)
  OPEN    water curtain SV04 (centre of B3, 4.50 m)
  mist valve opened at 11:04:53.358   5.52 s before ignition
```

The log's own ignition timestamp is checked against the ignitor spike measured
in the pressure record, because the two are not the same moment and the gap is
not small. On the rig this was written for they sit **exactly 8 hours apart** —
the control system logs local time, the DAQ logs UTC, and nothing in either file
says so. Anything correlating the two without allowing for it is out by 8 hours
silently. Past that whole-hour part the logged time still runs 0.9 to 5.3 s early
from shot to shot, which is why the spike and not the timestamp is t = 0.

`glob` accepts a list, so a rig can keep matching an older filename alongside the
current one and old shots stay readable.

## CSV export

```
dettube export "D:\shots\03.100926" --raw
```

Writes into a `csv/` folder beside the data. Results come from the same picking code the plots use — there is no second copy.

| File | Contents |
|---|---|
| `<shot>_arrivals.csv` | one row per station per sensor: channel used, arrival time, peak, `used`/`dropped`, plus every rejection note as its own row |
| `<shot>_velocities.csv` | one row per segment per sensor: length, transit time, velocity, fit, R², and any warning |
| `<shot>_raw_PT.csv` etc. | raw samples, one file per DAQ group |

Every raw file carries a `t_ms` column measured **from the ignitor spike**, so
the four groups line up with one another. They do not line up by themselves: PT,
PDT, Mixed and Pitot are separate DAQ tasks and the start-time offsets between
them run from −23 ms to +68 ms across the shots checked.

**On size.** The photodiode record is 16 channels at 100 kHz — written whole
that is ~700 000 rows and well over 100 MB of text. The default is the window
−20 to +120 ms around the spike, about 1.4 MB. `--full` writes everything and
`--every N` decimates. Sizes are printed as files are written.

### What it will tell you it could not do

Rather than write a silently wrong file, the export skips a group and says why:

```
Pitot: the ignitor spike falls at 2531 ms but this record is only 1800 ms long — skipped
Pitot: 'Pitot Tube Outputs' in Pitot Output20260828_104336.tdms has no sampled data — skipped
```

Both of those are real, from the September shots: **the Pitot data is unusable
in two of the four** — empty in one, and stopped recording before the shot in
another. Worth knowing before anyone plans an analysis around it.

It also reports when channels in one group have different lengths and it has had
to trim to the shortest, naming them. That is normal on this rig — the last pair
usually runs a few thousand samples short — but a group where ten of sixteen
channels are short is worth a look.

## Two things the tools handle for you

**The TDMS files will not open as they are.** The VI pre-allocates each file and
does not trim it on close, so a few hundred bytes of zero padding sit past the
last segment; npTDMS stops with `Segment does not start with b'TDSm'`, and the
rig's own `.tdms.log` files record the same thing as
`TdsErrNotTdsFile(-2503)`. The reader walks the segment chain and reads up to
the last complete segment. Nothing is lost. *Worth fixing in the VI so nobody
has to work around it.*

**`Ignition Time` in the CSV is not the moment of ignition.** On the shots
checked it was 0.5–1.4 s early. The script instead uses the ignitor's own
electrical spike, which lands on every PT channel simultaneously and is the best
time reference in the recording. It is reported in the plot title so you can see
where in the record the shot actually happened.

## Known data-integrity issues to watch

- **The TDMS writer looks size-capped.** Every TDMS file is byte-identical in
  size between shots, and for one August shot the Excel export carried 710 000
  samples against 700 000 in the TDMS. A long shot will lose its tail with no
  error. Worth checking before a campaign that matters.
- **Record lengths are inconsistent** between streams within one shot, and one
  September shot recorded an empty Pitot file.
- **Water-curtain state lives only in `Ignition Report*.csv`**, not in the TDMS.
  Water flow rate and supply pressure are not recorded anywhere; the nozzle is
  hand-noted in the analysis workbook as "Flat Fan 40deg 005, 2 bar". Adding
  flow and pressure as Mixed-Outputs channels would put them in the TDMS
  automatically.
