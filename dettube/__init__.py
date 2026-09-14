"""Quick-look tools for detonation-tube shots recorded as LabVIEW TDMS.

Reads a shot's TDMS files, plots every channel, picks the pressure and flame
fronts, and exports both to CSV. Nothing about any particular tube is built in:
a rig file says where the gauges are — see `dettube.rig` or run
`dettube rig --template`.

Two things it handles that trip people up:

* TDMS files written by a VI that pre-allocates and does not trim on close are
  padded with zeros, and npTDMS stops on them with "Segment does not start with
  b'TDSm'". The reader walks the segment chain and stops at the last complete
  segment. Nothing is lost.
* An ignition timestamp written by the control software is often not the moment
  of ignition — on the shots this was built against it was 0.5 to 1.4 s early.
  t = 0 is taken instead from the ignitor's own electrical spike in the pressure
  record, and converted onto each other DAQ group's clock, because they are
  separate tasks whose start times can differ by tens of milliseconds.
"""
from .core import CFG, COND, GROUPS, PAIRS, SENSORS, find_spark, load_rig, read_group  # noqa: F401

__version__ = "0.1.0"
__all__ = ["PAIRS", "SENSORS", "GROUPS", "CFG", "COND",
           "load_rig", "read_group", "find_spark"]
