-- ============================================================
-- TransitFlow Relational Database Schema
-- PostgreSQL + pgvector
-- ============================================================

-- TASK 6 EXTENSION: adds the vw_booking_revenue_daily analytics view and a
-- supporting travel_date index near the end of this file (see section 6b).

-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- ============================================================
-- Primary-key strategy
-- Most tables use natural VARCHAR business keys (e.g. 'MS01',
-- 'NR_SCH01', 'BK-XXXXXX') because the mock dataset ships with
-- stable, human-readable identifiers referenced across files;
-- reusing them avoids surrogate-key mapping during seeding and
-- keeps FK values readable in graded query output.
-- policy_documents uses SERIAL because RAG chunks have no
-- natural identifier and are only referenced internally.
--
-- Deletion strategy (rule = how much the child depends on the parent):
--   * dependent detail rows (credentials, schedule stops, payments)
--       -> ON DELETE CASCADE   (meaningless without their parent)
--   * hard operational references (bookings -> schedule/seat,
--     schedules -> stations)
--       -> ON DELETE RESTRICT  (never silently lose a sold journey)
--   * soft audit/history links (bookings.user_id, metro_trips.*,
--     feedback.*)
--       -> ON DELETE SET NULL  (keep the financial/audit record)
-- ============================================================


-- ============================================================
-- 1. Policy documents table
-- Used by pgvector / RAG policy search
-- ============================================================

