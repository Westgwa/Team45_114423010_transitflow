"""
TransitFlow — Live Testing Simulation
=====================================
Mirrors the grader's live-testing checklist (work.txt Sections A/B/C).
Run AFTER: docker compose up -d  +  seed_postgres.py  +  seed_neo4j.py

Usage:
    python scripts/live_test_simulation.py

Exit code 0 = all checks passed.
"""

from __future__ import annotations

import os
import sys
import traceback
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
import psycopg2.extras
from neo4j import GraphDatabase

from skeleton import config as cfg
from databases.relational import queries as rq
from databases.graph import queries as gq

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, fn):
    """Run one check; record PASS/FAIL instead of crashing the suite."""
    try:
        ok, detail = fn()
    except Exception as e:
        ok, detail = False, f"EXCEPTION: {e.__class__.__name__}: {e}"
        traceback.print_exc()
    RESULTS.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} — {detail}")


def pg_conn():
    return psycopg2.connect(
        host=cfg.PG_HOST, port=cfg.PG_PORT, dbname=cfg.PG_DB,
        user=cfg.PG_USER, password=cfg.PG_PASSWORD,
    )


# ── Section A: Seeding & Setup ────────────────────────────────────────────────

def section_a():
    print("\n=== Section A: Seeding & Setup ===")

    required_tables = [
        "metro_stations", "national_rail_stations", "metro_schedules",
        "national_rail_schedules", "metro_schedule_stops",
        "national_rail_schedule_stops", "users", "seat_layouts",
        "policy_documents",
    ]

    with pg_conn() as conn:
        with conn.cursor() as cur:
            for table in required_tables:
                def _count(t=table):
                    cur.execute(f"SELECT COUNT(*) FROM {t};")
                    n = cur.fetchone()[0]
                    return n > 0, f"{n} rows"
                check(f"A: {table} has rows", _count)

            def _fare_numeric():
                cur.execute("SELECT base_fare_usd FROM metro_schedules LIMIT 1;")
                v = cur.fetchone()[0]
                return isinstance(v, (Decimal, float, int)), f"type={type(v).__name__}"
            check("A: fare columns are numeric", _fare_numeric)

            def _password_hashed():
                cur.execute("SELECT password_hash FROM user_credentials LIMIT 1;")
                v = cur.fetchone()[0]
                return str(v).startswith("$argon2"), f"prefix={str(v)[:10]}"
            check("A: seeded passwords are argon2 hashes", _password_hashed)

    driver = GraphDatabase.driver(
        cfg.NEO4J_URI, auth=(cfg.NEO4J_USER, cfg.NEO4J_PASSWORD)
    )
    with driver.session() as session:
        for rel in ["METRO_LINK", "RAIL_LINK", "INTERCHANGE_TO"]:
            def _rel_count(r=rel):
                n = session.run(
                    f"MATCH ()-[x:{r}]->() RETURN count(x) AS n"
                ).single()["n"]
                return n > 0, f"{n} relationships"
            check(f"A: Neo4j {rel} exists", _rel_count)

        def _no_legacy():
            n = session.run(
                "MATCH ()-[x:CONNECTS_TO|INTERCHANGES_WITH]->() RETURN count(x) AS n"
            ).single()["n"]
            return n == 0, f"{n} legacy relationships"
        check("A: no legacy relationship names", _no_legacy)

        def _travel_time_numeric():
            v = session.run(
                "MATCH ()-[x:METRO_LINK]->() RETURN x.travel_time_min AS t LIMIT 1"
            ).single()["t"]
            return isinstance(v, int), f"type={type(v).__name__}"
        check("A: travel_time_min is numeric", _travel_time_numeric)
    driver.close()


# ── Section B: PostgreSQL queries ────────────────────────────────────────────

def _any_user_email() -> str:
    with pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT email FROM users ORDER BY user_id LIMIT 1;")
            return cur.fetchone()[0]


def _any_user_id() -> str:
    with pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id FROM users ORDER BY user_id LIMIT 1;")
            return cur.fetchone()[0]


