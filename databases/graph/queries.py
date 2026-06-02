"""
TransitFlow — Neo4j Graph Database Layer (Optimized)
"""

from __future__ import annotations

from neo4j import GraphDatabase
from skeleton.config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD

# =========================================================================
# 優化 1：將 Driver 改為全域單例 (Singleton)
# Neo4j Driver 物件很重，且內部已自帶連線池。全域維持一個實例即可。
# =========================================================================
_driver_instance = None

def _get_driver():
    global _driver_instance
    if _driver_instance is None:
        _driver_instance = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    return _driver_instance

def close_driver():
    """提供給應用程式關閉（如 FastAPI shutdown event）時釋放連線池資源的介面"""
    global _driver_instance
    if _driver_instance is not None:
        _driver_instance.close()
        _driver_instance = None


def example_count_nodes() -> int:
    driver = _get_driver()
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
    // 【步驟一：定位起點與終點】
    MATCH (start:Station {{station_id: $origin_id}})
    MATCH (end:Station {{station_id: $destination_id}})
    
    // 【步驟二：呼叫核心演算法】
    CALL apoc.algo.dijkstra(
        start,                             
        end,                               
        'CONNECTS_TO>|INTERCHANGES_WITH>', 
        'travel_time_min'                  
    ) YIELD path, weight                   
    
    // 【步驟三：套用外部篩選條件】
    WHERE {network_condition}
    
    // 【步驟四：整理並回傳最終結果】
    RETURN 
        weight AS total_time,
        [n IN nodes(path) | {{
            station_id: n.station_id,      
            name: n.name,                  
            network: n.network,            
            lines: n.lines                 
        }}] AS stations,
        [r IN relationships(path) | {{
            from: startNode(r).station_id, 
            to: endNode(r).station_id,     
            line: r.line,                  
            network: r.network,            
            travel_time_min: coalesce(r.travel_time_min, 1) 
        }}] AS legs 
    """

    driver = _get_driver()
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


def query_cheapest_route(origin_id: str, destination_id: str, network: str = "auto") -> dict:
    network_condition = _network_filter(network)

    cypher = f"""
    // 【步驟一：定位起點與終點】
    MATCH (start:Station {{station_id: $origin_id}})
    MATCH (end:Station {{station_id: $destination_id}})
    
    // 【步驟二：呼叫核心演算法 (替換權重為票價)】
    CALL apoc.algo.dijkstra(
        start,                                
        end,                                  
        'CONNECTS_TO>|INTERCHANGES_WITH>', 
        'fare'                                
    ) YIELD path, weight                   
    
    // 【步驟三：套用外部網路篩選條件】
    WHERE {network_condition}
    
    // 【步驟四：整理並回傳最終結果】
    RETURN 
        weight AS total_fare,              
        [n IN nodes(path) | {{
            station_id: n.station_id, 
            name: n.name, 
            network: n.network, 
            lines: n.lines
        }}] AS stations,
        [r IN relationships(path) | {{
            from: startNode(r).station_id,
            to: endNode(r).station_id,
            line: r.line,
            network: r.network,
            fare: coalesce(r.fare, 0),
            travel_time_min: coalesce(r.travel_time_min, 1)
        }}] AS legs
    """

    driver = _get_driver()
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

    # =========================================================================
    # 優化 2：修正原程式碼中重複堆疊 4 次的 Copy-Paste Bug，精簡為單一執行區塊
    # =========================================================================
    driver = _get_driver()
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

    driver = _get_driver()
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

    driver = _get_driver()
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

    driver = _get_driver()
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