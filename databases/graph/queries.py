"""
TransitFlow — Neo4j Graph Database Layer
"""

from __future__ import annotations

from neo4j import GraphDatabase
from skeleton.config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD


def _driver():
    return GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))


def example_count_nodes() -> int:
    with _driver() as driver:
        with driver.session() as session:
            result = session.run("MATCH (n) RETURN count(n) AS total")
            return result.single()["total"]


def _network_filter(network: str) -> str:
    if network == "metro":
        return 'ALL(r IN relationships(p) WHERE r.network IN ["metro", "interchange"])'
    if network in {"rail", "national_rail"}:
        return 'ALL(r IN relationships(p) WHERE r.network IN ["national_rail", "interchange"])'
    return "true"


def _format_route(record, origin_id, destination_id, value_key, output_key):
    if record is None:
        return {
            "found": False,
            "origin_id": origin_id,
            "destination_id": destination_id,
            "message": "No route found.",
        }

    return {
        "found": True,
        "origin_id": origin_id,
        "destination_id": destination_id,
        output_key: record[value_key],
        "path": record["stations"],
        "legs": record["legs"],
    }


def query_shortest_route(origin_id: str, destination_id: str, network: str = "auto") -> dict:
    network_condition = _network_filter(network)

    cypher = f"""
    MATCH (start:Station {{station_id: $origin_id}})
    MATCH (end:Station {{station_id: $destination_id}})
    MATCH p = (start)-[:CONNECTS_TO|INTERCHANGES_WITH*1..20]->(end)
    WHERE {network_condition}
    WITH p,
         reduce(total = 0, r IN relationships(p) |
            total + coalesce(r.travel_time_min, 1)
         ) AS total_time
    ORDER BY total_time ASC, length(p) ASC
    LIMIT 1
    RETURN
        total_time,
        [n IN nodes(p) | {{
            station_id: n.station_id,
            name: n.name,
            network: n.network,
            lines: n.lines
        }}] AS stations,
        [r IN relationships(p) | {{
            from: startNode(r).station_id,
            to: endNode(r).station_id,
            line: r.line,
            network: r.network,
            travel_time_min: coalesce(r.travel_time_min, 1)
        }}] AS legs
    """

    with _driver() as driver:
        with driver.session() as session:
            record = session.run(
                cypher,
                origin_id=origin_id,
                destination_id=destination_id,
            ).single()

            return _format_route(
                record=record,
                origin_id=origin_id,
                destination_id=destination_id,
                value_key="total_time",
                output_key="total_time_min",
            )


def query_cheapest_route(
    origin_id: str,
    destination_id: str,
    network: str = "auto",
    fare_class: str = "standard",
) -> dict:
    network_condition = _network_filter(network)

    fare_multiplier = 1.0
    if fare_class == "first":
        fare_multiplier = 1.8

    cypher = f"""
    MATCH (start:Station {{station_id: $origin_id}})
    MATCH (end:Station {{station_id: $destination_id}})
    MATCH p = (start)-[:CONNECTS_TO|INTERCHANGES_WITH*1..20]->(end)
    WHERE {network_condition}
    WITH p,
         reduce(total = 0.0, r IN relationships(p) |
            total + coalesce(r.fare, 1.0)
         ) * $fare_multiplier AS total_fare
    ORDER BY total_fare ASC, length(p) ASC
    LIMIT 1
    RETURN
        round(total_fare * 100) / 100 AS total_fare,
        [n IN nodes(p) | {{
            station_id: n.station_id,
            name: n.name,
            network: n.network,
            lines: n.lines
        }}] AS stations,
        [r IN relationships(p) | {{
            from: startNode(r).station_id,
            to: endNode(r).station_id,
            line: r.line,
            network: r.network,
            fare: coalesce(r.fare, 1.0),
            travel_time_min: coalesce(r.travel_time_min, 1)
        }}] AS legs
    """

    with _driver() as driver:
        with driver.session() as session:
            record = session.run(
                cypher,
                origin_id=origin_id,
                destination_id=destination_id,
                fare_multiplier=fare_multiplier,
            ).single()

            return _format_route(
                record=record,
                origin_id=origin_id,
                destination_id=destination_id,
                value_key="total_fare",
                output_key="total_fare_usd",
            )