def _booking_flow():
    user_id = _any_user_id()
    travel_date = "2026-07-15"

    seats = rq.query_available_seats("NR_SCH01", travel_date, "standard")
    if not seats or "error" in seats[0]:
        check("B9: booking flow", lambda: (False, "no free seats to test with"))
        return
    seat_no = seats[0].get("seat_no") or seats[0]["seat_id"]

    ok1, data1 = rq.execute_booking(
        user_id=user_id, schedule_id="NR_SCH01",
        origin_station_id="NR01", destination_station_id="NR05",
        travel_date=travel_date, fare_class="standard", seat_id=seat_no,
    )
    check("B9: booking succeeds", lambda: (ok1, str(data1)[:120]))

    if not ok1:
        return

    booking_id = data1["booking_id"]

    check("B9: payment row created in same transaction", lambda: (
        rq.query_payment_info(booking_id) is not None,
        str(rq.query_payment_info(booking_id))[:120],
    ))

    ok_dup, msg_dup = rq.execute_booking(
        user_id=user_id, schedule_id="NR_SCH01",
        origin_station_id="NR01", destination_station_id="NR05",
        travel_date=travel_date, fare_class="standard", seat_id=seat_no,
    )
    check("B9: duplicate booking rejected gracefully", lambda: (
        not ok_dup, str(msg_dup)[:120]))

    ok_c, data_c = rq.execute_cancellation(booking_id, user_id)
    check("B10: cancellation succeeds with refund", lambda: (
        ok_c and "refund_amount_usd" in data_c, str(data_c)[:120]))

    ok_c2, msg_c2 = rq.execute_cancellation(booking_id, user_id)
    check("B10: double-cancel rejected gracefully", lambda: (
        not ok_c2, str(msg_c2)[:120]))


def section_b():
    print("\n=== Section B: PostgreSQL Queries ===")

    def _b1():
        rows = rq.query_national_rail_availability("NR01", "NR05")
        return isinstance(rows, list) and len(rows) > 0, f"{len(rows)} schedules"
    check("B1: availability returns non-empty list", _b1)

    def _b1_seats():
        rows = rq.query_national_rail_availability("NR01", "NR05", "2026-08-01")
        ok = all(
            "schedule_id" in r
            and isinstance(r.get("available_seats"), int)
            for r in rows
        )
        return ok and len(rows) > 0, f"available_seats={[r.get('available_seats') for r in rows]}"
    check("B1: each result has schedule_id + numeric available_seats", _b1_seats)

    def _b1_empty():
        rows = rq.query_national_rail_availability("NR05", "NR01_NOPE")
        return rows == [], f"{rows!r}"
    check("B1: availability no-match returns []", _b1_empty)

    def _b2():
        rows = rq.query_metro_schedules("MS20", "MS17")
        return isinstance(rows, list) and len(rows) > 0, f"{len(rows)} schedules"
    check("B2: metro schedules found (same line, ordered)", _b2)

    def _b2_empty():
        rows = rq.query_metro_schedules("MS17", "MS17")
        return rows == [], f"{rows!r}"
    check("B2: metro no-route returns []", _b2_empty)

    def _b3():
        fare = rq.query_national_rail_fare("NR_SCH01", "standard", 4)
        keys = {"base_fare_usd", "per_stop_rate_usd", "total_fare_usd"}
        ok = keys.issubset(fare) and abs(fare["total_fare_usd"] - (2.50 + 4 * 1.50)) < 0.01
        return ok, str({k: fare.get(k) for k in keys})
    check("B3: national rail fare dict (3 keys, correct maths)", _b3)

    def _b4():
        fare = rq.query_metro_fare("MS_SCH01", 6)
        ok = abs(fare["total_fare_usd"] - (0.80 + 6 * 0.30)) < 0.01
        return ok, f"total={fare['total_fare_usd']}"
    check("B4: metro fare correct", _b4)

    def _b5():
        seats = rq.query_available_seats("NR_SCH01", "2026-07-01", "standard")
        ok = isinstance(seats, list) and len(seats) > 0 and all(
            s.get("fare_class", "standard") == "standard" for s in seats
        )
        return ok, f"{len(seats)} standard seats"
    check("B5: available seats filtered by fare class", _b5)

    def _b6():
        return rq.query_user_profile("nobody@example.com") is None, "None"
    check("B6: unknown email returns None", _b6)

    def _b6_known():
        profile = rq.query_user_profile(_any_user_email())
        ok = (
            isinstance(profile, dict)
            and "email" in profile
            and ("full_name" in profile or "name" in profile)
            and isinstance(profile.get("year_of_birth"), int)
        )
        return ok, f"year_of_birth={profile.get('year_of_birth') if profile else None}"
    check("B6: known email returns dict with email/name/year_of_birth", _b6_known)

    def _b7():
        result = rq.query_user_bookings(_any_user_email())
        ok = (
            isinstance(result, dict)
            and "national_rail" in result
            and "metro" in result
        )
        return ok, f"keys={sorted(result.keys()) if isinstance(result, dict) else type(result)}"
    check("B7: user bookings always has both keys", _b7)

    def _b7_unknown():
        result = rq.query_user_bookings("nobody@example.com")
        return result == {"national_rail": [], "metro": []}, str(result)
    check("B7: unknown user still has both keys", _b7_unknown)

    def _b8():
        return rq.query_payment_info("BK-NOPE99") is None, "None"
    check("B8: unknown booking payment returns None", _b8)

    _booking_flow()


