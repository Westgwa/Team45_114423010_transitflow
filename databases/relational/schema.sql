-- ============================================================
-- TransitFlow Relational Database Schema
-- PostgreSQL + pgvector
-- ============================================================

-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;


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
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_policy_documents_embedding
ON policy_documents
USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS idx_policy_documents_category
ON policy_documents (category);


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

    -- Example: ["MS01", "MS02", "MS03"]
    stops_in_order JSONB NOT NULL,

    first_train_time TIME,
    last_train_time TIME,

    -- Example: {"MS01": 0, "MS02": 3, "MS03": 6}
    travel_time_from_origin_min JSONB,

    base_fare_usd NUMERIC(10, 2) DEFAULT 2.00,
    per_stop_rate_usd NUMERIC(10, 2) DEFAULT 0.50,

    frequency_min INT DEFAULT 10,

    -- Example: ["weekday", "weekend"]
    operates_on JSONB,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_metro_schedules_origin
ON metro_schedules (origin_station_id);

CREATE INDEX IF NOT EXISTS idx_metro_schedules_destination
ON metro_schedules (destination_station_id);

CREATE INDEX IF NOT EXISTS idx_metro_schedules_line
ON metro_schedules (line);


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

    -- Example: ["NR01", "NR02", "NR03", "NR05"]
    stops_in_order JSONB NOT NULL,

    -- Optional full station detail from mock data
    passed_through_stations JSONB,

    first_train_time TIME,
    last_train_time TIME,

    -- Example: {"NR01": 0, "NR02": 15, "NR05": 50}
    travel_time_from_origin_min JSONB,

    -- Example:
    -- {
    --   "standard": {"base_fare_usd": 5, "per_stop_rate_usd": 2},
    --   "first": {"base_fare_usd": 12, "per_stop_rate_usd": 4}
    -- }
    fare_classes JSONB,

    frequency_min INT DEFAULT 60,

    -- Example: ["weekday", "weekend"]
    operates_on JSONB,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_national_rail_schedules_origin
ON national_rail_schedules (origin_station_id);

CREATE INDEX IF NOT EXISTS idx_national_rail_schedules_destination
ON national_rail_schedules (destination_station_id);

CREATE INDEX IF NOT EXISTS idx_national_rail_schedules_line
ON national_rail_schedules (line);


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

    -- For this course project demo only.
    -- In a real system, passwords should be hashed, not stored as plain text.
    password TEXT,

    date_of_birth DATE,

    secret_question TEXT,
    secret_answer TEXT,

    is_active BOOLEAN DEFAULT TRUE,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

    fare_class VARCHAR(30) DEFAULT 'standard',

    -- optional metadata
    seat_type VARCHAR(30),
    is_window BOOLEAN DEFAULT FALSE,
    is_aisle BOOLEAN DEFAULT FALSE,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

    fare_class VARCHAR(30) DEFAULT 'standard',
    seat_id VARCHAR(50),
    ticket_type VARCHAR(30) DEFAULT 'single',

    status VARCHAR(30) DEFAULT 'active',

    price_paid_usd NUMERIC(10, 2),
    refund_amount_usd NUMERIC(10, 2) DEFAULT 0.00,

    booked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    cancelled_at TIMESTAMP,

    CONSTRAINT fk_bookings_user
        FOREIGN KEY (user_id)
        REFERENCES users(user_id)
        ON DELETE SET NULL
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

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_metro_trips_user
        FOREIGN KEY (user_id)
        REFERENCES users(user_id)
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
    payment_status VARCHAR(30) DEFAULT 'paid',

    paid_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_feedback_user
ON feedback (user_id);

CREATE INDEX IF NOT EXISTS idx_feedback_booking
ON feedback (booking_id);

-- ============================================================
-- [優化] 針對 Metro schedules 的 JSONB 欄位建立 GIN 索引
-- ============================================================

CREATE INDEX IF NOT EXISTS idx_metro_schedules_stops_gin
ON metro_schedules USING GIN (stops_in_order);

CREATE INDEX IF NOT EXISTS idx_metro_schedules_operates_on_gin
ON metro_schedules USING GIN (operates_on);

CREATE INDEX IF NOT EXISTS idx_metro_schedules_travel_time_gin
ON metro_schedules USING GIN (travel_time_from_origin_min);


-- ============================================================
-- [優化] 針對 National rail schedules 的 JSONB 欄位建立 GIN 索引
-- ============================================================

CREATE INDEX IF NOT EXISTS idx_national_rail_schedules_stops_gin
ON national_rail_schedules USING GIN (stops_in_order);

CREATE INDEX IF NOT EXISTS idx_national_rail_schedules_passed_gin
ON national_rail_schedules USING GIN (passed_through_stations);

CREATE INDEX IF NOT EXISTS idx_national_rail_schedules_operates_on_gin
ON national_rail_schedules USING GIN (operates_on);

CREATE INDEX IF NOT EXISTS idx_national_rail_schedules_travel_time_gin
ON national_rail_schedules USING GIN (travel_time_from_origin_min);

CREATE INDEX IF NOT EXISTS idx_national_rail_schedules_fare_classes_gin
ON national_rail_schedules USING GIN (fare_classes);

-- ============================================================
-- Schema ready
-- ============================================================