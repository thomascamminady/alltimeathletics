# /// script
# dependencies = [
#   "duckdb>=1.0",
#   "matplotlib>=3.7",
#   "pyarrow>=12"
# ]
# ///

from pathlib import Path

import duckdb
import matplotlib.pyplot as plt

PARQUET_FILE = Path(__file__).resolve().parent.parent / "data" / "alltime_athletics.parquet"


def pace_mps(mark_value, distance_m):
    """Average pace = distance / time, expressed in m/s.

    (Same physical quantity as 'speed'; the user prefers the label 'pace'.)
    """
    return distance_m / mark_value


def main():
    # -------------------------------
    # Add sprint athletes & elites
    # -------------------------------
    # Names must match the parquet exactly. In particular Mo Farah is filed
    # under "Mohamed Farah", which is why an earlier version of this list
    # silently produced no data for him.
    MALE_ELITES = [
        # Distance runners
        "Joshua Cheptegei",
        "Eliud Kipchoge",
        "Kenenisa Bekele",
        "Hicham El Guerrouj",
        "Haile Gebrselassie",
        "Mohamed Farah",
        "David Rudisha",
        "Selemon Barega",
        "Jakob Ingebrigtsen",
        "Jacob Kiplimo",
        "Yomif Kejelcha",
        "Samuel Tefera",
        # 800m / 800-1500 specialists — fill the 400-to-1500 gap that the
        # pure sprinters and pure distance runners leave behind.
        "Sebastian Coe",
        "Steve Cram",
        "Wilson Kipketer",
        "Nijel Amos",
        "Marco Arop",
        "Emmanuel Wanyonyi",
        "Joaquim Cruz",
        # Sprinters
        "Usain Bolt",
        "Yohan Blake",
        "Justin Gatlin",
        "Michael Johnson",
        "Wayde van Niekerk",
    ]

    # Filter by event_slug rather than label so the script is robust against
    # label-casing changes (the parquet's ``event`` column only updates on
    # re-scrape, but ``event_slug`` is stable).
    DISTANCES = {
        "m_100ok": ("100 Metres", 100),
        "m_200ok": ("200 Metres", 200),
        "m_400ok": ("400 Metres", 400),
        "m_800ok": ("800 Metres", 800),
        "m_1500ok": ("1500 Metres", 1500),
        "m_5000ok": ("5000 Metres", 5000),
        "m_10kok": ("10000 Metres", 10000),
        "mhmaraok": ("Half-Marathon", 21097),
        "mmaraok": ("Marathon", 42195),
    }

    EVENT_SLUGS = list(DISTANCES.keys())

    # -------------------------------
    # DuckDB Queries
    # -------------------------------
    case_distance_m = "\n            ".join(
        f"WHEN '{slug}' THEN {dist}" for slug, (_, dist) in DISTANCES.items()
    )
    slug_in = ",".join(f"'{s}'" for s in EVENT_SLUGS)

    athletes_query = f"""
    SELECT
        name,
        event_slug,
        mark_value,
        CASE event_slug
            {case_distance_m}
        END AS distance_m
    FROM (
        SELECT *
        FROM read_parquet('{PARQUET_FILE}')
        WHERE trim(name) IN ({",".join([f"'{n}'" for n in MALE_ELITES])})
          AND event_slug IN ({slug_in})
          AND DATE_DIFF('year', dob, date) BETWEEN 15 AND 50
        QUALIFY mark_value = MIN(mark_value) OVER (PARTITION BY name, event_slug)
    ) t
    ORDER BY name, distance_m
    """

    wr_query = f"""
    SELECT
        event_slug,
        mark_value,
        CASE event_slug
            {case_distance_m}
        END AS distance_m
    FROM (
        SELECT *
        FROM read_parquet('{PARQUET_FILE}')
        WHERE event_slug IN ({slug_in})
        QUALIFY mark_value = MIN(mark_value) OVER (PARTITION BY event_slug)
    ) t
    ORDER BY distance_m
    """

    # -------------------------------
    # Run queries
    # -------------------------------
    con = duckdb.connect()
    athletes_rows = con.execute(athletes_query).fetchall()
    wr_rows = con.execute(wr_query).fetchall()

    # -------------------------------
    # Process data
    # -------------------------------
    athletes_data = {name: [] for name in MALE_ELITES}
    for name, _slug, mark_value, distance_m in athletes_rows:
        athletes_data[name].append((distance_m, pace_mps(mark_value, distance_m)))

    wr_data = [
        (distance_m, pace_mps(mark_value, distance_m)) for _, mark_value, distance_m in wr_rows
    ]

    # -------------------------------
    # Plotting (OO API)
    # -------------------------------
    fig, ax = plt.subplots(figsize=(12, 6))

    # Athlete curves
    for name, data in athletes_data.items():
        if not data:
            print(f"Warning: No data for {name}")
            continue
        data_sorted = sorted(data, key=lambda x: x[0])
        distances, speeds = zip(*data_sorted, strict=True)
        ax.plot(distances, speeds, marker="o", label=name, linestyle="-")

    # World record curve
    if wr_data:
        wr_data_sorted = sorted(wr_data, key=lambda x: x[0])
        wr_distances, wr_speeds = zip(*wr_data_sorted, strict=True)
        ax.plot(wr_distances, wr_speeds, color="black", marker="x", label="World Record")

    # Log x-axis but only show main event ticks
    event_distances = [DISTANCES[s][1] for s in EVENT_SLUGS]
    event_labels = [DISTANCES[s][0] for s in EVENT_SLUGS]
    ax.set_xscale("log")
    ax.set_xticks(event_distances)
    ax.set_xticklabels(event_labels, rotation=45, ha="right")

    # Clean grid: only major ticks
    ax.grid(True, which="major", linestyle="--", alpha=0.7)
    ax.grid(False, which="minor")

    ax.set_xlabel("Event")
    ax.set_ylabel("Pace (m/s)")
    ax.set_title("Athlete Pace vs Distance with World Record")
    ax.legend(loc="upper right", ncol=2, fontsize=8)
    fig.tight_layout()
    plt.show()


if __name__ == "__main__":
    main()