CREATE TABLE IF NOT EXISTS policy_documents (
    id SERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    category TEXT NOT NULL,
    content TEXT NOT NULL,
    embedding vector(768),
    source_file TEXT,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_policy_documents_embedding
ON policy_documents
USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS idx_policy_documents_category
ON policy_documents (category);


-- ============================================================
-- 1b. Stations
-- Parents for schedule stops, schedules, bookings and trips.
-- Mirrors train-mock-data/metro_stations.json and
-- national_rail_stations.json (adjacency lives in Neo4j).
-- ============================================================

CREATE TABLE IF NOT EXISTS metro_stations (
    station_id VARCHAR(20) PRIMARY KEY,   -- natural key, e.g. 'MS01'
    name VARCHAR(100) NOT NULL,

    -- e.g. ["M1", "M2"]; kept as JSONB because lines are a small,
    -- read-only display attribute (graph routing lives in Neo4j)
    lines JSONB,

    is_interchange_metro BOOLEAN DEFAULT FALSE,
    is_interchange_national_rail BOOLEAN DEFAULT FALSE,
    interchange_national_rail_station_id VARCHAR(20),

    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS national_rail_stations (
    station_id VARCHAR(20) PRIMARY KEY,   -- natural key, e.g. 'NR01'
    name VARCHAR(100) NOT NULL,

    lines JSONB,

    is_interchange_national_rail BOOLEAN DEFAULT FALSE,
    is_interchange_metro BOOLEAN DEFAULT FALSE,
    interchange_metro_station_id VARCHAR(20),

    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);


-- ============================================================
-- 2. Metro schedules
-- Used for metro availability and metro fare calculation
-- ============================================================

CREATE TABLE IF NOT EXISTS metro_schedules (
    schedule_id VARCHAR(30) PRIMARY KEY,

    line VARCHAR(20) NOT NULL,
    direction VARCHAR(50),

    origin_station_id VARCHAR(20) NOT NULL,
    destination_station_id VARCHAR(20) NOT NULL,

    first_train_time TIME,
    last_train_time TIME,

    base_fare_usd NUMERIC(10, 2) DEFAULT 2.00,
    per_stop_rate_usd NUMERIC(10, 2) DEFAULT 0.50,

    frequency_min INT DEFAULT 10,

    -- Example: ["weekday", "weekend"]
    operates_on JSONB,

    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_metro_schedules_origin
        FOREIGN KEY (origin_station_id)
        REFERENCES metro_stations(station_id)
        ON DELETE RESTRICT,

    CONSTRAINT fk_metro_schedules_destination
        FOREIGN KEY (destination_station_id)
        REFERENCES metro_stations(station_id)
        ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_metro_schedules_origin
ON metro_schedules (origin_station_id);

CREATE INDEX IF NOT EXISTS idx_metro_schedules_destination
ON metro_schedules (destination_station_id);

CREATE INDEX IF NOT EXISTS idx_metro_schedules_line
ON metro_schedules (line);

-- ============================================================
-- 2b. Metro schedule stops (junction table)
-- Replaces the former stops_in_order / travel_time_from_origin_min
-- JSONB columns: one row per (schedule, station) with an explicit
-- 0-based stop_order. This is the 3NF decomposition required for
-- ordered many-to-many schedule->station data.
-- ============================================================

CREATE TABLE IF NOT EXISTS metro_schedule_stops (
    schedule_id VARCHAR(30) NOT NULL,
    station_id VARCHAR(20) NOT NULL,

    stop_order INT NOT NULL,                          -- 0 = origin
    travel_time_from_origin_min INT NOT NULL DEFAULT 0,

    PRIMARY KEY (schedule_id, stop_order),
    UNIQUE (schedule_id, station_id),

    CONSTRAINT fk_metro_schedule_stops_schedule
        FOREIGN KEY (schedule_id)
        REFERENCES metro_schedules(schedule_id)
        ON DELETE CASCADE,

    CONSTRAINT fk_metro_schedule_stops_station
        FOREIGN KEY (station_id)
        REFERENCES metro_stations(station_id)
        ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_metro_schedule_stops_station
ON metro_schedule_stops (station_id);


-- ============================================================
-- 3. National rail schedules
-- Used for national rail availability and fare lookup
-- ============================================================

CREATE TABLE IF NOT EXISTS national_rail_schedules (
    schedule_id VARCHAR(30) PRIMARY KEY,

    line VARCHAR(20) NOT NULL,
    service_type VARCHAR(50),
    direction VARCHAR(50),

    origin_station_id VARCHAR(20) NOT NULL,
    destination_station_id VARCHAR(20) NOT NULL,

    -- Optional full station detail from mock data
    passed_through_stations JSONB,

    first_train_time TIME,
    last_train_time TIME,

    -- Example:
    -- {
    --   "standard": {"base_fare_usd": 5, "per_stop_rate_usd": 2},
    --   "first": {"base_fare_usd": 12, "per_stop_rate_usd": 4}
    -- }
    fare_classes JSONB,

    frequency_min INT DEFAULT 60,

    -- Example: ["weekday", "weekend"]
    operates_on JSONB,

    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_national_rail_schedules_origin
        FOREIGN KEY (origin_station_id)
        REFERENCES national_rail_stations(station_id)
        ON DELETE RESTRICT,

    CONSTRAINT fk_national_rail_schedules_destination
        FOREIGN KEY (destination_station_id)
        REFERENCES national_rail_stations(station_id)
        ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_national_rail_schedules_origin
ON national_rail_schedules (origin_station_id);

CREATE INDEX IF NOT EXISTS idx_national_rail_schedules_destination
ON national_rail_schedules (destination_station_id);

CREATE INDEX IF NOT EXISTS idx_national_rail_schedules_line
ON national_rail_schedules (line);

-- ============================================================
-- 3b. National rail schedule stops (junction table)
-- Same 3NF decomposition as metro_schedule_stops.
-- ============================================================

CREATE TABLE IF NOT EXISTS national_rail_schedule_stops (
    schedule_id VARCHAR(30) NOT NULL,
    station_id VARCHAR(20) NOT NULL,

    stop_order INT NOT NULL,                          -- 0 = origin
    travel_time_from_origin_min INT NOT NULL DEFAULT 0,

    PRIMARY KEY (schedule_id, stop_order),
    UNIQUE (schedule_id, station_id),

    CONSTRAINT fk_national_rail_schedule_stops_schedule
        FOREIGN KEY (schedule_id)
        REFERENCES national_rail_schedules(schedule_id)
        ON DELETE CASCADE,

    CONSTRAINT fk_national_rail_schedule_stops_station
        FOREIGN KEY (station_id)
        REFERENCES national_rail_stations(station_id)
        ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_national_rail_schedule_stops_station
ON national_rail_schedule_stops (station_id);


-- ============================================================
-- 4. Users
-- Used for login, registration, profile, and booking queries
-- ============================================================

CREATE TABLE IF NOT EXISTS users (
    user_id VARCHAR(30) PRIMARY KEY,

    full_name VARCHAR(100) NOT NULL,
    first_name VARCHAR(100),
    surname VARCHAR(100),

    email VARCHAR(150) UNIQUE NOT NULL,
    phone VARCHAR(50),

    date_of_birth DATE,

    secret_question TEXT,
    secret_answer TEXT,

    is_active BOOLEAN DEFAULT TRUE,

    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS user_credentials (
    user_id VARCHAR(30) PRIMARY KEY,
    password_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_user_credentials_user
        FOREIGN KEY (user_id)
        REFERENCES users(user_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_users_email
ON users (email);

CREATE INDEX IF NOT EXISTS idx_users_active
ON users (is_active);


-- ============================================================
-- 5. Seat layouts
-- Used for checking available seats
-- ============================================================

CREATE TABLE IF NOT EXISTS seat_layouts (
    seat_id VARCHAR(50) PRIMARY KEY,

    schedule_id VARCHAR(30) NOT NULL,

    carriage_no VARCHAR(20),
    seat_no VARCHAR(20),

    fare_class VARCHAR(30) DEFAULT 'standard' CHECK (fare_class IN ('standard', 'first')),

    -- optional metadata
    seat_type VARCHAR(30),
    is_window BOOLEAN DEFAULT FALSE,
    is_aisle BOOLEAN DEFAULT FALSE,

    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_seat_layouts_schedule
        FOREIGN KEY (schedule_id)
        REFERENCES national_rail_schedules(schedule_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_seat_layouts_schedule
ON seat_layouts (schedule_id);

CREATE INDEX IF NOT EXISTS idx_seat_layouts_fare_class
ON seat_layouts (fare_class);

CREATE INDEX IF NOT EXISTS idx_seat_layouts_schedule_class
ON seat_layouts (schedule_id, fare_class);


-- ============================================================
-- 6. Bookings
-- Used for national rail bookings, cancellation, and seat occupancy
-- ============================================================

CREATE TABLE IF NOT EXISTS bookings (
    booking_id VARCHAR(30) PRIMARY KEY,

    user_id VARCHAR(30),
    schedule_id VARCHAR(30),

    origin_station_id VARCHAR(20),
    destination_station_id VARCHAR(20),

    travel_date DATE,

    fare_class VARCHAR(30) DEFAULT 'standard' CHECK (fare_class IN ('standard', 'first')),
    seat_id VARCHAR(50),
    ticket_type VARCHAR(30) DEFAULT 'single',

    status VARCHAR(30) DEFAULT 'active' CHECK (status IN ('active', 'confirmed', 'completed', 'cancelled')),

    price_paid_usd NUMERIC(10, 2),
    refund_amount_usd NUMERIC(10, 2) DEFAULT 0.00,

    booked_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    cancelled_at TIMESTAMPTZ,

    CONSTRAINT fk_bookings_user
        FOREIGN KEY (user_id)
        REFERENCES users(user_id)
        ON DELETE SET NULL,

    CONSTRAINT fk_bookings_schedule
        FOREIGN KEY (schedule_id)
        REFERENCES national_rail_schedules(schedule_id)
        ON DELETE RESTRICT,

    CONSTRAINT fk_bookings_seat
        FOREIGN KEY (seat_id)
        REFERENCES seat_layouts(seat_id)
        ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_bookings_user
ON bookings (user_id);

CREATE INDEX IF NOT EXISTS idx_bookings_schedule_date
ON bookings (schedule_id, travel_date);

CREATE INDEX IF NOT EXISTS idx_bookings_status
ON bookings (status);

CREATE INDEX IF NOT EXISTS idx_bookings_seat
ON bookings (seat_id);

CREATE INDEX IF NOT EXISTS idx_bookings_user_date
ON bookings (user_id, travel_date);

-- ============================================================
-- 6b. Booking analytics — TASK 6 EXTENSION
-- ============================================================
-- TASK 6 EXTENSION: A daily financial rollup view that backs the booking
-- analytics dashboard. Pre-aggregating per travel_date keeps the dashboard
-- query simple (the application only SUMs the days inside the chosen range)
-- and means the heavy GROUP BY lives in one auditable place in the schema
-- rather than being re-expressed in application SQL.
CREATE OR REPLACE VIEW vw_booking_revenue_daily AS
SELECT
    travel_date,
    COUNT(*)                                                                                   AS total_bookings,
    COUNT(*) FILTER (WHERE LOWER(COALESCE(status, 'active')) NOT IN ('cancelled', 'canceled')) AS active_bookings,
    COUNT(*) FILTER (WHERE LOWER(COALESCE(status, 'active')) IN ('cancelled', 'canceled'))     AS cancelled_bookings,
    COALESCE(SUM(price_paid_usd), 0)                                                           AS revenue_usd,
    COALESCE(SUM(refund_amount_usd), 0)                                                        AS refunds_usd
FROM bookings
GROUP BY travel_date;

-- TASK 6 EXTENSION: supports the date-range filter the analytics dashboard
-- applies on travel_date (the existing composite index leads with
-- schedule_id, so it cannot serve a travel_date-only range scan).
CREATE INDEX IF NOT EXISTS idx_bookings_travel_date
ON bookings (travel_date);


-- ============================================================
-- 7. Metro trips
-- Optional table for metro trip history
-- Used if the system wants to store completed metro journeys
-- ============================================================

CREATE TABLE IF NOT EXISTS metro_trips (
    trip_id VARCHAR(30) PRIMARY KEY,

    user_id VARCHAR(30),
    schedule_id VARCHAR(30),

    origin_station_id VARCHAR(20),
    destination_station_id VARCHAR(20),

    travel_date DATE,
    fare_paid_usd NUMERIC(10, 2),

    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_metro_trips_user
        FOREIGN KEY (user_id)
        REFERENCES users(user_id)
        ON DELETE SET NULL,

    CONSTRAINT fk_metro_trips_schedule
        FOREIGN KEY (schedule_id)
        REFERENCES metro_schedules(schedule_id)
        ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_metro_trips_user
ON metro_trips (user_id);

CREATE INDEX IF NOT EXISTS idx_metro_trips_schedule
ON metro_trips (schedule_id);

CREATE INDEX IF NOT EXISTS idx_metro_trips_user_date
ON metro_trips (user_id, travel_date);


-- ============================================================
-- 8. Payments
-- Optional table for payment records
-- ============================================================

CREATE TABLE IF NOT EXISTS payments (
    payment_id VARCHAR(30) PRIMARY KEY,

    booking_id VARCHAR(30),
    user_id VARCHAR(30),

    amount_usd NUMERIC(10, 2),
    payment_method VARCHAR(50),
    payment_status VARCHAR(30) DEFAULT 'paid' CHECK (payment_status IN ('paid', 'pending', 'failed', 'refunded')),

    paid_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_payments_booking
        FOREIGN KEY (booking_id)
        REFERENCES bookings(booking_id)
        ON DELETE CASCADE,

    CONSTRAINT fk_payments_user
        FOREIGN KEY (user_id)
        REFERENCES users(user_id)
        ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_payments_booking
ON payments (booking_id);

CREATE INDEX IF NOT EXISTS idx_payments_user
ON payments (user_id);

CREATE INDEX IF NOT EXISTS idx_payments_status
ON payments (payment_status);


-- ============================================================
-- 9. Feedback
-- Optional table for user feedback
-- ============================================================

CREATE TABLE IF NOT EXISTS feedback (
    feedback_id VARCHAR(30) PRIMARY KEY,

    user_id VARCHAR(30),
    booking_id VARCHAR(30),

    rating INT,
    comment TEXT,

    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_feedback_user
        FOREIGN KEY (user_id)
        REFERENCES users(user_id)
        ON DELETE SET NULL,

    CONSTRAINT fk_feedback_booking
        FOREIGN KEY (booking_id)
        REFERENCES bookings(booking_id)
        ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_feedback_user
ON feedback (user_id);

CREATE INDEX IF NOT EXISTS idx_feedback_booking
ON feedback (booking_id);

-- ============================================================
-- [Optimization] Create GIN indexes for JSONB columns in `metro_schedules`
-- ============================================================

CREATE INDEX IF NOT EXISTS idx_metro_schedules_operates_on_gin
ON metro_schedules USING GIN (operates_on);


-- ============================================================
-- [Optimization] Create GIN indexes for JSONB columns in `national_rail_schedules`
-- ============================================================

CREATE INDEX IF NOT EXISTS idx_national_rail_schedules_passed_gin
ON national_rail_schedules USING GIN (passed_through_stations);

CREATE INDEX IF NOT EXISTS idx_national_rail_schedules_operates_on_gin
ON national_rail_schedules USING GIN (operates_on);

CREATE INDEX IF NOT EXISTS idx_national_rail_schedules_fare_classes_gin
ON national_rail_schedules USING GIN (fare_classes);

-- ============================================================
-- Schema ready
-- ============================================================