# ── Section C: Neo4j routing ─────────────────────────────────────────────────

def section_c():
    print("\n=== Section C: Neo4j Routing Queries ===")

    def _c1():
        r = gq.query_shortest_route("MS20", "MS17")
        ok = r.get("found") and "total_time_min" in r and "path" in r
        return ok, f"time={r.get('total_time_min')}, stops={len(r.get('path', []))}"
    check("C1: shortest route has path + total_time_min", _c1)

    def _c2():
        std = gq.query_cheapest_route("NR01", "NR05", fare_class="standard")
        fst = gq.query_cheapest_route("NR01", "NR05", fare_class="first")
        ok = (
            std.get("found") and fst.get("found")
            and fst["total_fare_usd"] > std["total_fare_usd"]
        )
        return ok, f"standard={std.get('total_fare_usd')}, first={fst.get('total_fare_usd')}"
    check("C2: fare class changes route cost", _c2)

    def _c3():
        routes = gq.query_alternative_routes("NR01", "NR05", "NR03", max_routes=2)
        ok = (
            isinstance(routes, list) and 0 < len(routes) <= 2
            and all(
                "NR03" not in [s["station_id"] for s in rt["stations"]]
                for rt in routes
            )
        )
        return ok, f"{len(routes)} routes, all avoid NR03"
    check("C3: alternative routes avoid station + respect max_routes", _c3)

    def _c4():
        r = gq.query_interchange_path("MS20", "NR05")
        ok = r.get("found") and len(r.get("interchange_points", [])) > 0
        return ok, f"time={r.get('total_time_min')}, interchanges={len(r.get('interchange_points', []))}"
    check("C4: interchange path crosses INTERCHANGE_TO", _c4)

    def _c5():
        ripple = gq.query_delay_ripple("MS01", hops=2)
        ok = (
            isinstance(ripple, list) and len(ripple) > 0
            and all("hops_away" in s for s in ripple)
        )
        return ok, f"{len(ripple)} affected stations"
    check("C5: delay ripple includes hops_away", _c5)

    def _c5_zero():
        ripple = gq.query_delay_ripple("MS01", hops=0)
        ok = (
            isinstance(ripple, list) and len(ripple) == 1
            and ripple[0]["station_id"] == "MS01"
            and ripple[0]["hops_away"] == 0
        )
        return ok, f"{ripple!r}"[:120]
    check("C5: hops=0 returns only the delayed station itself", _c5_zero)

    def _c34_path_shape():
        routes = gq.query_alternative_routes("NR01", "NR05", "NR03", max_routes=2)
        inter = gq.query_interchange_path("MS20", "NR05")
        ok = (
            all(isinstance(rt.get("path"), list) for rt in routes)
            and isinstance(inter.get("path"), list)
        )
        return ok, "path key present on alternative routes + interchange path"
    check("C3/C4: routing results expose a path list", _c34_path_shape)

    def _c6():
        conns = gq.query_station_connections("MS01")
        ok = (
            isinstance(conns, list) and len(conns) > 0
            and all("travel_time_min" in c for c in conns)
        )
        return ok, f"{len(conns)} direct connections"
    check("C6: station connections include travel_time_min", _c6)


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    print("TransitFlow live-testing simulation")
    print(f"PostgreSQL: {cfg.PG_HOST}:{cfg.PG_PORT}/{cfg.PG_DB}")
    print(f"Neo4j: {cfg.NEO4J_URI}")

    section_a()
    section_b()
    section_c()

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{'=' * 60}")
    print(f"TOTAL: {len(RESULTS)} checks, {len(RESULTS) - len(failed)} passed, {len(failed)} failed")
    for name, _, detail in failed:
        print(f"  FAILED: {name} — {detail}")

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
