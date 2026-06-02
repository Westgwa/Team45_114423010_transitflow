"""
TransitFlow — Neo4j Seeder (UNWIND Batch Optimized)

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


def _seed_metro_stations_batch(session, stations: list[dict]):
    session.run(
        """
        UNWIND $stations AS s
        MERGE (n:Station {station_id: s.station_id})
        SET n:MetroStation,
            n.name = coalesce(s.name, s.station_id),
            n.network = "metro",
            n.lines = coalesce(s.lines, []),
            n.is_interchange_metro = coalesce(s.is_interchange_metro, false),
            n.interchange_metro_lines = coalesce(s.interchange_metro_lines, []),
            n.is_interchange_national_rail = coalesce(s.is_interchange_national_rail, false),
            n.interchange_national_rail_station_id = coalesce(s.interchange_national_rail_station_id, s.national_rail_station_id, s.interchange_station_id, null)
        """,
        stations=stations
    )


def _seed_rail_stations_batch(session, stations: list[dict]):
    session.run(
        """
        UNWIND $stations AS s
        MERGE (n:Station {station_id: s.station_id})
        SET n:NationalRailStation,
            n.name = coalesce(s.name, s.station_id),
            n.network = "national_rail",
            n.lines = coalesce(s.lines, []),
            n.is_interchange_national_rail = coalesce(s.is_interchange_national_rail, false),
            n.interchange_national_rail_lines = coalesce(s.interchange_national_rail_lines, []),
            n.is_interchange_metro = coalesce(s.is_interchange_metro, false),
            n.interchange_metro_station_id = coalesce(s.interchange_metro_station_id, s.metro_station_id, s.interchange_station_id, null)
        """,
        stations=stations
    )


def _seed_connections_batch(session, connections: list[dict]):
    session.run(
        """
        UNWIND $connections AS c
        MATCH (a:Station {station_id: c.from_id})
        MATCH (b:Station {station_id: c.to_id})
        MERGE (a)-[r:CONNECTS_TO {line: c.line, network: c.network}]->(b)
        SET r.travel_time_min = toInteger(c.travel_time_min),
            r.fare = CASE
                WHEN c.network = "metro" THEN 1.0
                ELSE toFloat(c.travel_time_min) * 0.35
            END
        """,
        connections=connections
    )


def _seed_interchanges_batch(session, interchanges: list[dict]) -> list[tuple[str, str]]:
    result = session.run(
        """
        UNWIND $interchanges AS i
        MATCH (m:Station {station_id: i.metro_id})
        MATCH (r:Station {station_id: i.rail_id})

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
            
        RETURN m.station_id AS metro_id, r.station_id AS rail_id
        """,
        interchanges=interchanges
    )
    return [(rec["metro_id"], rec["rail_id"]) for rec in result]


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

            # 1. Create metro stations (Batch Insert)
            _seed_metro_stations_batch(session, metro_stations)
            print(f"Created {len(metro_stations)} metro stations")

            # 2. Create national rail stations (Batch Insert)
            _seed_rail_stations_batch(session, rail_stations)
            print(f"Created {len(rail_stations)} national rail stations")

            # 3. Create metro links (In-Memory Flattening -> Batch Insert)
            metro_links = []
            for station in metro_stations:
                from_id = station["station_id"]
                for adj in station.get("adjacent_stations", []):
                    to_id = adj.get("station_id")
                    if not to_id:
                        continue
                    metro_links.append({
                        "from_id": from_id,
                        "to_id": to_id,
                        "line": adj.get("line", "UNKNOWN"),
                        "travel_time_min": adj.get("travel_time_min", 1),
                        "network": "metro"
                    })
            _seed_connections_batch(session, metro_links)
            print(f"Created {len(metro_links)} metro links")

            # 4. Create national rail links (In-Memory Flattening -> Batch Insert)
            rail_links = []
            for station in rail_stations:
                from_id = station["station_id"]
                for adj in station.get("adjacent_stations", []):
                    to_id = adj.get("station_id")
                    if not to_id:
                        continue
                    rail_links.append({
                        "from_id": from_id,
                        "to_id": to_id,
                        "line": adj.get("line", "UNKNOWN"),
                        "travel_time_min": adj.get("travel_time_min", 1),
                        "network": "national_rail"
                    })
            _seed_connections_batch(session, rail_links)
            print(f"Created {len(rail_links)} national rail links")

            # 4.5 Extra fallback / disruption alternative rail links
            extra_rail_links_raw = [
                ("NR02", "NR06", "NR_ALT", 18),
                ("NR06", "NR05", "NR_ALT", 20),
                ("NR05", "NR06", "NR_ALT", 20),
                ("NR06", "NR02", "NR_ALT", 18),
            ]
            extra_links = []
            for from_id, to_id, line, travel_time_min in extra_rail_links_raw:
                extra_links.append({
                    "from_id": from_id,
                    "to_id": to_id,
                    "line": line,
                    "travel_time_min": travel_time_min,
                    "network": "national_rail"
                })
            _seed_connections_batch(session, extra_links)
            print(f"Created {len(extra_links)} extra alternative rail links")

            # 5. Create metro <-> national rail interchange relationships
            interchange_pairs = _extract_interchange_pairs_from_data(
                metro_stations=metro_stations,
                rail_stations=rail_stations,
            )

            print("Creating interchange pairs:")
            interchanges_input = [{"metro_id": m, "rail_id": r} for m, r in interchange_pairs]
            successful_pairs = _seed_interchanges_batch(session, interchanges_input)

            for m, r in interchange_pairs:
                if (m, r) in successful_pairs:
                    print(f"  {m} <-> {r}")
                else:
                    print(f"  skipped {m} <-> {r}: node not found")

            print(f"Created {len(successful_pairs)} metro-national rail interchange pairs")

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