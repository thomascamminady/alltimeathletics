# /// script
# dependencies = [
#   "duckdb>=1.0",
#   "matplotlib>=3.7",
#   "pyarrow>=12"
# ]
# ///

import duckdb
import matplotlib.pyplot as plt


def pace_mps(mark_value, distance_m):
    """Average pace = distance / time, expressed in m/s.

    (Same physical quantity as 'speed'; the user prefers the label 'pace'.)
    """
    return distance_m / mark_value


def main():
    PARQUET_FILE = "alltime_athletics.parquet"

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

    DISTANCES = {
        "100 metres": 100,
        "200 metres": 200,
        "400 metres": 400,
        "800 metres": 800,
        "1500 metres": 1500,
        "5000 metres": 5000,
        "10000 metres": 10000,
        "half-marathon": 21097,
        "marathon": 42195,
    }

    EVENTS = list(DISTANCES.keys())

    # -------------------------------
    # DuckDB Queries
    # -------------------------------
    athletes_query = f"""
    SELECT
        name,
        event,
        mark_value,
        CASE event
            WHEN '100 metres' THEN 100
            WHEN '200 metres' THEN 200
            WHEN '400 metres' THEN 400
            WHEN '800 metres' THEN 800
            WHEN '1500 metres' THEN 1500
            WHEN '5000 metres' THEN 5000
            WHEN '10000 metres' THEN 10000
            WHEN 'half-marathon' THEN 21097
            WHEN 'marathon' THEN 42195
        END AS distance_m
    FROM (
        SELECT *
        FROM read_parquet('{PARQUET_FILE}')
        WHERE trim(name) IN ({",".join([f"'{n}'" for n in MALE_ELITES])})
          AND event IN ({",".join([f"'{e}'" for e in EVENTS])})
          AND DATE_DIFF('year', dob, date) BETWEEN 15 AND 50
        QUALIFY mark_value = MIN(mark_value) OVER (PARTITION BY name, event)
    ) t
    ORDER BY name, distance_m
    """

    wr_query = f"""
    SELECT
        event,
        mark_value,
        CASE event
            WHEN '100 metres' THEN 100
            WHEN '200 metres' THEN 200
            WHEN '400 metres' THEN 400
            WHEN '800 metres' THEN 800
            WHEN '1500 metres' THEN 1500
            WHEN '5000 metres' THEN 5000
            WHEN '10000 metres' THEN 10000
            WHEN 'half-marathon' THEN 21097
            WHEN 'marathon' THEN 42195
        END AS distance_m
    FROM (
        SELECT *
        FROM read_parquet('{PARQUET_FILE}')
        WHERE event IN ({",".join([f"'{e}'" for e in EVENTS])})
        QUALIFY mark_value = MIN(mark_value) OVER (PARTITION BY event)
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
    for name, event, mark_value, distance_m in athletes_rows:
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
        distances, speeds = zip(*data_sorted)
        ax.plot(distances, speeds, marker="o", label=name, linestyle="-")

    # World record curve
    if wr_data:
        wr_data_sorted = sorted(wr_data, key=lambda x: x[0])
        wr_distances, wr_speeds = zip(*wr_data_sorted)
        ax.plot(wr_distances, wr_speeds, color="black", marker="x", label="World Record")

    # Log x-axis but only show main event ticks
    event_distances = [DISTANCES[e] for e in EVENTS]
    ax.set_xscale("log")
    ax.set_xticks(event_distances)
    ax.set_xticklabels(EVENTS, rotation=45, ha="right")

    # Clean grid: only major ticks
    ax.grid(True, which="major", linestyle="--", alpha=0.7)
    ax.grid(False, which="minor")

    ax.set_xlabel("Event")
    ax.set_ylabel("Pace (m/s)")
    ax.set_title("Athlete Pace vs Distance with World Record")
    ax.legend()
    fig.tight_layout()
    plt.show()


if __name__ == "__main__":
    main()
