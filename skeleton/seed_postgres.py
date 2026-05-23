"""
Seed PostgreSQL with TransitFlow mock data from train-mock-data/.

Usage:
    python skeleton/seed_postgres.py

Run AFTER:
    docker compose up -d

This file assumes your databases/relational/schema.sql contains:
    - metro_schedules
    - national_rail_schedules
    - users
    - seat_layouts
    - bookings
    - metro_trips
    - payments
    - feedback
    - policy_documents

Safe to re-run:
    Uses ON CONFLICT DO NOTHING.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

import psycopg2
from psycopg2.extras import execute_values


# ── resolve paths ────────────────────────────────────────────────────────────

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(PROJECT_DIR, "train-mock-data")

sys.path.insert(0, PROJECT_DIR)

from skeleton import config as cfg  # noqa: E402


def load(filename: str) -> Any:
    path = os.path.join(DATA_DIR, filename)

    if not os.path.exists(path):
        print(f"  skip {filename}: file not found")
        return []

    with open(path, encoding="utf-8") as f:
        return json.load(f)


def connect():
    return psycopg2.connect(
        host=cfg.PG_HOST,
        port=cfg.PG_PORT,
        dbname=cfg.PG_DB,
        user=cfg.PG_USER,
        password=cfg.PG_PASSWORD,
    )


def table_exists(cur, table_name: str) -> bool:
    cur.execute("SELECT to_regclass(%s);", (table_name,))
    row = cur.fetchone()
    return bool(row and row[0])


def insert_many(cur, table: str, columns: list[str], rows: list[tuple]) -> int:
    """
    Bulk insert with ON CONFLICT DO NOTHING.
    Returns inserted row count as reported by cursor.
    """
    if not rows:
        return 0

    sql = (
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES %s "
        f"ON CONFLICT DO NOTHING"
    )

    execute_values(cur, sql, rows)
    return cur.rowcount


def json_dumps(value: Any) -> str:
    return json.dumps(value if value is not None else [], ensure_ascii=False)


def get_any(item: dict, keys: list[str], default=None):
    for key in keys:
        if key in item and item[key] is not None:
            return item[key]
    return default


def split_full_name(full_name: str) -> tuple[str, str]:
    parts = (full_name or "").strip().split()

    if not parts:
        return "", ""

    if len(parts) == 1:
        return parts[0], ""

    return parts[0], " ".join(parts[1:])


# ── seeders ──────────────────────────────────────────────────────────────────

def seed_metro_stations(cur):
    """
    Metro stations are stored in Neo4j, not PostgreSQL.
    This function remains as a no-op for dependency order.
    """
    data = load("metro_stations.json")
    print(f"  metro_stations: skipped relational insert ({len(data)} records used by Neo4j)")


def seed_national_rail_stations(cur):
    """
    National rail stations are stored in Neo4j, not PostgreSQL.
    This function remains as a no-op for dependency order.
    """
    data = load("national_rail_stations.json")
    print(f"  national_rail_stations: skipped relational insert ({len(data)} records used by Neo4j)")


def seed_metro_schedules(cur):
    if not table_exists(cur, "metro_schedules"):
        print("  metro_schedules: table not found, skipped")
        return

    data = load("metro_schedules.json")
    rows = []

    for item in data:
        rows.append(
            (
                item["schedule_id"],
                item["line"],
                item.get("direction"),
                item["origin_station_id"],
                item["destination_station_id"],
                json_dumps(item.get("stops_in_order", [])),
                item.get("first_train_time"),
                item.get("last_train_time"),
                json_dumps(item.get("travel_time_from_origin_min", {})),
                item.get("base_fare_usd", 2.00),
                item.get("per_stop_rate_usd", 0.50),
                item.get("frequency_min", 10),
                json_dumps(item.get("operates_on", [])),
            )
        )

    inserted = insert_many(
        cur,
        "metro_schedules",
        [
            "schedule_id",
            "line",
            "direction",
            "origin_station_id",
            "destination_station_id",
            "stops_in_order",
            "first_train_time",
            "last_train_time",
            "travel_time_from_origin_min",
            "base_fare_usd",
            "per_stop_rate_usd",
            "frequency_min",
            "operates_on",
        ],
        rows,
    )

    print(f"  metro_schedules: {inserted} rows inserted / {len(rows)} prepared")


def seed_national_rail_schedules(cur):
    if not table_exists(cur, "national_rail_schedules"):
        print("  national_rail_schedules: table not found, skipped")
        return

    data = load("national_rail_schedules.json")
    rows = []

    for item in data:
        rows.append(
            (
                item["schedule_id"],
                item["line"],
                item.get("service_type"),
                item.get("direction"),
                item["origin_station_id"],
                item["destination_station_id"],
                json_dumps(item.get("stops_in_order", [])),
                json_dumps(item.get("passed_through_stations", [])),
                item.get("first_train_time"),
                item.get("last_train_time"),
                json_dumps(item.get("travel_time_from_origin_min", {})),
                json_dumps(item.get("fare_classes", {})),
                item.get("frequency_min", 60),
                json_dumps(item.get("operates_on", [])),
            )
        )

    inserted = insert_many(
        cur,
        "national_rail_schedules",
        [
            "schedule_id",
            "line",
            "service_type",
            "direction",
            "origin_station_id",
            "destination_station_id",
            "stops_in_order",
            "passed_through_stations",
            "first_train_time",
            "last_train_time",
            "travel_time_from_origin_min",
            "fare_classes",
            "frequency_min",
            "operates_on",
        ],
        rows,
    )

    print(f"  national_rail_schedules: {inserted} rows inserted / {len(rows)} prepared")


def seed_seat_layouts(cur):
    if not table_exists(cur, "seat_layouts"):
        print("  seat_layouts: table not found, skipped")
        return

    data = load("national_rail_seat_layouts.json")
    rows = []

    for layout in data:
        schedule_id = layout.get("schedule_id")

        for coach in layout.get("coaches", []):
            carriage_no = coach.get("coach") or coach.get("carriage_no")
            fare_class = coach.get("fare_class", "standard")

            for seat in coach.get("seats", []):
                seat_id_raw = (
                    seat.get("seat_id")
                    or seat.get("seat_no")
                    or seat.get("id")
                )

                if not seat_id_raw:
                    continue

                # seat_id is PRIMARY KEY in schema.sql.
                # Raw seat IDs may repeat across schedules, so make it globally unique.
                unique_seat_id = f"{schedule_id}_{seat_id_raw}"

                column = str(seat.get("column", "")).upper()
                is_window = column in {"A", "F"}
                is_aisle = column in {"C", "D"}

                rows.append(
                    (
                        unique_seat_id,
                        schedule_id,
                        carriage_no,
                        seat_id_raw,
                        fare_class,
                        seat.get("seat_type", "reserved_seat"),
                        seat.get("is_window", is_window),
                        seat.get("is_aisle", is_aisle),
                    )
                )

    inserted = insert_many(
        cur,
        "seat_layouts",
        [
            "seat_id",
            "schedule_id",
            "carriage_no",
            "seat_no",
            "fare_class",
            "seat_type",
            "is_window",
            "is_aisle",
        ],
        rows,
    )

    print(f"  seat_layouts: {inserted} rows inserted / {len(rows)} prepared")


def seed_users(cur):
    if not table_exists(cur, "users"):
        print("  users: table not found, skipped")
        return

    data = load("registered_users.json")
    rows = []

    for item in data:
        full_name = (
            item.get("full_name")
            or item.get("name")
            or f"{item.get('first_name', '')} {item.get('surname', '')}".strip()
            or item["user_id"]
        )

        first_name = item.get("first_name")
        surname = item.get("surname")

        if not first_name and not surname:
            first_name, surname = split_full_name(full_name)

        rows.append(
            (
                item["user_id"],
                full_name,
                first_name,
                surname,
                item["email"],
                item.get("phone"),
                item.get("password", "password123"),
                item.get("date_of_birth"),
                item.get("secret_question", "What is your favourite station?"),
                item.get("secret_answer", "central"),
                item.get("is_active", True),
                item.get("registered_at") or item.get("created_at"),
            )
        )

    inserted = insert_many(
        cur,
        "users",
        [
            "user_id",
            "full_name",
            "first_name",
            "surname",
            "email",
            "phone",
            "password",
            "date_of_birth",
            "secret_question",
            "secret_answer",
            "is_active",
            "created_at",
        ],
        rows,
    )

    print(f"  users: {inserted} rows inserted / {len(rows)} prepared")


def seed_national_rail_bookings(cur):
    if not table_exists(cur, "bookings"):
        print("  bookings: table not found, skipped")
        return

    data = load("bookings.json")
    rows = []

    for item in data:
        schedule_id = item.get("schedule_id")
        seat_id_raw = item.get("seat_id")

        # Seat layouts use globally unique IDs: schedule_id + raw seat id
        unique_seat_id = (
            f"{schedule_id}_{seat_id_raw}"
            if schedule_id and seat_id_raw and not str(seat_id_raw).startswith(str(schedule_id))
            else seat_id_raw
        )

        rows.append(
            (
                item["booking_id"],
                item.get("user_id"),
                schedule_id,
                item.get("origin_station_id"),
                item.get("destination_station_id"),
                item.get("travel_date"),
                item.get("fare_class", "standard"),
                unique_seat_id,
                item.get("ticket_type", "single"),
                item.get("status", "active"),
                item.get("price_paid_usd") or item.get("amount_usd"),
                item.get("refund_amount_usd", 0.00),
                item.get("booked_at"),
                item.get("cancelled_at"),
            )
        )

    inserted = insert_many(
        cur,
        "bookings",
        [
            "booking_id",
            "user_id",
            "schedule_id",
            "origin_station_id",
            "destination_station_id",
            "travel_date",
            "fare_class",
            "seat_id",
            "ticket_type",
            "status",
            "price_paid_usd",
            "refund_amount_usd",
            "booked_at",
            "cancelled_at",
        ],
        rows,
    )

    print(f"  bookings: {inserted} rows inserted / {len(rows)} prepared")


def seed_metro_travels(cur):
    if not table_exists(cur, "metro_trips"):
        print("  metro_trips: table not found, skipped")
        return

    data = load("metro_travel_history.json")
    rows = []

    for index, item in enumerate(data, start=1):
        trip_id = get_any(
            item,
            ["trip_id", "travel_id", "metro_trip_id", "history_id"],
            f"MT{index:03d}",
        )

        rows.append(
            (
                trip_id,
                item.get("user_id"),
                item.get("schedule_id"),
                get_any(item, ["origin_station_id", "origin_id", "from_station_id"]),
                get_any(item, ["destination_station_id", "destination_id", "to_station_id"]),
                get_any(item, ["travel_date", "date"]),
                get_any(item, ["fare_paid_usd", "amount_usd", "fare_usd", "price_usd"], 0.0),
                get_any(item, ["created_at", "travelled_at", "timestamp"]),
            )
        )

    inserted = insert_many(
        cur,
        "metro_trips",
        [
            "trip_id",
            "user_id",
            "schedule_id",
            "origin_station_id",
            "destination_station_id",
            "travel_date",
            "fare_paid_usd",
            "created_at",
        ],
        rows,
    )

    print(f"  metro_trips: {inserted} rows inserted / {len(rows)} prepared")


def seed_payments(cur):
    if not table_exists(cur, "payments"):
        print("  payments: table not found, skipped")
        return

    data = load("payments.json")
    rows = []

    for index, item in enumerate(data, start=1):
        payment_id = get_any(
            item,
            ["payment_id", "id"],
            f"PM{index:03d}",
        )

        rows.append(
            (
                payment_id,
                item.get("booking_id"),
                item.get("user_id"),
                get_any(item, ["amount_usd", "amount", "price_paid_usd"], 0.0),
                get_any(item, ["payment_method", "method"], "card"),
                get_any(item, ["payment_status", "status"], "paid"),
                get_any(item, ["paid_at", "payment_time"]),
                get_any(item, ["created_at", "timestamp"]),
            )
        )

    inserted = insert_many(
        cur,
        "payments",
        [
            "payment_id",
            "booking_id",
            "user_id",
            "amount_usd",
            "payment_method",
            "payment_status",
            "paid_at",
            "created_at",
        ],
        rows,
    )

    print(f"  payments: {inserted} rows inserted / {len(rows)} prepared")


def seed_feedback(cur):
    if not table_exists(cur, "feedback"):
        print("  feedback: table not found, skipped")
        return

    data = load("feedback.json")
    rows = []

    for index, item in enumerate(data, start=1):
        feedback_id = get_any(
            item,
            ["feedback_id", "id"],
            f"FB{index:03d}",
        )

        rows.append(
            (
                feedback_id,
                item.get("user_id"),
                item.get("booking_id"),
                get_any(item, ["rating", "score"], None),
                get_any(item, ["comment", "feedback", "message"], None),
                get_any(item, ["created_at", "submitted_at", "timestamp"], None),
            )
        )

    inserted = insert_many(
        cur,
        "feedback",
        [
            "feedback_id",
            "user_id",
            "booking_id",
            "rating",
            "comment",
            "created_at",
        ],
        rows,
    )

    print(f"  feedback: {inserted} rows inserted / {len(rows)} prepared")


def print_table_counts(cur):
    tables = [
        "users",
        "metro_schedules",
        "national_rail_schedules",
        "seat_layouts",
        "bookings",
        "metro_trips",
        "payments",
        "feedback",
        "policy_documents",
    ]

    print("\nCurrent table counts:")

    for table in tables:
        if table_exists(cur, table):
            cur.execute(f"SELECT COUNT(*) FROM {table};")
            count = cur.fetchone()[0]
            print(f"  {table}: {count}")


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    print("Connecting to PostgreSQL...")
    conn = connect()
    conn.autocommit = False
    cur = conn.cursor()

    try:
        print("Seeding tables (dependency order):")

        seed_metro_stations(cur)
        seed_national_rail_stations(cur)
        seed_metro_schedules(cur)
        seed_national_rail_schedules(cur)
        seed_seat_layouts(cur)
        seed_users(cur)
        seed_national_rail_bookings(cur)
        seed_metro_travels(cur)
        seed_payments(cur)
        seed_feedback(cur)

        conn.commit()

        print_table_counts(cur)

        print("\nAll done. Database seeded successfully.")

    except Exception as e:
        conn.rollback()
        print(f"\nError: {e}")
        raise

    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()