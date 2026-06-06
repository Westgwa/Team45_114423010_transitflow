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
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

from argon2 import PasswordHasher
from argon2 import exceptions as argon2_exceptions

import psycopg2
import psycopg2.extras
from psycopg2 import pool

from skeleton.config import PG_DSN, VECTOR_TOP_K, VECTOR_SIMILARITY_THRESHOLD

# TASK 6 EXTENSION: Added booking analytics query for bonus eligibility.

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
        # Initialize the connection pool (min 1 connection, max 20 connections).
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
    """Borrow a pooled connection as a context manager.

    One `with` block = one transaction:
    - commits on normal exit (so multi-statement writes such as
      booking + payment are persisted atomically),
    - rolls back and re-raises on any exception,
    - always returns the connection to the pool.
    """
    pool_instance = _get_pool()
    conn = pool_instance.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool_instance.putconn(conn)


def _gen_booking_id() -> str:
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"BK-{suffix}"


def _gen_payment_id() -> str:
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"PM-{suffix}"


# ─────────────────────────────────────────────────────────────────────────────
# Password hashing (Argon2id — same hasher settings as skeleton/seed_postgres.py
# so seeded credentials verify correctly at login)
# ─────────────────────────────────────────────────────────────────────────────

_ph = PasswordHasher()


def _hash_password(plain_password: str) -> str:
    """Hash a plaintext password with Argon2id (memory-hard KDF, random salt)."""
    return _ph.hash(plain_password)


