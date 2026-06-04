"""
TransitFlow — PostgreSQL / Relational Database Layer (Optimized)
=====================================================
This module handles all queries to PostgreSQL with Connection Pooling.

Roles:
1. Relational database:
   - metro schedules
   - national rail schedules
   - fares
   - seats
   - users
   - bookings
   - cancellations

2. Vector database:
   - policy document similarity search by pgvector
"""

from __future__ import annotations

import json
import random
import string
from datetime import datetime, timezone
from typing import Optional
from contextlib import contextmanager

import psycopg2
import psycopg2.extras
from psycopg2 import pool

from skeleton.config import PG_DSN, VECTOR_TOP_K, VECTOR_SIMILARITY_THRESHOLD


# ─────────────────────────────────────────────────────────────────────────────
# Optimization 1: Global Connection Pool and safe borrow/return mechanism
# ─────────────────────────────────────────────────────────────────────────────

_PG_POOL = None

def _get_pool():
    """Lazily initialize the connection pool so it is created only when needed,
    avoiding connection spikes during application startup.
    """
    global _PG_POOL
    if _PG_POOL is None:
        # 初始化連線池 (最小連線數 1，最大連線數 20)
        _PG_POOL = psycopg2.pool.SimpleConnectionPool(1, 20, PG_DSN)
    return _PG_POOL

def close_pool():
    """Provide an interface for the application to close (e.g. on FastAPI shutdown)
    and release the connection pool resources.
    """
    global _PG_POOL
    if _PG_POOL is not None:
        _PG_POOL.closeall()
        _PG_POOL = None

@contextmanager
def get_db_connection():
    """Optimization 2: Safe connection borrow/return mechanism (Context Manager).
    Replaces the previous `_connect()` function which created a new connection
    on every call.
    """
    pool_instance = _get_pool()
    conn = pool_instance.getconn()
    conn.autocommit = True
    try:
        yield conn
    finally:
        # Ensure the connection is returned to the pool whether the query
        # succeeds or an exception occurs.
        pool_instance.putconn(conn)


# ─────────────────────────────────────────────────────────────────────────────
# Basic helpers
# ─────────────────────────────────────────────────────────────────────────────

def _gen_booking_id() -> str:
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"BK-{suffix}"


def _gen_payment_id() -> str:
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"PM-{suffix}"


def _table_exists(cur, table_name: str) -> bool:
    cur.execute("SELECT to_regclass(%s);", (table_name,))
    row = cur.fetchone()
    if not row:
        return False

    if isinstance(row, dict):
        return bool(next(iter(row.values())))

    return bool(row[0])


def _column_exists(cur, table_name: str, column_name: str) -> bool:
    cur.execute(
        """
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = %s
          AND column_name = %s
        """,
        (table_name, column_name),
    )
    return cur.fetchone() is not None


def _safe_json(value, default=None):
    if default is None:
        default = []

    if value is None:
        return default

    if isinstance(value, (dict, list)):
        return value

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return default

        try:
            return json.loads(text)
        except Exception:
            pass

        if text.startswith("{") and text.endswith("}"):
            text = text[1:-1]
            return [x.strip().strip('"').strip("'") for x in text.split(",") if x.strip()]

        if "," in text:
            return [x.strip().strip('"').strip("'") for x in text.split(",") if x.strip()]

    return default