def query_alternative_routes(
    origin_id: str,
    destination_id: str,
    avoid_station_id: str,
    network: str = "auto",
    max_routes: int = 3,
) -> list[dict]:
    """
    Find alternative routes while avoiding a closed station.

    Safer version:
    - Limits path depth to 8 to avoid very slow graph expansion.
    - Avoids duplicated nodes in the same path.
    - Avoids both the closed station and its interchange counterpart.
    """

    origin_id = origin_id.upper()
    destination_id = destination_id.upper()
    avoid_station_id = avoid_station_id.upper()

    interchange_counterparts = {
        "NR01": "MS01",
        "MS01": "NR01",
        "NR03": "MS07",
        "MS07": "NR03",
        "NR07": "MS15",
        "MS15": "NR07",
    }

    avoid_ids = [avoid_station_id]

    if avoid_station_id in interchange_counterparts:
        avoid_ids.append(interchange_counterparts[avoid_station_id])

    cypher = """
    MATCH (start:Station {station_id: $origin_id})
    MATCH (end:Station {station_id: $destination_id})

    MATCH p = (start)-[:CONNECTS_TO|INTERCHANGES_WITH*1..8]-(end)

    WHERE NONE(n IN nodes(p) WHERE n.station_id IN $avoid_ids)
      AND ALL(n IN nodes(p) WHERE single(m IN nodes(p) WHERE m = n))

    WITH p,
         reduce(total = 0, r IN relationships(p) |
            total + coalesce(r.travel_time_min, 1)
         ) AS total_time

    ORDER BY total_time ASC, length(p) ASC
    LIMIT $max_routes

    RETURN
        total_time,
        [n IN nodes(p) | {
            station_id: n.station_id,
            name: n.name,
            network: n.network,
            lines: n.lines
        }] AS stations,
        [r IN relationships(p) | {
            from: startNode(r).station_id,
            to: endNode(r).station_id,
            type: type(r),
            line: r.line,
            network: r.network,
            travel_time_min: coalesce(r.travel_time_min, 1)
        }] AS legs
    """

    with _driver() as driver:
        with driver.session() as session:
            records = session.run(
                cypher,
                origin_id=origin_id,
                destination_id=destination_id,
                avoid_ids=avoid_ids,
                max_routes=max_routes,
            )

            routes = []

            for index, record in enumerate(records, start=1):
                routes.append(
                    {
                        "route_number": index,
                        "origin_id": origin_id,
                        "destination_id": destination_id,
                        "avoid_station_ids": avoid_ids,
                        "total_time_min": record["total_time"],
                        "stations": record["stations"],
                        "legs": record["legs"],
                    }
                )

            return routes

    with _driver() as driver:
        with driver.session() as session:
            records = session.run(
                cypher,
                origin_id=origin_id,
                destination_id=destination_id,
                avoid_ids=avoid_ids,
                max_routes=max_routes,
            )

            routes = []

            for index, record in enumerate(records, start=1):
                routes.append(
                    {
                        "route_number": index,
                        "origin_id": origin_id,
                        "destination_id": destination_id,
                        "avoid_station_ids": avoid_ids,
                        "total_time_min": record["total_time"],
                        "stations": record["stations"],
                        "legs": record["legs"],
                    }
                )

            return routes
    with _driver() as driver:
        with driver.session() as session:
            records = session.run(
                cypher,
                origin_id=origin_id,
                destination_id=destination_id,
                avoid_ids=avoid_ids,
                max_routes=max_routes,
            )

            routes = []

            for index, record in enumerate(records, start=1):
                routes.append(
                    {
                        "route_number": index,
                        "origin_id": origin_id,
                        "destination_id": destination_id,
                        "avoid_station_ids": avoid_ids,
                        "total_time_min": record["total_time"],
                        "stations": record["stations"],
                        "legs": record["legs"],
                    }
                )

            return routes

    with _driver() as driver:
        with driver.session() as session:
            records = session.run(
                cypher,
                origin_id=origin_id,
                destination_id=destination_id,
                avoid_station_id=avoid_station_id,
                max_routes=max_routes,
            )

            routes = []
            for record in records:
                routes.append({
                    "total_time_min": record["total_time"],
                    "legs": record["legs"],
                })

            return routes