def _verify_password(stored_hash: str, plain_password: str) -> tuple[bool, bool]:
    """Verify a password against a stored Argon2 hash.

    Returns (is_valid, needs_rehash). Never raises: malformed or
    non-matching hashes simply yield (False, False).
    """
    try:
        _ph.verify(stored_hash, plain_password)
    except (
        argon2_exceptions.VerifyMismatchError,
        argon2_exceptions.VerificationError,
        argon2_exceptions.InvalidHashError,
    ):
        return False, False
    return True, _ph.check_needs_rehash(stored_hash)


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
                created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS user_credentials (
            user_id VARCHAR(30) PRIMARY KEY,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT fk_user_credentials_user
                FOREIGN KEY (user_id)
                REFERENCES users(user_id)
                ON DELETE CASCADE
        )
        """
    )

    if _column_exists(cur, "users", "password"):
        cur.execute(
            """
            SELECT user_id, password
            FROM users
            WHERE password IS NOT NULL
              AND user_id NOT IN (SELECT user_id FROM user_credentials)
            """
        )
        for user_id, plain_password in cur.fetchall():
            try:
                password_hash = _hash_password(str(plain_password))
            except Exception:
                continue

            cur.execute(
                """
                INSERT INTO user_credentials (
                    user_id,
                    password_hash,
                    created_at,
                    updated_at
                ) VALUES (%s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT DO NOTHING
                """,
                (user_id, password_hash),
            )

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


def query_booking_revenue_summary(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> dict:
    """
    Return summary metrics for national rail bookings and payments.

    This function aggregates booking counts, revenue, refunds, and active
    booking status from the `bookings` table. It is useful for analytics
    dashboards or operational reports.
    """
    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Filter the query by date range when provided, otherwise include all rows.
            sql = """
                SELECT
                    COUNT(*) AS total_bookings,
                    COUNT(*) FILTER (WHERE LOWER(COALESCE(status, 'active')) NOT IN ('cancelled', 'canceled')) AS active_bookings,
                    COUNT(*) FILTER (WHERE LOWER(COALESCE(status, 'active')) IN ('cancelled', 'canceled')) AS cancelled_bookings,
                    COALESCE(SUM(price_paid_usd), 0) AS total_revenue_usd,
                    COALESCE(SUM(refund_amount_usd), 0) AS total_refunds_usd
                FROM bookings
                WHERE 1=1
            """

            params: list[str] = []

            if start_date:
                sql += "\n                  AND travel_date >= %s"
                params.append(start_date)

            if end_date:
                sql += "\n                  AND travel_date <= %s"
                params.append(end_date)

            cur.execute(sql, tuple(params))
            record = cur.fetchone()

            # Return a clear JSON object for downstream tools or dashboards.
            return {
                "total_bookings": record["total_bookings"],
                "active_bookings": record["active_bookings"],
                "cancelled_bookings": record["cancelled_bookings"],
                "total_revenue_usd": float(record["total_revenue_usd"] or 0),
                "total_refunds_usd": float(record["total_refunds_usd"] or 0),
                "start_date": start_date,
                "end_date": end_date,
            }


# ─────────────────────────────────────────────────────────────────────────────
# National rail availability
# ─────────────────────────────────────────────────────────────────────────────

def query_national_rail_availability(
    origin_id: str,
    destination_id: str,
    travel_date: Optional[str] = None,
) -> list[dict]:
    """
    Return national rail schedules serving origin before destination,
    resolved through the national_rail_schedule_stops junction table.
    Returns [] when nothing matches (never raises for missing data).
    """
    origin_id = origin_id.upper()
    destination_id = destination_id.upper()

    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if not _table_exists(cur, "national_rail_schedules"):
                return []
            if not _table_exists(cur, "national_rail_schedule_stops"):
                return []

            # Self-join the junction table: the origin stop must appear
            # strictly before the destination stop on the same schedule.
            cur.execute(
                """
                SELECT
                    s.*,
                    o.stop_order AS origin_order,
                    o.travel_time_from_origin_min AS origin_time_min,
                    d.stop_order AS destination_order,
                    d.travel_time_from_origin_min AS destination_time_min
                FROM national_rail_schedules s
                JOIN national_rail_schedule_stops o
                    ON o.schedule_id = s.schedule_id
                   AND o.station_id = %s
                JOIN national_rail_schedule_stops d
                    ON d.schedule_id = s.schedule_id
                   AND d.station_id = %s
                WHERE o.stop_order < d.stop_order
                ORDER BY s.schedule_id
                """,
                (origin_id, destination_id),
            )
            schedules = [dict(row) for row in cur.fetchall()]

            results = []

            for sched in schedules:
                cur.execute(
                    """
                    SELECT station_id
                    FROM national_rail_schedule_stops
                    WHERE schedule_id = %s
                    ORDER BY stop_order
                    """,
                    (sched["schedule_id"],),
                )
                # stop_order is 0-based and contiguous (seeded via enumerate),
                # so list position == stop_order.
                all_stops = [r["station_id"] for r in cur.fetchall()]
                journey_stops = all_stops[
                    sched["origin_order"]: sched["destination_order"] + 1
                ]
                stops_travelled = sched["destination_order"] - sched["origin_order"]

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
                        (sched["schedule_id"], travel_date),
                    )
                    booking_row = cur.fetchone()

                    if booking_row:
                        total_booked = booking_row["total_booked"] or 0
                        standard_booked = booking_row["standard_booked"] or 0
                        first_booked = booking_row["first_booked"] or 0

                duration = max(
                    _as_float(sched.get("destination_time_min"), 0.0)
                    - _as_float(sched.get("origin_time_min"), 0.0),
                    0.0,
                )

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
                        "full_route": all_stops,
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


def _compute_national_rail_fare(fare_classes, fare_class: str, stops_travelled: int) -> dict:
    """Pure fare maths shared by query_national_rail_fare and execute_booking.

    fare_classes is the (possibly raw JSONB) fare_classes value from a
    national_rail_schedules row; falls back to standard class and default
    rates when data is missing.
    """
    fare_classes = _safe_json(fare_classes, {})
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
        "fare_class": fare_class,
        "stops_travelled": stops_travelled,
        "base_fare_usd": base,
        "per_stop_rate_usd": per_stop,
        "total_fare_usd": total,
        "currency": "USD",
    }


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
            fare = _compute_national_rail_fare(
                sched.get("fare_classes"), fare_class, stops_travelled
            )
            return {"schedule_id": schedule_id, **fare}


# ─────────────────────────────────────────────────────────────────────────────
# Metro schedules and fare
# ─────────────────────────────────────────────────────────────────────────────

def query_metro_schedules(origin_id: str, destination_id: str) -> list[dict]:
    """
    Return metro schedules serving origin before destination, resolved
    through the metro_schedule_stops junction table. Returns [] when
    nothing matches.
    """
    origin_id = origin_id.upper()
    destination_id = destination_id.upper()

    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if not _table_exists(cur, "metro_schedules"):
                return []
            if not _table_exists(cur, "metro_schedule_stops"):
                return []

            cur.execute(
                """
                SELECT
                    s.*,
                    o.stop_order AS origin_order,
                    o.travel_time_from_origin_min AS origin_time_min,
                    d.stop_order AS destination_order,
                    d.travel_time_from_origin_min AS destination_time_min
                FROM metro_schedules s
                JOIN metro_schedule_stops o
                    ON o.schedule_id = s.schedule_id
                   AND o.station_id = %s
                JOIN metro_schedule_stops d
                    ON d.schedule_id = s.schedule_id
                   AND d.station_id = %s
                WHERE o.stop_order < d.stop_order
                ORDER BY s.schedule_id
                """,
                (origin_id, destination_id),
            )
            schedules = [dict(row) for row in cur.fetchall()]

            results = []

            for sched in schedules:
                cur.execute(
                    """
                    SELECT station_id
                    FROM metro_schedule_stops
                    WHERE schedule_id = %s
                    ORDER BY stop_order
                    """,
                    (sched["schedule_id"],),
                )
                all_stops = [r["station_id"] for r in cur.fetchall()]
                journey_stops = all_stops[
                    sched["origin_order"]: sched["destination_order"] + 1
                ]
                stops_travelled = sched["destination_order"] - sched["origin_order"]

                duration = max(
                    _as_float(sched.get("destination_time_min"), 0.0)
                    - _as_float(sched.get("origin_time_min"), 0.0),
                    0.0,
                )

                item = dict(sched)
                item.update(
                    {
                        "origin_id": origin_id,
                        "destination_id": destination_id,
                        "stops_travelled": stops_travelled,
                        "journey_stops": journey_stops,
                        "stops_in_order": all_stops,
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


def query_user_bookings(user_email: str) -> dict:
    """
    Return booking history for a user, ALWAYS as
    {"national_rail": [...], "metro": [...]} — empty lists when the
    user or tables are missing (never raises, never returns an error row).
    """
    result: dict = {"national_rail": [], "metro": []}

    profile = query_user_profile(user_email)
    if not profile:
        return result

    user_id = profile["user_id"]

    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if _table_exists(cur, "bookings"):
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
                result["national_rail"] = [dict(row) for row in cur.fetchall()]

            if _table_exists(cur, "metro_trips"):
                cur.execute(
                    """
                    SELECT
                        t.*,
                        ms.line
                    FROM metro_trips t
                    LEFT JOIN metro_schedules ms
                        ON ms.schedule_id = t.schedule_id
                    WHERE t.user_id = %s
                    ORDER BY t.travel_date DESC NULLS LAST, t.created_at DESC NULLS LAST
                    """,
                    (user_id,),
                )
                result["metro"] = [dict(row) for row in cur.fetchall()]

    return result


def query_trip_history(user_email: str, limit: int = 20) -> dict:
    """
    Return a detailed trip history panel for logged-in users.
    
    This function retrieves completed and upcoming bookings with full
    station name details, fare information, and refund status.
    Useful for displaying a trip history panel in the UI.
    """
    profile = query_user_profile(user_email)

    if not profile:
        return {"error": "User profile not found.", "trips": []}

    user_id = profile["user_id"]

    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if not _table_exists(cur, "bookings"):
                return {"user_id": user_id, "trips": []}

            # Retrieve booking details joined with schedule info.
            # Include origin/destination station names and fare class.
            cur.execute(
                """
                SELECT
                    b.booking_id,
                    b.schedule_id,
                    b.origin_station_id,
                    b.destination_station_id,
                    b.travel_date,
                    b.fare_class,
                    b.seat_id,
                    b.ticket_type,
                    b.status,
                    b.price_paid_usd,
                    b.refund_amount_usd,
                    b.booked_at,
                    b.cancelled_at,
                    s.line,
                    s.service_type
                FROM bookings b
                LEFT JOIN national_rail_schedules s
                    ON s.schedule_id = b.schedule_id
                WHERE b.user_id = %s
                ORDER BY b.travel_date DESC NULLS LAST, b.booked_at DESC NULLS LAST
                LIMIT %s
                """,
                (user_id, limit),
            )

            trips = []
            for row in cur.fetchall():
                trip_dict = dict(row)
                
                # Format readable trip entry
                trip_dict["trip_summary"] = (
                    f"{trip_dict.get('origin_station_id')} → {trip_dict.get('destination_station_id')} | "
                    f"{trip_dict.get('travel_date')} | "
                    f"{trip_dict.get('fare_class', 'standard')} | "
                    f"${trip_dict.get('price_paid_usd', 0):.2f}"
                )
                
                trips.append(trip_dict)

            return {
                "user_id": user_id,
                "user_email": profile.get("email"),
                "total_trips_retrieved": len(trips),
                "trips": trips,
            }


def query_route_visualization(origin_station: str, destination_station: str, route_type: str = "national_rail") -> dict:
    """
    # TASK 6 EXTENSION:
    Retrieve detailed route information for visualization.
    
    This function queries either national rail or metro routes between two stations,
    including all intermediate stops, travel times, and fare information.
    Useful for displaying an interactive route map or timeline in the UI.
    
    Args:
        origin_station: Starting station ID (e.g., "NR01")
        destination_station: Ending station ID (e.g., "NR05")
        route_type: Either "national_rail" or "metro"
    
    Returns:
        dict containing:
            - "routes": list of matching routes with stops and timing
            - "origin": origin station ID
            - "destination": destination station ID
            - "count": number of routes found
    """
    if route_type not in ["national_rail", "metro"]:
        return {"error": "Invalid route_type. Must be 'national_rail' or 'metro'.", "routes": []}

    table_name = "national_rail_schedules" if route_type == "national_rail" else "metro_schedules"

    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if not _table_exists(cur, table_name):
                return {"error": f"Table {table_name} not found.", "routes": []}

            # table_name / stops_table are chosen from an in-code whitelist
            # branch on route_type (validated above), NOT from user input, so
            # interpolating them into the f-string SQL is safe.
            stops_table = (
                "national_rail_schedule_stops"
                if route_type == "national_rail"
                else "metro_schedule_stops"
            )

            cur.execute(
                f"""
                SELECT *
                FROM {table_name}
                WHERE origin_station_id = %s AND destination_station_id = %s
                ORDER BY line
                """,
                (origin_station, destination_station),
            )

            routes = []
            for row in cur.fetchall():
                route_dict = dict(row)

                # Resolve ordered stops from the junction table.
                cur.execute(
                    f"""
                    SELECT station_id, stop_order, travel_time_from_origin_min
                    FROM {stops_table}
                    WHERE schedule_id = %s
                    ORDER BY stop_order
                    """,
                    (route_dict["schedule_id"],),
                )
                route_dict["stops_detail"] = [
                    {
                        "station_id": stop["station_id"],
                        "position": stop["stop_order"],
                        "travel_time_min": stop["travel_time_from_origin_min"],
                    }
                    for stop in cur.fetchall()
                ]

                fares = route_dict.get("fare_classes", {})
                route_dict["fare_summary"] = fares if isinstance(fares, dict) else {}

                routes.append(route_dict)

            return {
                "origin": origin_station,
                "destination": destination_station,
                "route_type": route_type,
                "count": len(routes),
                "routes": routes,
            }


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

                # Fetch the schedule's fare table in the same query we use to
                # confirm the schedule exists (single round-trip, same cursor —
                # avoids borrowing a second pool connection mid-transaction).
                cur.execute(
                    """
                    SELECT fare_classes
                    FROM national_rail_schedules
                    WHERE schedule_id = %s
                    """,
                    (schedule_id,),
                )
                schedule_row = cur.fetchone()

                if not schedule_row:
                    return False, f"Schedule {schedule_id} not found."

                origin = origin_station_id.upper()
                destination = destination_station_id.upper()

                # Validate stop order via the junction table.
                cur.execute(
                    """
                    SELECT
                        (SELECT stop_order
                           FROM national_rail_schedule_stops
                          WHERE schedule_id = %s AND station_id = %s) AS origin_order,
                        (SELECT stop_order
                           FROM national_rail_schedule_stops
                          WHERE schedule_id = %s AND station_id = %s) AS destination_order
                    """,
                    (schedule_id, origin, schedule_id, destination),
                )
                orders = cur.fetchone()

                if orders["origin_order"] is None or orders["destination_order"] is None:
                    return False, "Origin or destination is not served by this schedule."

                if orders["origin_order"] >= orders["destination_order"]:
                    return False, "Destination must come after origin on this schedule."

                stops_travelled = orders["destination_order"] - orders["origin_order"]

                # Fare maths shared with query_national_rail_fare, executed on
                # data already fetched on this cursor so booking + payment stay
                # in ONE transaction on ONE connection.
                fare = {
                    "schedule_id": schedule_id,
                    **_compute_national_rail_fare(
                        schedule_row.get("fare_classes"), fare_class, stops_travelled
                    ),
                }
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

                # Record the payment in the SAME transaction as the booking:
                # the context manager commits once on exit, so a payment
                # failure rolls the booking back too (no orphan bookings).
                if _table_exists(cur, "payments"):
                    payment_id = _gen_payment_id()
                    cur.execute(
                        """
                        INSERT INTO payments (
                            payment_id,
                            booking_id,
                            user_id,
                            amount_usd,
                            payment_method,
                            payment_status,
                            paid_at,
                            created_at
                        )
                        VALUES (%s, %s, %s, %s, 'card', 'paid',
                                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                        RETURNING *
                        """,
                        (payment_id, booking_id, user_id, price),
                    )
                    row["payment"] = dict(cur.fetchone())

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

                # Mark the payment refunded in the same transaction.
                if refund > 0 and _table_exists(cur, "payments"):
                    cur.execute(
                        """
                        UPDATE payments
                        SET payment_status = 'refunded'
                        WHERE booking_id = %s
                          AND payment_status = 'paid'
                        """,
                        (booking_id,),
                    )

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
                        first_name,
                        surname,
                        date_of_birth,
                        secret_question,
                        secret_answer,
                        is_active,
                        created_at
                    )
                    VALUES (
                        %s, %s, %s, NULL, %s, %s, %s, %s, %s,
                        TRUE, CURRENT_TIMESTAMP
                    )
                    """,
                    (
                        user_id,
                        full_name,
                        email,
                        first_name,
                        surname,
                        date_of_birth,
                        secret_question,
                        secret_answer,
                    ),
                )

                password_hash = _hash_password(password)
                cur.execute(
                    """
                    INSERT INTO user_credentials (
                        user_id,
                        password_hash,
                        created_at,
                        updated_at
                    ) VALUES (%s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """,
                    (user_id, password_hash),
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
                SELECT u.*, uc.password_hash
                FROM users u
                JOIN user_credentials uc ON uc.user_id = u.user_id
                WHERE LOWER(u.email) = LOWER(%s)
                  AND COALESCE(u.is_active, TRUE) = TRUE
                """,
                (email.strip(),),
            )

            row = cur.fetchone()

            if not row:
                return None

            password_hash = row.pop("password_hash", None)

            if not password_hash:
                return None

            valid, needs_rehash = _verify_password(password_hash, password)
            if not valid:
                return None

            if needs_rehash:
                try:
                    new_hash = _hash_password(password)
                    cur.execute(
                        """
                        UPDATE user_credentials
                        SET password_hash = %s,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE user_id = %s
                        """,
                        (new_hash, row["user_id"]),
                    )
                except Exception:
                    pass

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
                SELECT user_id
                FROM users
                WHERE LOWER(email) = LOWER(%s)
                """,
                (email.strip(),),
            )
            row = cur.fetchone()
            if not row:
                return False

            user_id = row[0]
            password_hash = _hash_password(new_password)

            cur.execute(
                """
                INSERT INTO user_credentials (
                    user_id,
                    password_hash,
                    created_at,
                    updated_at
                ) VALUES (%s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT (user_id) DO UPDATE
                SET password_hash = EXCLUDED.password_hash,
                    updated_at = EXCLUDED.updated_at
                """,
                (user_id, password_hash),
            )

            return True


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