def _as_float(value, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _split_full_name(full_name: str) -> tuple[str, str]:
    parts = (full_name or "").strip().split()

    if not parts:
        return "", ""

    if len(parts) == 1:
        return parts[0], ""

    return parts[0], " ".join(parts[1:])


def _ensure_user_auth_columns(cur):
    """
    Make login/register/password functions tolerant of older schema.sql.
    These ALTER statements are safe to re-run.
    """
    if not _table_exists(cur, "users"):
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id VARCHAR(30) PRIMARY KEY,
                full_name VARCHAR(100) NOT NULL,
                email VARCHAR(150) UNIQUE NOT NULL,
                phone VARCHAR(50),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS password TEXT;")
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS first_name VARCHAR(100);")
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS surname VARCHAR(100);")
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS date_of_birth DATE;")
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS secret_question TEXT;")
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS secret_answer TEXT;")
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE;")


def example_query() -> dict:
    """Example: returns the name of the connected database."""
    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT current_database() AS db;")
            return dict(cur.fetchone())


# ─────────────────────────────────────────────────────────────────────────────
# National rail availability
# ─────────────────────────────────────────────────────────────────────────────

def query_national_rail_availability(
    origin_id: str,
    destination_id: str,
    travel_date: Optional[str] = None,
) -> list[dict]:
    """
    Return national rail schedules that serve both origin and destination stations
    in the correct order.
    """
    origin_id = origin_id.upper()
    destination_id = destination_id.upper()

    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if not _table_exists(cur, "national_rail_schedules"):
                return [{"error": "national_rail_schedules table does not exist."}]

            cur.execute("SELECT * FROM national_rail_schedules ORDER BY schedule_id;")
            schedules = [dict(row) for row in cur.fetchall()]

            results = []

            for sched in schedules:
                stops = _safe_json(sched.get("stops_in_order"), [])

                if not stops:
                    continue

                stops_upper = [str(s).upper() for s in stops]

                if origin_id not in stops_upper or destination_id not in stops_upper:
                    continue

                origin_index = stops_upper.index(origin_id)
                destination_index = stops_upper.index(destination_id)

                if origin_index >= destination_index:
                    continue

                stops_travelled = destination_index - origin_index
                journey_stops = stops[origin_index: destination_index + 1]

                total_booked = 0
                standard_booked = 0
                first_booked = 0

                if travel_date and _table_exists(cur, "bookings"):
                    cur.execute(
                        """
                        SELECT
                            COUNT(*) AS total_booked,
                            SUM(CASE WHEN LOWER(COALESCE(fare_class, 'standard')) = 'standard' THEN 1 ELSE 0 END) AS standard_booked,
                            SUM(CASE WHEN LOWER(COALESCE(fare_class, 'standard')) = 'first' THEN 1 ELSE 0 END) AS first_booked
                        FROM bookings
                        WHERE schedule_id = %s
                          AND travel_date = %s
                          AND LOWER(COALESCE(status, 'active')) NOT IN ('cancelled', 'canceled')
                        """,
                        (sched.get("schedule_id"), travel_date),
                    )
                    booking_row = cur.fetchone()

                    if booking_row:
                        total_booked = booking_row["total_booked"] or 0
                        standard_booked = booking_row["standard_booked"] or 0
                        first_booked = booking_row["first_booked"] or 0

                travel_times = _safe_json(sched.get("travel_time_from_origin_min"), {})
                origin_time = _as_float(travel_times.get(origin_id), 0.0) if isinstance(travel_times, dict) else 0.0
                dest_time = _as_float(travel_times.get(destination_id), 0.0) if isinstance(travel_times, dict) else 0.0
                duration = max(dest_time - origin_time, 0.0)

                results.append(
                    {
                        "schedule_id": sched.get("schedule_id"),
                        "line": sched.get("line"),
                        "service_type": sched.get("service_type", "national_rail"),
                        "origin_id": origin_id,
                        "destination_id": destination_id,
                        "first_train_time": str(sched.get("first_train_time")) if sched.get("first_train_time") else None,
                        "last_train_time": str(sched.get("last_train_time")) if sched.get("last_train_time") else None,
                        "frequency_min": sched.get("frequency_min"),
                        "stops_travelled": stops_travelled,
                        "stops_in_order": journey_stops,
                        "full_route": stops,
                        "estimated_duration_min": duration,
                        "travel_date": travel_date,
                        "seat_occupancy": {
                            "total_booked": total_booked,
                            "standard_booked": standard_booked,
                            "first_booked": first_booked,
                        },
                    }
                )

            return results


def query_national_rail_fare(
    schedule_id: str,
    fare_class: str = "standard",
    stops_travelled: int = 1,
) -> dict:
    """
    Calculate national rail fare from schedule fare_classes JSON.
    """
    fare_class = (fare_class or "standard").lower()
    stops_travelled = int(stops_travelled or 1)

    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if not _table_exists(cur, "national_rail_schedules"):
                return {"error": "national_rail_schedules table does not exist."}

            cur.execute(
                """
                SELECT *
                FROM national_rail_schedules
                WHERE schedule_id = %s
                """,
                (schedule_id,),
            )
            row = cur.fetchone()

            if not row:
                return {"error": f"Schedule {schedule_id} not found."}

            sched = dict(row)
            fare_classes = _safe_json(sched.get("fare_classes"), {})

            if not isinstance(fare_classes, dict):
                fare_classes = {}

            class_info = (
                fare_classes.get(fare_class)
                or fare_classes.get(fare_class.capitalize())
                or fare_classes.get("standard")
                or {}
            )

            if not isinstance(class_info, dict):
                class_info = {}

            base = _as_float(class_info.get("base_fare_usd"), 5.0)
            per_stop = _as_float(class_info.get("per_stop_rate_usd"), 2.0)
            total = round(base + max(stops_travelled, 0) * per_stop, 2)

            return {
                "schedule_id": schedule_id,
                "fare_class": fare_class,
                "stops_travelled": stops_travelled,
                "base_fare_usd": base,
                "per_stop_rate_usd": per_stop,
                "total_fare_usd": total,
                "currency": "USD",
            }


# ─────────────────────────────────────────────────────────────────────────────
# Metro schedules and fare
# ─────────────────────────────────────────────────────────────────────────────

def query_metro_schedules(origin_id: str, destination_id: str) -> list[dict]:
    """
    Return metro schedules that serve origin and destination in the correct order.
    """
    origin_id = origin_id.upper()
    destination_id = destination_id.upper()

    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if not _table_exists(cur, "metro_schedules"):
                return []

            cur.execute("SELECT * FROM metro_schedules ORDER BY schedule_id;")
            schedules = [dict(row) for row in cur.fetchall()]

            results = []

            for sched in schedules:
                stops = _safe_json(sched.get("stops_in_order"), [])

                if not stops:
                    continue

                stops_upper = [str(s).upper() for s in stops]

                if origin_id not in stops_upper or destination_id not in stops_upper:
                    continue

                origin_index = stops_upper.index(origin_id)
                destination_index = stops_upper.index(destination_id)

                if origin_index >= destination_index:
                    continue

                stops_travelled = destination_index - origin_index
                journey_stops = stops[origin_index: destination_index + 1]

                travel_times = _safe_json(sched.get("travel_time_from_origin_min"), {})
                origin_time = _as_float(travel_times.get(origin_id), 0.0) if isinstance(travel_times, dict) else 0.0
                dest_time = _as_float(travel_times.get(destination_id), 0.0) if isinstance(travel_times, dict) else 0.0
                duration = max(dest_time - origin_time, 0.0)

                item = dict(sched)
                item.update(
                    {
                        "origin_id": origin_id,
                        "destination_id": destination_id,
                        "stops_travelled": stops_travelled,
                        "journey_stops": journey_stops,
                        "estimated_duration_min": duration,
                    }
                )
                results.append(item)

            return results


def query_metro_fare(schedule_id: str, stops_travelled: int = 1) -> dict:
    """
    Calculate metro fare from base_fare_usd and per_stop_rate_usd.
    """
    stops_travelled = int(stops_travelled or 1)

    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if not _table_exists(cur, "metro_schedules"):
                return {"error": "metro_schedules table does not exist."}

            cur.execute(
                """
                SELECT *
                FROM metro_schedules
                WHERE schedule_id = %s
                """,
                (schedule_id,),
            )
            row = cur.fetchone()

            if not row:
                return {"error": f"Metro schedule {schedule_id} not found."}

            sched = dict(row)
            base = _as_float(sched.get("base_fare_usd"), 2.0)
            per_stop = _as_float(sched.get("per_stop_rate_usd"), 0.5)
            total = round(base + max(stops_travelled, 0) * per_stop, 2)

            return {
                "schedule_id": schedule_id,
                "stops_travelled": stops_travelled,
                "base_fare_usd": base,
                "per_stop_rate_usd": per_stop,
                "total_fare_usd": total,
                "currency": "USD",
            }


# ─────────────────────────────────────────────────────────────────────────────
# Seats
# ─────────────────────────────────────────────────────────────────────────────

def query_available_seats(
    schedule_id: str,
    travel_date: str,
    fare_class: str = "standard",
) -> list[dict]:
    """
    Return available seats for a schedule/date/class.
    """
    fare_class = (fare_class or "standard").lower()

    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if not _table_exists(cur, "seat_layouts"):
                return [{"error": "seat_layouts table does not exist."}]

            if not _table_exists(cur, "bookings"):
                cur.execute(
                    """
                    SELECT *
                    FROM seat_layouts
                    WHERE schedule_id = %s
                      AND LOWER(COALESCE(fare_class, 'standard')) = %s
                    ORDER BY carriage_no, seat_no
                    """,
                    (schedule_id, fare_class),
                )
                return [dict(row) for row in cur.fetchall()]

            cur.execute(
                """
                SELECT sl.*
                FROM seat_layouts sl
                WHERE sl.schedule_id = %s
                  AND LOWER(COALESCE(sl.fare_class, 'standard')) = %s
                  AND NOT EXISTS (
                      SELECT 1
                      FROM bookings b
                      WHERE b.schedule_id = sl.schedule_id
                        AND b.travel_date = %s
                        AND b.seat_id = sl.seat_id
                        AND LOWER(COALESCE(b.status, 'active')) NOT IN ('cancelled', 'canceled')
                  )
                ORDER BY sl.carriage_no, sl.seat_no
                """,
                (schedule_id, fare_class, travel_date),
            )

            return [dict(row) for row in cur.fetchall()]


# ─────────────────────────────────────────────────────────────────────────────
# Users / bookings / payments
# ─────────────────────────────────────────────────────────────────────────────

def query_user_profile(email: str) -> Optional[dict]:
    """
    Return user profile by email.
    """
    if not email:
        return None

    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if not _table_exists(cur, "users"):
                return None

            _ensure_user_auth_columns(cur)

            cur.execute(
                """
                SELECT *
                FROM users
                WHERE LOWER(email) = LOWER(%s)
                """,
                (email.strip(),),
            )
            row = cur.fetchone()

            if not row:
                return None

            data = dict(row)

            first_name = data.get("first_name")
            surname = data.get("surname")

            if not first_name and not surname:
                first_name, surname = _split_full_name(data.get("full_name", ""))

            data["first_name"] = first_name
            data["surname"] = surname
            data["is_active"] = data.get("is_active", True)

            return data


def query_user_bookings(user_email: str) -> list[dict]:
    """
    Return booking history for a user email.
    """
    profile = query_user_profile(user_email)

    if not profile:
        return [{"error": "User profile not found."}]

    user_id = profile["user_id"]

    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if not _table_exists(cur, "bookings"):
                return []

            cur.execute(
                """
                SELECT
                    b.*,
                    s.line,
                    s.service_type
                FROM bookings b
                LEFT JOIN national_rail_schedules s
                    ON s.schedule_id = b.schedule_id
                WHERE b.user_id = %s
                ORDER BY b.travel_date DESC NULLS LAST, b.booked_at DESC NULLS LAST
                """,
                (user_id,),
            )

            return [dict(row) for row in cur.fetchall()]


def query_payment_info(booking_id: str) -> Optional[dict]:
    """
    Return payment information if a payments table exists.
    Otherwise return minimal payment-like info from bookings.
    """
    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if _table_exists(cur, "payments"):
                cur.execute(
                    """
                    SELECT *
                    FROM payments
                    WHERE booking_id = %s
                    ORDER BY created_at DESC NULLS LAST
                    LIMIT 1
                    """,
                    (booking_id,),
                )
                row = cur.fetchone()
                return dict(row) if row else None

            if _table_exists(cur, "bookings"):
                cur.execute(
                    """
                    SELECT
                        booking_id,
                        price_paid_usd AS amount_usd,
                        refund_amount_usd,
                        status,
                        booked_at
                    FROM bookings
                    WHERE booking_id = %s
                    """,
                    (booking_id,),
                )
                row = cur.fetchone()
                return dict(row) if row else None

    return None


# ─────────────────────────────────────────────────────────────────────────────
# Booking / cancellation
# ─────────────────────────────────────────────────────────────────────────────

def execute_booking(
    user_id: str,
    schedule_id: str,
    origin_station_id: str,
    destination_station_id: str,
    travel_date: str,
    fare_class: str,
    seat_id: str,
    ticket_type: str = "single",
) -> tuple[bool, dict | str]:
    """
    Create a national rail booking.
    """
    fare_class = (fare_class or "standard").lower()
    ticket_type = ticket_type or "single"

    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                if not _table_exists(cur, "bookings"):
                    return False, "bookings table does not exist."

                cur.execute(
                    """
                    SELECT *
                    FROM national_rail_schedules
                    WHERE schedule_id = %s
                    """,
                    (schedule_id,),
                )
                schedule = cur.fetchone()

                if not schedule:
                    return False, f"Schedule {schedule_id} not found."

                schedule = dict(schedule)
                stops = _safe_json(schedule.get("stops_in_order"), [])

                stops_upper = [str(s).upper() for s in stops]
                origin = origin_station_id.upper()
                destination = destination_station_id.upper()

                if origin not in stops_upper or destination not in stops_upper:
                    return False, "Origin or destination is not served by this schedule."

                origin_index = stops_upper.index(origin)
                dest_index = stops_upper.index(destination)

                if origin_index >= dest_index:
                    return False, "Destination must come after origin on this schedule."

                stops_travelled = dest_index - origin_index
                fare = query_national_rail_fare(schedule_id, fare_class, stops_travelled)

                if "error" in fare:
                    return False, fare["error"]

                price = fare["total_fare_usd"]

                # Accept either globally unique seat_id or raw seat_no.
                final_seat_id = seat_id

                if _table_exists(cur, "seat_layouts"):
                    cur.execute(
                        """
                        SELECT seat_id
                        FROM seat_layouts
                        WHERE schedule_id = %s
                          AND (
                              seat_id = %s
                              OR seat_no = %s
                              OR seat_id = %s
                          )
                          AND LOWER(COALESCE(fare_class, 'standard')) = %s
                        LIMIT 1
                        """,
                        (
                            schedule_id,
                            seat_id,
                            seat_id,
                            f"{schedule_id}_{seat_id}",
                            fare_class,
                        ),
                    )
                    seat_row = cur.fetchone()

                    if not seat_row:
                        return False, "Seat not found for this schedule and fare class."

                    final_seat_id = seat_row["seat_id"]

                cur.execute(
                    """
                    SELECT 1
                    FROM bookings
                    WHERE schedule_id = %s
                      AND travel_date = %s
                      AND seat_id = %s
                      AND LOWER(COALESCE(status, 'active')) NOT IN ('cancelled', 'canceled')
                    """,
                    (schedule_id, travel_date, final_seat_id),
                )

                if cur.fetchone():
                    return False, "Seat is already booked for this date."

                booking_id = _gen_booking_id()

                cur.execute(
                    """
                    INSERT INTO bookings (
                        booking_id,
                        user_id,
                        schedule_id,
                        origin_station_id,
                        destination_station_id,
                        travel_date,
                        fare_class,
                        seat_id,
                        ticket_type,
                        status,
                        price_paid_usd,
                        refund_amount_usd,
                        booked_at
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        'active', %s, 0.00, CURRENT_TIMESTAMP
                    )
                    RETURNING *
                    """,
                    (
                        booking_id,
                        user_id,
                        schedule_id,
                        origin,
                        destination,
                        travel_date,
                        fare_class,
                        final_seat_id,
                        ticket_type,
                        price,
                    ),
                )

                row = dict(cur.fetchone())
                row["fare"] = fare

                return True, row

    except Exception as e:
        return False, str(e)


def execute_cancellation(booking_id: str, user_id: str) -> tuple[bool, dict | str]:
    """
    Cancel a national rail booking owned by the given user.
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                if not _table_exists(cur, "bookings"):
                    return False, "bookings table does not exist."

                cur.execute(
                    """
                    SELECT b.*, s.service_type
                    FROM bookings b
                    LEFT JOIN national_rail_schedules s
                        ON s.schedule_id = b.schedule_id
                    WHERE b.booking_id = %s
                      AND b.user_id = %s
                    """,
                    (booking_id, user_id),
                )
                booking = cur.fetchone()

                if not booking:
                    return False, "Booking not found or does not belong to this user."

                booking = dict(booking)

                if str(booking.get("status", "")).lower() in {"cancelled", "canceled"}:
                    return False, "Booking is already cancelled."

                price = _as_float(booking.get("price_paid_usd"), 0.0)
                service_type = str(booking.get("service_type") or "normal").lower()

                travel_date = booking.get("travel_date")
                today = datetime.now(timezone.utc).date()

                refund_rate = 0.0
                note = "No refund after travel date."

                if travel_date and travel_date > today:
                    if service_type == "express":
                        refund_rate = 0.50
                        note = "Express service cancellation before travel date: 50% refund."
                    else:
                        refund_rate = 0.75
                        note = "Normal service cancellation before travel date: 75% refund."

                refund = round(price * refund_rate, 2)

                cur.execute(
                    """
                    UPDATE bookings
                    SET status = 'cancelled',
                        refund_amount_usd = %s,
                        cancelled_at = CURRENT_TIMESTAMP
                    WHERE booking_id = %s
                    RETURNING *
                    """,
                    (refund, booking_id),
                )

                updated = dict(cur.fetchone())

                return True, {
                    "booking": updated,
                    "refund_amount_usd": refund,
                    "refund_rate": refund_rate,
                    "policy_note": note,
                }

    except Exception as e:
        return False, str(e)


# ─────────────────────────────────────────────────────────────────────────────
# Authentication
# ─────────────────────────────────────────────────────────────────────────────

def register_user(
    email: str,
    first_name: str,
    surname: str,
    year_of_birth: int,
    password: str,
    secret_question: str,
    secret_answer: str,
) -> tuple[bool, str]:
    """
    Register a new user.
    Returns (True, user_id) on success or (False, error_message) on failure.
    """
    email = (email or "").strip().lower()

    if not email or not password:
        return False, "Email and password are required."

    full_name = f"{(first_name or '').strip()} {(surname or '').strip()}".strip()
    if not full_name:
        full_name = email

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                _ensure_user_auth_columns(cur)

                cur.execute(
                    "SELECT 1 FROM users WHERE LOWER(email) = LOWER(%s)",
                    (email,),
                )

                if cur.fetchone():
                    return False, "An account with this email already exists."

                cur.execute(
                    """
                    SELECT COALESCE(
                        MAX(CAST(SUBSTRING(user_id FROM 3) AS INTEGER)),
                        0
                    ) + 1
                    FROM users
                    WHERE user_id ~ '^RU[0-9]+$'
                    """
                )
                next_num = cur.fetchone()[0]
                user_id = f"RU{int(next_num):02d}"

                date_of_birth = f"{int(year_of_birth):04d}-01-01"

                cur.execute(
                    """
                    INSERT INTO users (
                        user_id,
                        full_name,
                        email,
                        phone,
                        password,
                        first_name,
                        surname,
                        date_of_birth,
                        secret_question,
                        secret_answer,
                        is_active,
                        created_at
                    )
                    VALUES (
                        %s, %s, %s, NULL, %s, %s, %s, %s, %s, %s,
                        TRUE, CURRENT_TIMESTAMP
                    )
                    """,
                    (
                        user_id,
                        full_name,
                        email,
                        password,
                        first_name,
                        surname,
                        date_of_birth,
                        secret_question,
                        secret_answer,
                    ),
                )

                return True, user_id

    except Exception as e:
        return False, str(e)


def login_user(email: str, password: str) -> Optional[dict]:
    """
    Verify credentials.
    Returns a user dict on success, otherwise None.
    """
    if not email or not password:
        return None

    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            _ensure_user_auth_columns(cur)

            cur.execute(
                """
                SELECT *
                FROM users
                WHERE LOWER(email) = LOWER(%s)
                  AND COALESCE(password, '') = %s
                  AND COALESCE(is_active, TRUE) = TRUE
                """,
                (email.strip(), password),
            )

            row = cur.fetchone()

    if not row:
        return None

    data = dict(row)

    first_name = data.get("first_name")
    surname = data.get("surname")

    if not first_name and not surname:
        first_name, surname = _split_full_name(data.get("full_name", ""))

    data["first_name"] = first_name
    data["surname"] = surname
    data["is_active"] = data.get("is_active", True)

    return data


def get_user_secret_question(email: str) -> Optional[str]:
    """
    Return the secret question for a registered email.
    """
    if not email:
        return None

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            _ensure_user_auth_columns(cur)

            cur.execute(
                """
                SELECT secret_question
                FROM users
                WHERE LOWER(email) = LOWER(%s)
                """,
                (email.strip(),),
            )

            row = cur.fetchone()

    return row[0] if row and row[0] else None


def verify_secret_answer(email: str, answer: str) -> bool:
    """
    Return True if the answer matches the stored secret answer.
    """
    if not email or not answer:
        return False

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            _ensure_user_auth_columns(cur)

            cur.execute(
                """
                SELECT secret_answer
                FROM users
                WHERE LOWER(email) = LOWER(%s)
                """,
                (email.strip(),),
            )

            row = cur.fetchone()

    if not row or row[0] is None:
        return False

    return str(row[0]).strip().lower() == str(answer).strip().lower()


def update_password(email: str, new_password: str) -> bool:
    """
    Update password for a user.
    """
    if not email or not new_password:
        return False

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            _ensure_user_auth_columns(cur)

            cur.execute(
                """
                UPDATE users
                SET password = %s
                WHERE LOWER(email) = LOWER(%s)
                """,
                (new_password, email.strip()),
            )

            return cur.rowcount > 0


# ─────────────────────────────────────────────────────────────────────────────
# Vector / RAG
# ─────────────────────────────────────────────────────────────────────────────

def query_policy_vector_search(
    embedding: list[float],
    top_k: int = VECTOR_TOP_K,
) -> list[dict]:
    """
    Find the most relevant policy documents for a given query embedding.
    """
    sql = """
        SELECT
            title,
            category,
            content,
            1 - (embedding <=> %s::vector) AS similarity
        FROM policy_documents
        WHERE 1 - (embedding <=> %s::vector) > %s
        ORDER BY embedding <=> %s::vector
        LIMIT %s
    """

    vec_str = "[" + ",".join(str(x) for x in embedding) + "]"

    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                sql,
                (
                    vec_str,
                    vec_str,
                    VECTOR_SIMILARITY_THRESHOLD,
                    vec_str,
                    top_k,
                ),
            )

            return [dict(row) for row in cur.fetchall()]


def store_policy_document(
    title: str,
    category: str,
    content: str,
    embedding: list[float],
    source_file: str = "",
) -> int:
    """
    Insert a policy document with its embedding into the database.
    Used by skeleton/seed_vectors.py.
    """
    vec_str = "[" + ",".join(str(x) for x in embedding) + "]"

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            if not _column_exists(cur, "policy_documents", "source_file"):
                cur.execute(
                    """
                    ALTER TABLE policy_documents
                    ADD COLUMN IF NOT EXISTS source_file TEXT
                    """
                )

            sql = """
                INSERT INTO policy_documents (
                    title,
                    category,
                    content,
                    embedding,
                    source_file
                )
                VALUES (%s, %s, %s, %s::vector, %s)
                RETURNING id
            """

            cur.execute(sql, (title, category, content, vec_str, source_file))
            return cur.fetchone()[0]