def query_interchange_path(origin_id: str, destination_id: str) -> dict:
    cypher = """
    MATCH (start:Station {station_id: $origin_id})
    MATCH (end:Station {station_id: $destination_id})
    MATCH p = (start)-[:CONNECTS_TO|INTERCHANGES_WITH*1..20]->(end)
    WHERE ANY(r IN relationships(p) WHERE type(r) = "INTERCHANGES_WITH")
    WITH p,
         reduce(total = 0, r IN relationships(p) |
            total + coalesce(r.travel_time_min, 1)
         ) AS total_time
    ORDER BY total_time ASC, length(p) ASC
    LIMIT 1
    RETURN
        total_time,
        [n IN nodes(p) | {
            station_id: n.station_id,
            name: n.name,
            network: n.network,
            lines: n.lines
        }] AS stations,
        [n IN nodes(p) WHERE n.is_interchange_metro = true OR n.is_interchange_national_rail = true |
            {
                station_id: n.station_id,
                name: n.name,
                network: n.network
            }
        ] AS interchange_points
    """

    with _driver() as driver:
        with driver.session() as session:
            record = session.run(
                cypher,
                origin_id=origin_id,
                destination_id=destination_id,
            ).single()

            if record is None:
                return {
                    "found": False,
                    "origin_id": origin_id,
                    "destination_id": destination_id,
                    "message": "No interchange path found.",
                }

            return {
                "found": True,
                "origin_id": origin_id,
                "destination_id": destination_id,
                "total_time_min": record["total_time"],
                "stations": record["stations"],
                "interchange_points": record["interchange_points"],
            }


def query_delay_ripple(delayed_station_id: str, hops: int = 2) -> list[dict]:
    cypher = """
    MATCH (start:Station {station_id: $delayed_station_id})
    MATCH p = (start)-[:CONNECTS_TO|INTERCHANGES_WITH*1..$hops]-(affected:Station)
    WITH affected, min(length(p)) AS hops_away
    RETURN DISTINCT
        affected.station_id AS station_id,
        affected.name AS name,
        affected.network AS network,
        affected.lines AS lines_affected,
        hops_away
    ORDER BY hops_away ASC, station_id ASC
    """

    with _driver() as driver:
        with driver.session() as session:
            records = session.run(
                cypher,
                delayed_station_id=delayed_station_id,
                hops=int(hops),
            )

            return [
                {
                    "station_id": record["station_id"],
                    "name": record["name"],
                    "network": record["network"],
                    "hops_away": record["hops_away"],
                    "lines_affected": record["lines_affected"],
                }
                for record in records
            ]


def query_station_connections(station_id: str) -> list[dict]:
    cypher = """
    MATCH (s:Station {station_id: $station_id})-[r:CONNECTS_TO|INTERCHANGES_WITH]->(target:Station)
    RETURN
        target.station_id AS station_id,
        target.name AS name,
        target.network AS network,
        target.lines AS lines,
        type(r) AS relationship_type,
        r.line AS line,
        r.network AS connection_network,
        coalesce(r.travel_time_min, 1) AS travel_time_min,
        coalesce(r.fare, 0.0) AS fare
    ORDER BY travel_time_min ASC, station_id ASC
    """

    with _driver() as driver:
        with driver.session() as session:
            records = session.run(cypher, station_id=station_id)

            return [
                {
                    "station_id": record["station_id"],
                    "name": record["name"],
                    "network": record["network"],
                    "lines": record["lines"],
                    "relationship_type": record["relationship_type"],
                    "line": record["line"],
                    "connection_network": record["connection_network"],
                    "travel_time_min": record["travel_time_min"],
                    "fare": record["fare"],
                }
                for record in records
            ]