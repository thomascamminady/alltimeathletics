"""Canonical catalogue of every event page on alltime-athletics.com.

The catalogue is hardcoded from the link lists on `men.htm` / `women.htm`
because Larsson's index pages are stable and small. If a new event is added,
the scraper will WARN about an unmapped link and you add a row here.

Each entry records the URL slug Larsson uses, the human label, the sex,
the legality (wind-legal/legal-conditions vs all marks including illegal),
and an event *family* that drives parser dispatch and mark normalization.

Families:
- `track_time`           — track race; mark is a duration
- `track_time_wind`      — track race with a wind column (sprints, hurdles)
- `field_distance`       — vertical jump or throw; mark is metres
- `field_distance_wind`  — horizontal jump with wind column
- `combined_points`      — decathlon/heptathlon; mark is points
- `relay`                — team event; rows interleave with team-member lines
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

BASE_URL = "https://www.alltime-athletics.com"

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


@dataclass(frozen=True, slots=True)
class Event:
    slug: str  # URL filename without `.htm`; also the canonical event id
    label: str  # human-readable, e.g. "100 metres"
    sex: Sex
    legality: Legality
    family: Family

    @property
    def url(self) -> str:
        return f"{BASE_URL}/{self.slug}.htm"


# (slug, label, sex, legality, family)
_RAW: tuple[tuple[str, str, Sex, Legality, Family], ...] = (
    # ---------- MEN — wind-legal sprints / hurdles (track_time_wind) ----------
    ("m_100ok", "100 metres", "men", "legal", "track_time_wind"),
    ("m_200ok", "200 metres", "men", "legal", "track_time_wind"),
    ("m_110hok", "110m hurdles", "men", "legal", "track_time_wind"),
    ("m60mok", "60 metres", "men", "legal", "track_time_wind"),
    ("m_60mhok", "60m hurdles", "men", "legal", "track_time_wind"),
    ("m_100yok", "100 yards", "men", "legal", "track_time_wind"),
    ("m_200hok", "200m hurdles", "men", "legal", "track_time_wind"),
    # non-legal counterparts
    ("m100mno", "100 metres", "men", "non-legal", "track_time_wind"),
    ("m_200no", "200 metres", "men", "non-legal", "track_time_wind"),
    ("m_110hno", "110m hurdles", "men", "non-legal", "track_time_wind"),
    ("m60mno", "60 metres", "men", "non-legal", "track_time_wind"),
    ("m_60mhno", "60m hurdles", "men", "non-legal", "track_time_wind"),
    ("m_100yno", "100 yards", "men", "non-legal", "track_time_wind"),
    ("m_200hno", "200m hurdles", "men", "non-legal", "track_time_wind"),
    # ---------- MEN — track times without wind (track_time) ----------
    ("m_400ok", "400 metres", "men", "legal", "track_time"),
    ("m_800ok", "800 metres", "men", "legal", "track_time"),
    ("m_1500ok", "1500 metres", "men", "legal", "track_time"),
    ("m_mileok", "1 Mile", "men", "legal", "track_time"),
    ("m_3000ok", "3000 metres", "men", "legal", "track_time"),
    ("m_5000ok", "5000 metres", "men", "legal", "track_time"),
    ("m_10kok", "10000 metres", "men", "legal", "track_time"),
    ("mhmaraok", "half-marathon", "men", "legal", "track_time"),
    ("mmaraok", "marathon", "men", "legal", "track_time"),
    ("m3000hok", "3000m steeplechase", "men", "legal", "track_time"),
    ("m_400hok", "400m hurdles", "men", "legal", "track_time"),
    ("m_300ok", "300 metres", "men", "legal", "track_time"),
    ("m_600ok", "600 metres", "men", "legal", "track_time"),
    ("m_1000ok", "1000 metres", "men", "legal", "track_time"),
    ("m_2000ok", "2000 metres", "men", "legal", "track_time"),
    ("m_2miok", "2 Miles", "men", "legal", "track_time"),
    ("m1hourok", "One hour run", "men", "legal", "track_time"),
    ("m25kok", "25000m track", "men", "legal", "track_time"),
    ("m30kok", "30000m track", "men", "legal", "track_time"),
    ("m10kroad", "10km road", "men", "legal", "track_time"),
    ("m15kroad", "15km road", "men", "legal", "track_time"),
    ("m10milesroad", "10 miles road", "men", "legal", "track_time"),
    ("m20kroad", "20km road", "men", "legal", "track_time"),
    ("m30kroad", "30km road", "men", "legal", "track_time"),
    ("m100km", "100km road", "men", "legal", "track_time"),
    ("m2000hok", "2000m steeplechase", "men", "legal", "track_time"),
    ("m20kwok", "20km race walk", "men", "legal", "track_time"),
    ("mHalf-Marathonwok", "Half-marathon walk", "men", "legal", "track_time"),
    ("m35kwok", "35km race walk", "men", "legal", "track_time"),
    ("m50kwok", "50km race walk", "men", "legal", "track_time"),
    ("mMarathonwok", "Marathon walk", "men", "legal", "track_time"),
    ("m10kwok", "10000m track walk", "men", "legal", "track_time"),
    # non-legal
    ("m_400no", "400 metres", "men", "non-legal", "track_time"),
    ("m_800no", "800 metres", "men", "non-legal", "track_time"),
    ("m_1500no", "1500 metres", "men", "non-legal", "track_time"),
    ("m_mileno", "1 Mile", "men", "non-legal", "track_time"),
    ("m_3000no", "3000 metres", "men", "non-legal", "track_time"),
    ("m_5000no", "5000 metres", "men", "non-legal", "track_time"),
    ("m_10kno", "10000 metres", "men", "non-legal", "track_time"),
    ("mhmarano", "half-marathon", "men", "non-legal", "track_time"),
    ("mmarano", "marathon", "men", "non-legal", "track_time"),
    ("m3000hno", "3000m steeplechase", "men", "non-legal", "track_time"),
    ("m_400hno", "400m hurdles", "men", "non-legal", "track_time"),
    ("m_300no", "300 metres", "men", "non-legal", "track_time"),
    ("m_600no", "600 metres", "men", "non-legal", "track_time"),
    ("m_1000no", "1000 metres", "men", "non-legal", "track_time"),
    ("m1hourno", "One hour run", "men", "non-legal", "track_time"),
    ("m10kroadno", "10km road", "men", "non-legal", "track_time"),
    ("m15kroadno", "15km road", "men", "non-legal", "track_time"),
    ("m10milesroadno", "10 miles road", "men", "non-legal", "track_time"),
    ("m20kroadno", "20km road", "men", "non-legal", "track_time"),
    ("m30kroadno", "30km road", "men", "non-legal", "track_time"),
    ("m2000hno", "2000m steeplechase", "men", "non-legal", "track_time"),
    ("m20kwno", "20km race walk", "men", "non-legal", "track_time"),
    ("m50kwno", "50km race walk", "men", "non-legal", "track_time"),
    ("m10kwno", "10000m track walk", "men", "non-legal", "track_time"),
    # ---------- MEN — horizontal jumps (field_distance_wind) ----------
    ("mlongok", "Long jump", "men", "legal", "field_distance_wind"),
    ("mtripok", "Triple jump", "men", "legal", "field_distance_wind"),
    ("mlongno", "Long jump", "men", "non-legal", "field_distance_wind"),
    ("mtripno", "Triple jump", "men", "non-legal", "field_distance_wind"),
    # ---------- MEN — vertical jumps + throws (field_distance) ----------
    ("mhighok", "High jump", "men", "legal", "field_distance"),
    ("mpoleok", "Pole vault", "men", "legal", "field_distance"),
    ("mshotok", "Shot put", "men", "legal", "field_distance"),
    ("mdiscok", "Discus throw", "men", "legal", "field_distance"),
    ("mhammok", "Hammer throw", "men", "legal", "field_distance"),
    ("mjaveok", "Javelin throw", "men", "legal", "field_distance"),
    ("mjaveoldok", "Javelin throw (old specs)", "men", "legal", "field_distance"),
    ("mhighno", "High jump", "men", "non-legal", "field_distance"),
    ("mpoleno", "Pole vault", "men", "non-legal", "field_distance"),
    ("mshotno", "Shot put", "men", "non-legal", "field_distance"),
    ("mdiscno", "Discus throw", "men", "non-legal", "field_distance"),
    ("mhammno", "Hammer throw", "men", "non-legal", "field_distance"),
    ("mjaveno", "Javelin throw", "men", "non-legal", "field_distance"),
    ("mjaveoldno", "Javelin throw (old specs)", "men", "non-legal", "field_distance"),
    # ---------- MEN — combined / relays ----------
    ("mdecaok", "Decathlon", "men", "legal", "combined_points"),
    ("mdecano", "Decathlon", "men", "non-legal", "combined_points"),
    ("m4x100ok", "4x100m relay", "men", "legal", "relay"),
    ("m4x400ok", "4x400m relay", "men", "legal", "relay"),
    ("m4x200ok", "4x200m relay", "men", "legal", "relay"),
    ("m4x800ok", "4x800m relay", "men", "legal", "relay"),
    ("m4x1500ok", "4x1500m relay", "men", "legal", "relay"),
    ("m_4xmileok", "4x1 Mile relay", "men", "legal", "relay"),
    ("m4x100no", "4x100m relay", "men", "non-legal", "relay"),
    ("m4x400no", "4x400m relay", "men", "non-legal", "relay"),
    ("m4x200no", "4x200m relay", "men", "non-legal", "relay"),
    ("m4x800no", "4x800m relay", "men", "non-legal", "relay"),
    # ---------- WOMEN — wind-legal sprints / hurdles ----------
    ("w_100ok", "100 metres", "women", "legal", "track_time_wind"),
    ("w_200ok", "200 metres", "women", "legal", "track_time_wind"),
    ("w_100hok", "100m hurdles", "women", "legal", "track_time_wind"),
    ("w60mok", "60 metres", "women", "legal", "track_time_wind"),
    ("w_60mhok", "60m hurdles", "women", "legal", "track_time_wind"),
    ("w_100no", "100 metres", "women", "non-legal", "track_time_wind"),
    ("w_200no", "200 metres", "women", "non-legal", "track_time_wind"),
    ("w_100hno", "100m hurdles", "women", "non-legal", "track_time_wind"),
    ("w60mno", "60 metres", "women", "non-legal", "track_time_wind"),
    ("w_60mhno", "60m hurdles", "women", "non-legal", "track_time_wind"),
    # ---------- WOMEN — track times no wind ----------
    ("w_400ok", "400 metres", "women", "legal", "track_time"),
    ("w_800ok", "800 metres", "women", "legal", "track_time"),
    ("w_1500ok", "1500 metres", "women", "legal", "track_time"),
    ("w_mileok", "1 Mile", "women", "legal", "track_time"),
    ("w_3000ok", "3000 metres", "women", "legal", "track_time"),
    ("w_5000ok", "5000 metres", "women", "legal", "track_time"),
    ("w_10kok", "10000 metres", "women", "legal", "track_time"),
    ("whmaraok", "half-marathon", "women", "legal", "track_time"),
    ("wmaraok", "marathon", "women", "legal", "track_time"),
    ("w3000hok", "3000m steeplechase", "women", "legal", "track_time"),
    ("w_400hok", "400m hurdles", "women", "legal", "track_time"),
    ("w_300ok", "300 metres", "women", "legal", "track_time"),
    ("w_600ok", "600 metres", "women", "legal", "track_time"),
    ("w_1000ok", "1000 metres", "women", "legal", "track_time"),
    ("w_2000ok", "2000 metres", "women", "legal", "track_time"),
    ("w2milesok", "2 Miles", "women", "legal", "track_time"),
    ("w10kroad", "10km road", "women", "legal", "track_time"),
    ("w15kroad", "15km road", "women", "legal", "track_time"),
    ("w10milesroad", "10 miles road", "women", "legal", "track_time"),
    ("w20kroad", "20km road", "women", "legal", "track_time"),
    ("w30kroad", "30km road", "women", "legal", "track_time"),
    ("w2000hok", "2000m steeplechase", "women", "legal", "track_time"),
    ("w20kwok", "20km race walk", "women", "legal", "track_time"),
    ("wHalf-Marathonwok", "Half-marathon walk", "women", "legal", "track_time"),
    ("w35kwok", "35km race walk", "women", "legal", "track_time"),
    ("w50kwok", "50km race walk", "women", "legal", "track_time"),
    ("wMarathonwok", "Marathon walk", "women", "legal", "track_time"),
    ("w5kwok", "5000m track walk", "women", "legal", "track_time"),
    ("w10kwok", "10km race walk", "women", "legal", "track_time"),
    # non-legal
    ("w_400no", "400 metres", "women", "non-legal", "track_time"),
    ("w_800no", "800 metres", "women", "non-legal", "track_time"),
    ("w_1500no", "1500 metres", "women", "non-legal", "track_time"),
    ("w_mileno", "1 Mile", "women", "non-legal", "track_time"),
    ("w_3000no", "3000 metres", "women", "non-legal", "track_time"),
    ("w_5000no", "5000 metres", "women", "non-legal", "track_time"),
    ("w_10kno", "10000 metres", "women", "non-legal", "track_time"),
    ("whmarano", "half-marathon", "women", "non-legal", "track_time"),
    ("wmarano", "marathon", "women", "non-legal", "track_time"),
    ("w3000hno", "3000m steeplechase", "women", "non-legal", "track_time"),
    ("w_400hno", "400m hurdles", "women", "non-legal", "track_time"),
    ("w_300no", "300 metres", "women", "non-legal", "track_time"),
    ("w_1000no", "1000 metres", "women", "non-legal", "track_time"),
    ("w_2000no", "2000 metres", "women", "non-legal", "track_time"),
    ("w2milesno", "2 Miles", "women", "non-legal", "track_time"),
    ("w10kroadno", "10km road", "women", "non-legal", "track_time"),
    ("w15kroadno", "15km road", "women", "non-legal", "track_time"),
    ("w20kroadno", "20km road", "women", "non-legal", "track_time"),
    ("w30kroadno", "30km road", "women", "non-legal", "track_time"),
    ("w2000hno", "2000m steeplechase", "women", "non-legal", "track_time"),
    ("w20kwno", "20km race walk", "women", "non-legal", "track_time"),
    ("w5kwno", "5000m track walk", "women", "non-legal", "track_time"),
    ("w10kwno", "10km race walk", "women", "non-legal", "track_time"),
    # ---------- WOMEN — horizontal jumps ----------
    ("wlongok", "Long jump", "women", "legal", "field_distance_wind"),
    ("wtripleok", "Triple jump", "women", "legal", "field_distance_wind"),
    ("wlongno", "Long jump", "women", "non-legal", "field_distance_wind"),
    ("wtripleno", "Triple jump", "women", "non-legal", "field_distance_wind"),
    # ---------- WOMEN — vertical jumps + throws ----------
    ("whighok", "High jump", "women", "legal", "field_distance"),
    ("wpoleok", "Pole vault", "women", "legal", "field_distance"),
    ("wshotok", "Shot put", "women", "legal", "field_distance"),
    ("wdiscok", "Discus throw", "women", "legal", "field_distance"),
    ("whammok", "Hammer throw", "women", "legal", "field_distance"),
    ("wjaveok", "Javelin throw", "women", "legal", "field_distance"),
    ("wjaveoldok", "Javelin throw (old specs)", "women", "legal", "field_distance"),
    ("whighno", "High jump", "women", "non-legal", "field_distance"),
    ("wpoleno", "Pole vault", "women", "non-legal", "field_distance"),
    ("wshotno", "Shot put", "women", "non-legal", "field_distance"),
    ("wdiscno", "Discus throw", "women", "non-legal", "field_distance"),
    ("whammno", "Hammer throw", "women", "non-legal", "field_distance"),
    ("wjaveno", "Javelin throw", "women", "non-legal", "field_distance"),
    # ---------- WOMEN — combined / relays ----------
    ("whepaok", "Heptathlon", "women", "legal", "combined_points"),
    ("whepano", "Heptathlon", "women", "non-legal", "combined_points"),
    ("w4x100ok", "4x100m relay", "women", "legal", "relay"),
    ("w4x400ok", "4x400m relay", "women", "legal", "relay"),
    ("w4x800ok", "4x800m relay", "women", "legal", "relay"),
    ("w4x1500ok", "4x1500m relay", "women", "legal", "relay"),
    ("w4x100no", "4x100m relay", "women", "non-legal", "relay"),
    ("w4x400no", "4x400m relay", "women", "non-legal", "relay"),
    ("w4x800no", "4x800m relay", "women", "non-legal", "relay"),
    # ---------- MIXED ----------
    ("x4x400ok", "4x400m relay", "mixed", "legal", "relay"),
    ("x4x400no", "4x400m relay", "mixed", "non-legal", "relay"),
)


EVENTS: tuple[Event, ...] = tuple(
    Event(slug=s, label=lbl, sex=x, legality=g, family=f) for (s, lbl, x, g, f) in _RAW
)


def by_slug(slug: str) -> Event:
    for e in EVENTS:
        if e.slug == slug:
            return e
    raise KeyError(slug)
