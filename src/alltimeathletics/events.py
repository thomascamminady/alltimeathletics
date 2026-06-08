"""Canonical catalogue of every event page on alltime-athletics.com.

The catalogue is hardcoded from the link lists on `men.htm` / `women.htm`
because Larsson's index pages are stable and small. If a new event is added,
the scraper will WARN about an unmapped link and you add a row here.

Each entry records the URL slug Larsson uses, the human label, the sex,
the legality (wind-legal/legal-conditions vs all marks including illegal),
and an event *family* that drives parser dispatch and mark normalization.

Labels are written in consistent Title Case so the homepage event listing
reads uniformly. Larsson's source pages use mixed casing
("marathon" vs "Marathon walk" vs "half-marathon"); we normalise here so
every UI rendering — including the parquet's ``event`` column on the next
re-scrape — picks up the same convention.

Families:
- `track_time`           — track race; mark is a duration
- `track_time_wind`      — track race with a wind column (sprints, hurdles)
- `field_distance`       — vertical jump or throw; mark is metres
- `field_distance_wind`  — horizontal jump with wind column
- `combined_points`      — decathlon/heptathlon; mark is points
- `relay`                — team event; rows interleave with team-member lines
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

BASE_URL = "https://www.alltime-athletics.com"

# Canonical display order for the homepage category subheadings. The template
# iterates this list so categories always appear in the same order regardless
# of how the events happen to be sorted within a sex/legality block.
CATEGORY_ORDER: tuple[str, ...] = (
    "Sprints",
    "Hurdles",
    "Middle distance",
    "Distance",
    "Road",
    "Field",
    "Combined",
    "Walks",
    "Relays",
    "Track",  # safe fallback bucket; ideally empty
)

Sex = Literal["men", "women", "mixed"]
Legality = Literal["legal", "non-legal"]
Family = Literal[
    "track_time",
    "track_time_wind",
    "field_distance",
    "field_distance_wind",
    "combined_points",
    "relay",
]


_MILE_M = 1609.344
_YARD_M = 0.9144


def _distance_metres(label: str) -> float | None:
    """Best-effort metric distance for a plain track-running label.

    Only called after relay/combined/field/walk/hurdles/road labels have been
    routed elsewhere, so the inputs are simple forms like "100 metres",
    "10000 metres", "1 mile", "2 miles" or "100 yards". Returns ``None`` when
    no distance can be parsed (caller then uses the "Track" fallback).
    """
    m = re.search(r"(\d+(?:\.\d+)?)", label)
    if m is None:
        return None
    value = float(m.group(1))
    if "mile" in label:
        return value * _MILE_M
    if "yard" in label:
        return value * _YARD_M
    # "metres" (and the bare numeric case) are already in metres.
    return value


@dataclass(frozen=True, slots=True)
class Event:
    slug: str  # URL filename without `.htm`; also the canonical event id
    label: str  # human-readable, Title Case (e.g. "100 Metres")
    sex: Sex
    legality: Legality
    family: Family

    @property
    def url(self) -> str:
        return f"{BASE_URL}/{self.slug}.htm"

    @property
    def category(self) -> str:
        """Display bucket for the homepage event grid.

        Computed from ``family`` + ``label`` (no stored field, so the ~180
        ``_RAW`` rows stay untouched). Order of the checks matters: family
        wins first, then the label-keyword cases (walks/hurdles/road) take
        precedence over the by-distance track buckets so that e.g. a
        "3000m Steeplechase" lands in Hurdles rather than Distance, and a
        "Marathon Walk" lands in Walks rather than Road.
        """
        label = self.label.lower()

        # 1. Family-driven buckets.
        if self.family == "relay":
            return "Relays"
        if self.family == "combined_points":
            return "Combined"
        if self.family in ("field_distance", "field_distance_wind"):
            return "Field"

        # 2. Label-keyword buckets (must win over by-distance track buckets).
        if "walk" in label:
            return "Walks"
        if "hurdles" in label or "steeple" in label:
            return "Hurdles"
        if (
            "marathon" in label  # also catches "half-marathon"
            or "road" in label
            or "one hour" in label
            or "1 hour" in label
        ):
            return "Road"

        # 3. Remaining track running events, bucketed by parsed distance.
        d = _distance_metres(label)
        if d is not None:
            if d <= 400:
                return "Sprints"
            if d <= _MILE_M:  # 800 / 1000 / 1500 / 1 mile (1609.344m)
                return "Middle distance"
            return "Distance"  # 2000m and up

        # 4. Safe fallback (ideally nothing reaches here).
        return "Track"


# (slug, label, sex, legality, family)
_RAW: tuple[tuple[str, str, Sex, Legality, Family], ...] = (
    # ---------- MEN — wind-legal sprints / hurdles (track_time_wind) ----------
    ("m_100ok", "100 Metres", "men", "legal", "track_time_wind"),
    ("m_200ok", "200 Metres", "men", "legal", "track_time_wind"),
    ("m_110hok", "110m Hurdles", "men", "legal", "track_time_wind"),
    ("m60mok", "60 Metres", "men", "legal", "track_time_wind"),
    ("m_60mhok", "60m Hurdles", "men", "legal", "track_time_wind"),
    ("m_100yok", "100 Yards", "men", "legal", "track_time_wind"),
    ("m_200hok", "200m Hurdles", "men", "legal", "track_time_wind"),
    # non-legal counterparts
    ("m100mno", "100 Metres", "men", "non-legal", "track_time_wind"),
    ("m_200no", "200 Metres", "men", "non-legal", "track_time_wind"),
    ("m_110hno", "110m Hurdles", "men", "non-legal", "track_time_wind"),
    ("m60mno", "60 Metres", "men", "non-legal", "track_time_wind"),
    ("m_60mhno", "60m Hurdles", "men", "non-legal", "track_time_wind"),
    ("m_100yno", "100 Yards", "men", "non-legal", "track_time_wind"),
    ("m_200hno", "200m Hurdles", "men", "non-legal", "track_time_wind"),
    # ---------- MEN — track times without wind (track_time) ----------
    ("m_400ok", "400 Metres", "men", "legal", "track_time"),
    ("m_800ok", "800 Metres", "men", "legal", "track_time"),
    ("m_1500ok", "1500 Metres", "men", "legal", "track_time"),
    ("m_mileok", "1 Mile", "men", "legal", "track_time"),
    ("m_3000ok", "3000 Metres", "men", "legal", "track_time"),
    ("m_5000ok", "5000 Metres", "men", "legal", "track_time"),
    ("m_10kok", "10000 Metres", "men", "legal", "track_time"),
    ("mhmaraok", "Half-Marathon", "men", "legal", "track_time"),
    ("mmaraok", "Marathon", "men", "legal", "track_time"),
    ("m3000hok", "3000m Steeplechase", "men", "legal", "track_time"),
    ("m_400hok", "400m Hurdles", "men", "legal", "track_time"),
    ("m_300ok", "300 Metres", "men", "legal", "track_time"),
    ("m_600ok", "600 Metres", "men", "legal", "track_time"),
    ("m_1000ok", "1000 Metres", "men", "legal", "track_time"),
    ("m_2000ok", "2000 Metres", "men", "legal", "track_time"),
    ("m_2miok", "2 Miles", "men", "legal", "track_time"),
    ("m1hourok", "One Hour Run", "men", "legal", "track_time"),
    ("m25kok", "25000m Track", "men", "legal", "track_time"),
    ("m30kok", "30000m Track", "men", "legal", "track_time"),
    ("m10kroad", "10km Road", "men", "legal", "track_time"),
    ("m15kroad", "15km Road", "men", "legal", "track_time"),
    ("m10milesroad", "10 Miles Road", "men", "legal", "track_time"),
    ("m20kroad", "20km Road", "men", "legal", "track_time"),
    ("m30kroad", "30km Road", "men", "legal", "track_time"),
    ("m100km", "100km Road", "men", "legal", "track_time"),
    ("m2000hok", "2000m Steeplechase", "men", "legal", "track_time"),
    ("m20kwok", "20km Race Walk", "men", "legal", "track_time"),
    ("mHalf-Marathonwok", "Half-Marathon Walk", "men", "legal", "track_time"),
    ("m35kwok", "35km Race Walk", "men", "legal", "track_time"),
    ("m50kwok", "50km Race Walk", "men", "legal", "track_time"),
    ("mMarathonwok", "Marathon Walk", "men", "legal", "track_time"),
    ("m10kwok", "10000m Track Walk", "men", "legal", "track_time"),
    # non-legal
    ("m_400no", "400 Metres", "men", "non-legal", "track_time"),
    ("m_800no", "800 Metres", "men", "non-legal", "track_time"),
    ("m_1500no", "1500 Metres", "men", "non-legal", "track_time"),
    ("m_mileno", "1 Mile", "men", "non-legal", "track_time"),
    ("m_3000no", "3000 Metres", "men", "non-legal", "track_time"),
    ("m_5000no", "5000 Metres", "men", "non-legal", "track_time"),
    ("m_10kno", "10000 Metres", "men", "non-legal", "track_time"),
    ("mhmarano", "Half-Marathon", "men", "non-legal", "track_time"),
    ("mmarano", "Marathon", "men", "non-legal", "track_time"),
    ("m3000hno", "3000m Steeplechase", "men", "non-legal", "track_time"),
    ("m_400hno", "400m Hurdles", "men", "non-legal", "track_time"),
    ("m_300no", "300 Metres", "men", "non-legal", "track_time"),
    ("m_600no", "600 Metres", "men", "non-legal", "track_time"),
    ("m_1000no", "1000 Metres", "men", "non-legal", "track_time"),
    ("m1hourno", "One Hour Run", "men", "non-legal", "track_time"),
    ("m10kroadno", "10km Road", "men", "non-legal", "track_time"),
    ("m15kroadno", "15km Road", "men", "non-legal", "track_time"),
    ("m10milesroadno", "10 Miles Road", "men", "non-legal", "track_time"),
    ("m20kroadno", "20km Road", "men", "non-legal", "track_time"),
    ("m30kroadno", "30km Road", "men", "non-legal", "track_time"),
    ("m2000hno", "2000m Steeplechase", "men", "non-legal", "track_time"),
    ("m20kwno", "20km Race Walk", "men", "non-legal", "track_time"),
    ("m50kwno", "50km Race Walk", "men", "non-legal", "track_time"),
    ("m10kwno", "10000m Track Walk", "men", "non-legal", "track_time"),
    # ---------- MEN — horizontal jumps (field_distance_wind) ----------
    ("mlongok", "Long Jump", "men", "legal", "field_distance_wind"),
    ("mtripok", "Triple Jump", "men", "legal", "field_distance_wind"),
    ("mlongno", "Long Jump", "men", "non-legal", "field_distance_wind"),
    ("mtripno", "Triple Jump", "men", "non-legal", "field_distance_wind"),
    # ---------- MEN — vertical jumps + throws (field_distance) ----------
    ("mhighok", "High Jump", "men", "legal", "field_distance"),
    ("mpoleok", "Pole Vault", "men", "legal", "field_distance"),
    ("mshotok", "Shot Put", "men", "legal", "field_distance"),
    ("mdiscok", "Discus Throw", "men", "legal", "field_distance"),
    ("mhammok", "Hammer Throw", "men", "legal", "field_distance"),
    ("mjaveok", "Javelin Throw", "men", "legal", "field_distance"),
    ("mjaveoldok", "Javelin Throw (Old Specs)", "men", "legal", "field_distance"),
    ("mhighno", "High Jump", "men", "non-legal", "field_distance"),
    ("mpoleno", "Pole Vault", "men", "non-legal", "field_distance"),
    ("mshotno", "Shot Put", "men", "non-legal", "field_distance"),
    ("mdiscno", "Discus Throw", "men", "non-legal", "field_distance"),
    ("mhammno", "Hammer Throw", "men", "non-legal", "field_distance"),
    ("mjaveno", "Javelin Throw", "men", "non-legal", "field_distance"),
    ("mjaveoldno", "Javelin Throw (Old Specs)", "men", "non-legal", "field_distance"),
    # ---------- MEN — combined / relays ----------
    ("mdecaok", "Decathlon", "men", "legal", "combined_points"),
    ("mdecano", "Decathlon", "men", "non-legal", "combined_points"),
    ("m4x100ok", "4x100m Relay", "men", "legal", "relay"),
    ("m4x400ok", "4x400m Relay", "men", "legal", "relay"),
    ("m4x200ok", "4x200m Relay", "men", "legal", "relay"),
    ("m4x800ok", "4x800m Relay", "men", "legal", "relay"),
    ("m4x1500ok", "4x1500m Relay", "men", "legal", "relay"),
    ("m_4xmileok", "4x1 Mile Relay", "men", "legal", "relay"),
    ("m4x100no", "4x100m Relay", "men", "non-legal", "relay"),
    ("m4x400no", "4x400m Relay", "men", "non-legal", "relay"),
    ("m4x200no", "4x200m Relay", "men", "non-legal", "relay"),
    ("m4x800no", "4x800m Relay", "men", "non-legal", "relay"),
    # ---------- WOMEN — wind-legal sprints / hurdles ----------
    ("w_100ok", "100 Metres", "women", "legal", "track_time_wind"),
    ("w_200ok", "200 Metres", "women", "legal", "track_time_wind"),
    ("w_100hok", "100m Hurdles", "women", "legal", "track_time_wind"),
    ("w60mok", "60 Metres", "women", "legal", "track_time_wind"),
    ("w_60mhok", "60m Hurdles", "women", "legal", "track_time_wind"),
    ("w_100no", "100 Metres", "women", "non-legal", "track_time_wind"),
    ("w_200no", "200 Metres", "women", "non-legal", "track_time_wind"),
    ("w_100hno", "100m Hurdles", "women", "non-legal", "track_time_wind"),
    ("w60mno", "60 Metres", "women", "non-legal", "track_time_wind"),
    ("w_60mhno", "60m Hurdles", "women", "non-legal", "track_time_wind"),
    # ---------- WOMEN — track times no wind ----------
    ("w_400ok", "400 Metres", "women", "legal", "track_time"),
    ("w_800ok", "800 Metres", "women", "legal", "track_time"),
    ("w_1500ok", "1500 Metres", "women", "legal", "track_time"),
    ("w_mileok", "1 Mile", "women", "legal", "track_time"),
    ("w_3000ok", "3000 Metres", "women", "legal", "track_time"),
    ("w_5000ok", "5000 Metres", "women", "legal", "track_time"),
    ("w_10kok", "10000 Metres", "women", "legal", "track_time"),
    ("whmaraok", "Half-Marathon", "women", "legal", "track_time"),
    ("wmaraok", "Marathon", "women", "legal", "track_time"),
    ("w3000hok", "3000m Steeplechase", "women", "legal", "track_time"),
    ("w_400hok", "400m Hurdles", "women", "legal", "track_time"),
    ("w_300ok", "300 Metres", "women", "legal", "track_time"),
    ("w_600ok", "600 Metres", "women", "legal", "track_time"),
    ("w_1000ok", "1000 Metres", "women", "legal", "track_time"),
    ("w_2000ok", "2000 Metres", "women", "legal", "track_time"),
    ("w2milesok", "2 Miles", "women", "legal", "track_time"),
    ("w10kroad", "10km Road", "women", "legal", "track_time"),
    ("w15kroad", "15km Road", "women", "legal", "track_time"),
    ("w10milesroad", "10 Miles Road", "women", "legal", "track_time"),
    ("w20kroad", "20km Road", "women", "legal", "track_time"),
    ("w30kroad", "30km Road", "women", "legal", "track_time"),
    ("w2000hok", "2000m Steeplechase", "women", "legal", "track_time"),
    ("w20kwok", "20km Race Walk", "women", "legal", "track_time"),
    ("wHalf-Marathonwok", "Half-Marathon Walk", "women", "legal", "track_time"),
    ("w35kwok", "35km Race Walk", "women", "legal", "track_time"),
    ("w50kwok", "50km Race Walk", "women", "legal", "track_time"),
    ("wMarathonwok", "Marathon Walk", "women", "legal", "track_time"),
    ("w5kwok", "5000m Track Walk", "women", "legal", "track_time"),
    ("w10kwok", "10km Race Walk", "women", "legal", "track_time"),
    # non-legal
    ("w_400no", "400 Metres", "women", "non-legal", "track_time"),
    ("w_800no", "800 Metres", "women", "non-legal", "track_time"),
    ("w_1500no", "1500 Metres", "women", "non-legal", "track_time"),
    ("w_mileno", "1 Mile", "women", "non-legal", "track_time"),
    ("w_3000no", "3000 Metres", "women", "non-legal", "track_time"),
    ("w_5000no", "5000 Metres", "women", "non-legal", "track_time"),
    ("w_10kno", "10000 Metres", "women", "non-legal", "track_time"),
    ("whmarano", "Half-Marathon", "women", "non-legal", "track_time"),
    ("wmarano", "Marathon", "women", "non-legal", "track_time"),
    ("w3000hno", "3000m Steeplechase", "women", "non-legal", "track_time"),
    ("w_400hno", "400m Hurdles", "women", "non-legal", "track_time"),
    ("w_300no", "300 Metres", "women", "non-legal", "track_time"),
    ("w_1000no", "1000 Metres", "women", "non-legal", "track_time"),
    ("w_2000no", "2000 Metres", "women", "non-legal", "track_time"),
    ("w2milesno", "2 Miles", "women", "non-legal", "track_time"),
    ("w10kroadno", "10km Road", "women", "non-legal", "track_time"),
    ("w15kroadno", "15km Road", "women", "non-legal", "track_time"),
    ("w20kroadno", "20km Road", "women", "non-legal", "track_time"),
    ("w30kroadno", "30km Road", "women", "non-legal", "track_time"),
    ("w2000hno", "2000m Steeplechase", "women", "non-legal", "track_time"),
    ("w20kwno", "20km Race Walk", "women", "non-legal", "track_time"),
    ("w5kwno", "5000m Track Walk", "women", "non-legal", "track_time"),
    ("w10kwno", "10km Race Walk", "women", "non-legal", "track_time"),
    # ---------- WOMEN — horizontal jumps ----------
    ("wlongok", "Long Jump", "women", "legal", "field_distance_wind"),
    ("wtripleok", "Triple Jump", "women", "legal", "field_distance_wind"),
    ("wlongno", "Long Jump", "women", "non-legal", "field_distance_wind"),
    ("wtripleno", "Triple Jump", "women", "non-legal", "field_distance_wind"),
    # ---------- WOMEN — vertical jumps + throws ----------
    ("whighok", "High Jump", "women", "legal", "field_distance"),
    ("wpoleok", "Pole Vault", "women", "legal", "field_distance"),
    ("wshotok", "Shot Put", "women", "legal", "field_distance"),
    ("wdiscok", "Discus Throw", "women", "legal", "field_distance"),
    ("whammok", "Hammer Throw", "women", "legal", "field_distance"),
    ("wjaveok", "Javelin Throw", "women", "legal", "field_distance"),
    ("wjaveoldok", "Javelin Throw (Old Specs)", "women", "legal", "field_distance"),
    ("whighno", "High Jump", "women", "non-legal", "field_distance"),
    ("wpoleno", "Pole Vault", "women", "non-legal", "field_distance"),
    ("wshotno", "Shot Put", "women", "non-legal", "field_distance"),
    ("wdiscno", "Discus Throw", "women", "non-legal", "field_distance"),
    ("whammno", "Hammer Throw", "women", "non-legal", "field_distance"),
    ("wjaveno", "Javelin Throw", "women", "non-legal", "field_distance"),
    # ---------- WOMEN — combined / relays ----------
    ("whepaok", "Heptathlon", "women", "legal", "combined_points"),
    ("whepano", "Heptathlon", "women", "non-legal", "combined_points"),
    ("w4x100ok", "4x100m Relay", "women", "legal", "relay"),
    ("w4x400ok", "4x400m Relay", "women", "legal", "relay"),
    ("w4x800ok", "4x800m Relay", "women", "legal", "relay"),
    ("w4x1500ok", "4x1500m Relay", "women", "legal", "relay"),
    ("w4x100no", "4x100m Relay", "women", "non-legal", "relay"),
    ("w4x400no", "4x400m Relay", "women", "non-legal", "relay"),
    ("w4x800no", "4x800m Relay", "women", "non-legal", "relay"),
    # ---------- MIXED ----------
    ("x4x400ok", "4x400m Relay", "mixed", "legal", "relay"),
    ("x4x400no", "4x400m Relay", "mixed", "non-legal", "relay"),
)


EVENTS: tuple[Event, ...] = tuple(
    Event(slug=s, label=lbl, sex=x, legality=g, family=f) for (s, lbl, x, g, f) in _RAW
)


def by_slug(slug: str) -> Event:
    for e in EVENTS:
        if e.slug == slug:
            return e
    raise KeyError(slug)
