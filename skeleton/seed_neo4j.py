"""
TransitFlow — Neo4j Seeder

Creates a complete graph schema for:
- Metro stations
- National rail stations
- Metro connections
- National rail connections
- Extra alternative national rail links
- Metro <-> national rail interchange links
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, ".")

from neo4j import GraphDatabase
from skeleton.config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD


_DATA_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "train-mock-data")
)


def _load(filename: str):
    path = os.path.join(_DATA_DIR, filename)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _create_constraints(session):
    session.run(
        """
        CREATE CONSTRAINT station_id_unique IF NOT EXISTS
        FOR (s:Station)
        REQUIRE s.station_id IS UNIQUE
        """
    )


def _merge_metro_station(session, station: dict):
    session.run(
        """
        MERGE (s:Station {station_id: $station_id})
        SET s:MetroStation,
            s.name = $name,
            s.network = "metro",
            s.lines = $lines,
            s.is_interchange_metro = $is_interchange_metro,
            s.interchange_metro_lines = $interchange_metro_lines,
            s.is_interchange_national_rail = $is_interchange_national_rail,
            s.interchange_national_rail_station_id = $interchange_national_rail_station_id
        """,
        station_id=station["station_id"],
        name=station.get("name", station["station_id"]),
        lines=station.get("lines", []),
        is_interchange_metro=station.get("is_interchange_metro", False),
        interchange_metro_lines=station.get("interchange_metro_lines", []),
        is_interchange_national_rail=station.get("is_interchange_national_rail", False),
        interchange_national_rail_station_id=(
            station.get("interchange_national_rail_station_id")
            or station.get("national_rail_station_id")
            or station.get("interchange_station_id")
        ),
    )


def _merge_rail_station(session, station: dict):
    session.run(
        """
        MERGE (s:Station {station_id: $station_id})
        SET s:NationalRailStation,
            s.name = $name,
            s.network = "national_rail",
            s.lines = $lines,
            s.is_interchange_national_rail = $is_interchange_national_rail,
            s.interchange_national_rail_lines = $interchange_national_rail_lines,
            s.is_interchange_metro = $is_interchange_metro,
            s.interchange_metro_station_id = $interchange_metro_station_id
        """,
        station_id=station["station_id"],
        name=station.get("name", station["station_id"]),
        lines=station.get("lines", []),
        is_interchange_national_rail=station.get("is_interchange_national_rail", False),
        interchange_national_rail_lines=station.get("interchange_national_rail_lines", []),
        is_interchange_metro=station.get("is_interchange_metro", False),
        interchange_metro_station_id=(
            station.get("interchange_metro_station_id")
            or station.get("metro_station_id")
            or station.get("interchange_station_id")
        ),
    )


def _merge_connection(
    session,
    from_id: str,
    to_id: str,
    line: str,
    travel_time_min: int,
    network: str,
):
    """
    Create one directed connection.
    JSON usually already lists both directions.
    MERGE prevents duplicates.
    """
    session.run(
        """
        MATCH (a:Station {station_id: $from_id})
        MATCH (b:Station {station_id: $to_id})
        MERGE (a)-[r:CONNECTS_TO {line: $line, network: $network}]->(b)
        SET r.travel_time_min = $travel_time_min,
            r.fare = CASE
                WHEN $network = "metro" THEN 1.0
                ELSE toFloat($travel_time_min) * 0.35
            END
        """,
        from_id=from_id,
        to_id=to_id,
        line=line,
        travel_time_min=int(travel_time_min),
        network=network,
    )


def _merge_interchange(session, metro_id: str, rail_id: str) -> bool:
    """
    Create bidirectional metro <-> national rail interchange relationship.
    Returns True only if both nodes exist and the relationship can be created.
    """
    result = session.run(
        """
        MATCH (m:Station {station_id: $metro_id})
        MATCH (r:Station {station_id: $rail_id})

        MERGE (m)-[a:INTERCHANGES_WITH]->(r)
        SET a.travel_time_min = 5,
            a.fare = 0.0,
            a.network = "interchange",
            a.line = "INTERCHANGE"

        MERGE (r)-[b:INTERCHANGES_WITH]->(m)
        SET b.travel_time_min = 5,
            b.fare = 0.0,
            b.network = "interchange",
            b.line = "INTERCHANGE"

        RETURN count(a) + count(b) AS created_count
        """,
        metro_id=metro_id,
        rail_id=rail_id,
    ).single()

    return bool(result and result["created_count"] > 0)


def _extract_interchange_pairs_from_data(
    metro_stations: list[dict],
    rail_stations: list[dict],
):
    """
    Try to discover interchange pairs from JSON fields.
    Also includes fallback hard-coded official interchange pairs.
    """
    pairs: list[tuple[str, str]] = []

    # 1. Try from metro station fields
    for station in metro_stations:
        metro_id = station.get("station_id")

        rail_id = (
            station.get("interchange_national_rail_station_id")
            or station.get("national_rail_station_id")
            or station.get("interchange_station_id")
        )

        if (
            metro_id
            and rail_id
            and str(metro_id).upper().startswith("MS")
            and str(rail_id).upper().startswith("NR")
        ):
            pairs.append((str(metro_id).upper(), str(rail_id).upper()))

    # 2. Try from national rail station fields
    for station in rail_stations:
        rail_id = station.get("station_id")

        metro_id = (
            station.get("interchange_metro_station_id")
            or station.get("metro_station_id")
            or station.get("interchange_station_id")
        )

        if (
            metro_id
            and rail_id
            and str(metro_id).upper().startswith("MS")
            and str(rail_id).upper().startswith("NR")
        ):
            pairs.append((str(metro_id).upper(), str(rail_id).upper()))

    # 3. Fallback official interchange pairs
    fallback_pairs = [
        ("MS01", "NR01"),
        ("MS07", "NR03"),
        ("MS15", "NR07"),
    ]

    for pair in fallback_pairs:
        if pair not in pairs:
            pairs.append(pair)

    # Remove duplicates while preserving order
    unique_pairs = []
    for pair in pairs:
        if pair not in unique_pairs:
            unique_pairs.append(pair)

    return unique_pairs


def seed():
    metro_stations = _load("metro_stations.json")
    rail_stations = _load("national_rail_stations.json")

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

    try:
        with driver.session() as session:
            session.run("MATCH (n) DETACH DELETE n")
            print("Cleared existing graph data")

            _create_constraints(session)

            # 1. Create metro stations
            for station in metro_stations:
                _merge_metro_station(session, station)

            print(f"Created {len(metro_stations)} metro stations")

            # 2. Create national rail stations
            for station in rail_stations:
                _merge_rail_station(session, station)

            print(f"Created {len(rail_stations)} national rail stations")

            # 3. Create metro links
            metro_links = 0

            for station in metro_stations:
                from_id = station["station_id"]

                for adj in station.get("adjacent_stations", []):
                    to_id = adj.get("station_id")
                    if not to_id:
                        continue

                    _merge_connection(
                        session=session,
                        from_id=from_id,
                        to_id=to_id,
                        line=adj.get("line", "UNKNOWN"),
                        travel_time_min=adj.get("travel_time_min", 1),
                        network="metro",
                    )

                    metro_links += 1

            print(f"Created {metro_links} metro links")

            # 4. Create national rail links
            rail_links = 0

            for station in rail_stations:
                from_id = station["station_id"]

                for adj in station.get("adjacent_stations", []):
                    to_id = adj.get("station_id")
                    if not to_id:
                        continue

                    _merge_connection(
                        session=session,
                        from_id=from_id,
                        to_id=to_id,
                        line=adj.get("line", "UNKNOWN"),
                        travel_time_min=adj.get("travel_time_min", 1),
                        network="national_rail",
                    )

                    rail_links += 1

            print(f"Created {rail_links} national rail links")

            # 4.5 Extra fallback / disruption alternative rail links
            # Used to support alternative route queries when NR03 is closed.
            # This creates a backup path:
            # NR01 -> NR02 -> NR06 -> NR05
            extra_rail_links = [
                ("NR02", "NR06", "NR_ALT", 18),
                ("NR06", "NR05", "NR_ALT", 20),
                ("NR05", "NR06", "NR_ALT", 20),
                ("NR06", "NR02", "NR_ALT", 18),
            ]

            extra_links_count = 0

            for from_id, to_id, line, travel_time_min in extra_rail_links:
                _merge_connection(
                    session=session,
                    from_id=from_id,
                    to_id=to_id,
                    line=line,
                    travel_time_min=travel_time_min,
                    network="national_rail",
                )
                extra_links_count += 1

            print(f"Created {extra_links_count} extra alternative rail links")

            # 5. Create metro <-> national rail interchange relationships
            interchange_pairs = _extract_interchange_pairs_from_data(
                metro_stations=metro_stations,
                rail_stations=rail_stations,
            )

            interchange_count = 0

            print("Creating interchange pairs:")

            for metro_id, rail_id in interchange_pairs:
                ok = _merge_interchange(session, metro_id, rail_id)

                if ok:
                    interchange_count += 1
                    print(f"  {metro_id} <-> {rail_id}")
                else:
                    print(f"  skipped {metro_id} <-> {rail_id}: node not found")

            print(f"Created {interchange_count} metro-national rail interchange pairs")

            # 6. Final counts
            total_nodes = session.run(
                "MATCH (n) RETURN count(n) AS total"
            ).single()["total"]

            total_rels = session.run(
                "MATCH ()-[r]->() RETURN count(r) AS total"
            ).single()["total"]

            total_interchanges = session.run(
                "MATCH ()-[r:INTERCHANGES_WITH]->() RETURN count(r) AS total"
            ).single()["total"]

            total_alt_links = session.run(
                """
                MATCH ()-[r:CONNECTS_TO {line: "NR_ALT"}]->()
                RETURN count(r) AS total
                """
            ).single()["total"]

            print(f"Total nodes: {total_nodes}")
            print(f"Total relationships: {total_rels}")
            print(f"Total INTERCHANGES_WITH relationships: {total_interchanges}")
            print(f"Total NR_ALT alternative links: {total_alt_links}")

    finally:
        driver.close()

    print("\nNeo4j graph seeded successfully.")
    print("Open http://localhost:7475 to explore the graph.")


if __name__ == "__main__":
    print("Connecting to Neo4j...")
    seed()