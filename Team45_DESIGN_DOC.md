# TransitFlow Design Document — Team 45

**Team Members:** 郭明儒 (114423010, Team Leader) · 卓少筠 (113403005) · 林楷崋 (113403018)

## Section 1 — Entity-Relationship Diagram

```mermaid
erDiagram
    users ||--|| user_credentials : "1:1 has"
    users |o--o{ bookings : "1:N makes"
    users |o--o{ metro_trips : "1:N takes"
    users |o--o{ payments : "1:N pays"
    users |o--o{ feedback : "1:N writes"
    bookings ||--o{ payments : "1:N paid by"
    bookings |o--o{ feedback : "1:N reviewed in"
    seat_layouts |o--o{ bookings : "1:N reserved in"
    national_rail_schedules ||--o{ bookings : "1:N booked on"
    national_rail_schedules ||--o{ seat_layouts : "1:N has seats"
    national_rail_schedules ||--o{ national_rail_schedule_stops : "1:N ordered stops"
    national_rail_stations ||--o{ national_rail_schedule_stops : "1:N appears in"
    national_rail_stations ||--o{ national_rail_schedules : "1:N origin/dest of"
    metro_schedules ||--o{ metro_schedule_stops : "1:N ordered stops"
    metro_stations ||--o{ metro_schedule_stops : "1:N appears in"
    metro_stations ||--o{ metro_schedules : "1:N origin/dest of"
    metro_schedules |o--o{ metro_trips : "1:N used by"

    users {
        varchar user_id PK
        varchar email UK
        varchar full_name
        date date_of_birth
    }
    user_credentials {
        varchar user_id PK, FK
        text password_hash
        timestamptz updated_at
    }
    metro_stations {
        varchar station_id PK
        varchar name
        jsonb lines
    }
    national_rail_stations {
        varchar station_id PK
        varchar name
        jsonb lines
    }
    metro_schedules {
        varchar schedule_id PK
        varchar origin_station_id FK
        varchar destination_station_id FK
        numeric base_fare_usd
        numeric per_stop_rate_usd
    }
    national_rail_schedules {
        varchar schedule_id PK
        varchar origin_station_id FK
        varchar destination_station_id FK
        jsonb fare_classes
        varchar service_type
    }
    metro_schedule_stops {
        varchar schedule_id PK, FK
        int stop_order PK
        varchar station_id FK
        int travel_time_from_origin_min
    }
    national_rail_schedule_stops {
        varchar schedule_id PK, FK
        int stop_order PK
        varchar station_id FK
        int travel_time_from_origin_min
    }
    seat_layouts {
        varchar seat_id PK
        varchar schedule_id FK
        varchar fare_class
        varchar seat_no
    }
    bookings {
        varchar booking_id PK
        varchar user_id FK
        varchar schedule_id FK
        varchar seat_id FK
        numeric price_paid_usd
        varchar status
    }
    metro_trips {
        varchar trip_id PK
        varchar user_id FK
        varchar schedule_id FK
        numeric fare_paid_usd
    }
    payments {
        varchar payment_id PK
        varchar booking_id FK
        varchar user_id FK
        numeric amount_usd
        varchar payment_status
    }
    feedback {
        varchar feedback_id PK
        varchar user_id FK
        varchar booking_id FK
        int rating
    }
    policy_documents {
        serial id PK
        text title
        vector_768 embedding
        text source_file
    }
```

The diagram uses crow's-foot notation, where the symbol *on each line* states the cardinality of both ends. `||` is "exactly one", `o{` is "zero or many", and `|o` is "zero or one". The `|o` (optional one) appears wherever the child's foreign key is nullable and uses `ON DELETE SET NULL`: for example `users |o--o{ bookings` means a booking *may* reference a user, but `bookings.user_id` is set to `NULL` if that user is deleted, so the financial/audit row survives. By contrast `national_rail_schedules ||--o{ bookings` is a mandatory one (a booking must reference a real schedule, enforced with `ON DELETE RESTRICT`), and `users ||--|| user_credentials` is a strict 1:1 because credentials share the user's primary key and cascade-delete with the user.

`policy_documents` is intentionally drawn with no relationships: it is the Retrieval-Augmented-Generation (RAG) vector store, holding policy text plus a `vector(768)` embedding, and is reached only by similarity search — it is never joined to operational tables, so adding a foreign key would be meaningless. The conceptually many-to-many relationship between a schedule and the stations it visits ("a schedule passes through many stations; a station appears in many schedules") is resolved by the two associative entities `metro_schedule_stops` and `national_rail_schedule_stops`. Each carries the relationship's own attributes — `stop_order` (0-based position) and `travel_time_from_origin_min` — and uses the composite primary key `(schedule_id, stop_order)`, which is the textbook decomposition of an M:N relationship with attributes into a junction table.

## Section 2 — Normalisation Justification

### 2.1 Schedule stops: junction table (3NF decision)

The earlier design stored the ordered stops directly inside each schedule row as a `stops_in_order` JSONB array, with a parallel `travel_time_from_origin_min` JSONB array holding one number per stop. This is unsatisfactory on several concrete counts:

- **1NF violation (non-atomic columns).** A single column held a list of stops; the position of an element carried meaning, which is exactly the repeating-group anti-pattern 1NF forbids.
- **Hidden update anomaly.** The stops array and the travel-time array were two independent lists that had to be kept index-aligned by hand. Inserting one stop required editing two columns in lock-step; nothing in the database prevented them from drifting out of sync.
- **No referential integrity.** A stop was just a string inside JSON, so the database could not guarantee it named a real station — a typo such as `"NR99"` would be accepted silently.
- **Poor query shape.** "Is there service from A to B on this schedule, and how long does it take?" required scanning every schedule, parsing the JSON in Python, and locating both stations by array position — a full scan plus application-side logic that no index could accelerate.

The new design extracts a junction relation `metro_schedule_stops(schedule_id, station_id, stop_order, travel_time_from_origin_min)` (and the rail twin `national_rail_schedule_stops`). It is in 3NF:

- **Composite primary key `(schedule_id, stop_order)`.** Every non-key attribute (`station_id`, `travel_time_from_origin_min`) is functionally dependent on the *whole* key and on nothing but the key — there are no partial dependencies (2NF) and no transitive dependencies through a non-key attribute (3NF).
- **`UNIQUE (schedule_id, station_id)`** forbids a schedule from listing the same station twice.
- **Foreign keys with deliberate delete rules:** `schedule_id` references the schedule with `ON DELETE CASCADE` (stops are meaningless without their schedule), and `station_id` references the station table with `ON DELETE RESTRICT` (a station that is still part of a route cannot be silently removed). A supporting index on `station_id` backs reverse look-ups.

The change is visible in the query shape. Availability between an origin and a destination is now a single set-based self-join over the junction table, with the ordering enforced by `stop_order`:

```sql
-- abbreviated from the availability / fare queries in queries.py
JOIN national_rail_schedule_stops o ON o.schedule_id = s.schedule_id
JOIN national_rail_schedule_stops d ON d.schedule_id = s.schedule_id
WHERE o.station_id = %s
  AND d.station_id = %s
  AND o.stop_order < d.stop_order;   -- destination must come after origin
```

This is index-backed and declarative; the count of intermediate stops (used for fare maths) is simply `d.stop_order - o.stop_order`, with no JSON parsing in Python.

### 2.2 Password storage

Credentials live in `user_credentials.password_hash` and are hashed with **Argon2id** via the `argon2-cffi` library (`PasswordHasher`).

- **Why not MD5 or SHA-1.** Those are *fast, general-purpose* digests designed to hash large messages quickly. That speed is the problem for passwords: commodity GPUs compute billions of MD5/SHA-1 hashes per second, so an offline brute-force or dictionary attack against a leaked hash table is cheap. SHA-1 additionally has a demonstrated practical collision (the SHAttered attack, 2017), and neither algorithm includes a salt or any deliberate cost.
- **Why Argon2id.** Argon2id is a *memory-hard key-derivation function* with three independently configurable cost parameters — time cost (iterations), memory cost (KiB used), and parallelism. Making each guess expensive in memory as well as CPU defeats the economics of GPU/ASIC cracking, because attackers can no longer trade cheap silicon for throughput. This deliberate slowness (key stretching) is the feature, not a bug.
- **Salting.** `argon2-cffi` generates a random per-password salt and embeds it in the self-describing encoded hash string. Two users with the same password therefore get different stored hashes, which makes precomputed rainbow tables useless.
- **Single source of truth.** The seeder and the runtime verification path (`login_user` → `_verify_password`) use the *same* `PasswordHasher` instance/settings, so hashes are produced and checked consistently. `_verify_password` also returns the result of `check_needs_rehash`, letting the system transparently re-hash a password with upgraded parameters the next time the user logs in.

### 2.3 Deliberate denormalisation kept

Four JSONB columns are retained on purpose: `fare_classes` and `operates_on` on the schedule tables, `lines` on the station tables, and `passed_through_stations` on `national_rail_schedules`. The justification is concrete, not "JSON is convenient":

- They are **read-only display/configuration attributes** — fare-class labels, days of operation, line tags — rendered to the UI or used as filters, never updated piecemeal.
- They are **never used as a join key** and need **no referential-integrity constraint**, so a junction table would buy nothing.
- Decomposing them would add three to four extra tables (and joins) for *zero* query benefit.

The rule applied throughout is simple: anything that participates in a join or a constraint is fully normalised; data that is only ever read back whole is allowed to stay as a JSONB document (and is still indexed with GIN indexes where filtering is needed).

## Section 3 — Graph Database Design Rationale

**Nodes, relationships, and properties.** The network topology is modelled in Neo4j. A **node** is a `Station`, additionally labelled `MetroStation` or `NationalRailStation` so each sub-network can be queried in isolation. Node identity is the business key `station_id`, enforced by `CREATE CONSTRAINT station_id_unique IF NOT EXISTS` (a uniqueness constraint that also creates a backing index). **Relationships** are directed edges between stations: `METRO_LINK` and `RAIL_LINK` model intra-network adjacency (seeded as directional pairs), while `INTERCHANGE_TO` models a cross-network transfer and is seeded in *both* directions with `travel_time_min = 5` and a fare of `0.0` (transferring between the metro and rail platforms is free). Edge **properties** hold the routing weights: `travel_time_min` (integer), `fare_standard`, and `fare_first`. On rail links `fare_standard = travel_time_min * 0.35` and `fare_first = travel_time_min * 0.60`; metro links use a flat standard fare.

**Why a graph beats the relational store for this workload.** Route planning is a *transitive-closure* problem over an adjacency relation — "find a path of unknown length from A to B" — which is precisely what relational algebra handles badly. In PostgreSQL the only native tool is a `WITH RECURSIVE` CTE, which forces the application to: carry a visited-path array to avoid cycles manually, impose an arbitrary depth cap, and pay a join cost that grows with every level of recursion, all *without* native weighted-priority expansion (you cannot tell the recursion to explore the cheapest frontier first). Neo4j instead stores adjacency as direct node-to-node pointers (index-free adjacency), so traversal cost depends on the size of the *answer*, not the size of the tables. A single call to `apoc.algo.dijkstra(start, end, 'METRO_LINK>|RAIL_LINK>|INTERCHANGE_TO>', 'travel_time_min')` returns the weighted shortest path with the correct algorithm (Dijkstra) doing the priority-queue work for us.

**Worked example 1 — shortest route.** The actual Cypher from `query_shortest_route` (abbreviated):

```cypher
MATCH (start:Station {station_id: $origin_id})
MATCH (end:Station   {station_id: $destination_id})
CALL apoc.algo.dijkstra(
    start, end,
    'METRO_LINK>|RAIL_LINK>|INTERCHANGE_TO>',
    'travel_time_min'
) YIELD path, weight
RETURN weight AS total_time,
       [n IN nodes(path)         | n.station_id] AS stations,
       [r IN relationships(path) | r.travel_time_min] AS legs;
```

The relational equivalent has to reinvent the traversal by hand:

```sql
WITH RECURSIVE route(curr, dest, total_min, path, depth) AS (
    SELECT origin_id, origin_id, 0, ARRAY[origin_id], 0
    UNION ALL
    SELECT e.to_station, r.dest, r.total_min + e.travel_time_min,
           r.path || e.to_station, r.depth + 1
    FROM route r
    JOIN station_edges e ON e.from_station = r.curr
    WHERE e.to_station <> ALL(r.path)   -- manual cycle avoidance
      AND r.depth < 12                  -- arbitrary depth cap
)
SELECT path, total_min FROM route
WHERE curr = $destination_id
ORDER BY total_min LIMIT 1;            -- "shortest" only after full enumeration
```

Note the recursive CTE enumerates *all* simple paths and only picks the minimum afterwards — there is no priority-driven early termination, so it does strictly more work than Dijkstra.

**Worked example 2 — delay ripple.** "Which stations are within N hops of a delayed station, and how far away?" uses a variable-length pattern with hop bookkeeping (from `query_delay_ripple`):

```cypher
MATCH p = (start:Station {station_id: $station_id})
          -[:METRO_LINK|RAIL_LINK|INTERCHANGE_TO*1..N]-(affected:Station)
WITH affected, min(length(p)) AS hops_away
RETURN affected.station_id, hops_away
ORDER BY hops_away ASC;
```

The `min(length(p))` keeps the shortest distance when a station is reachable by several paths. In SQL this is again an iterative CTE (or N self-joins) that has to track and `MIN`/`GREATEST` the hop count per station manually.

**Fare-class routing.** The cheapest-route query (`query_cheapest_route`) reuses the same Dijkstra call but swaps the *weight property* from a small whitelist: `weight_property = "fare_first" if fare_class == "first" else "fare_standard"`. Because first class costs more per minute on rail (`0.60` vs `0.35`), a first-class request can return a different total — and sometimes a different path — than a standard request over the identical topology. The whitelist is what keeps the property swap injection-safe.

## Section 4 — Vector / RAG Design

**RAG pipeline (numbered).**

1. The user's message is converted to an embedding vector by the configured provider (`llm_provider.py`).
2. `query_policy_vector_search` runs a cosine-similarity search over `policy_documents` using pgvector — `1 - (embedding <=> %s::vector)` as the similarity score, accelerated by the HNSW cosine index (`idx_policy_documents_embedding`). It keeps rows above the `VECTOR_SIMILARITY_THRESHOLD` of `0.5` and returns the top `VECTOR_TOP_K = 3`.
3. The retrieved policy chunks are injected into the system prompt as grounding context.
4. The LLM answers using that context, so the reply is grounded in real policy text rather than the model's parametric memory.
5. The same agent can additionally call SQL and graph tools, so a single answer can combine retrieved policy text with live database facts.

**Embedding dimensions.**

| Provider | Model | Dimension |
|----------|-------|-----------|
| Ollama | nomic-embed-text | 768 |
| Gemini | gemini-embedding-001 | 3072 |

The schema column is `vector(768)`, matching the default Ollama provider.

**Dimension-mismatch hazard (required).** Embeddings are seeded at 768 dimensions, so the stored vectors and the HNSW index are *dimension-bound* to 768. If the provider were switched to Gemini, every query embedding would be 3072-dimensional, and pgvector raises a hard dimension-mismatch error when comparing a 3072-d query vector against a 768-d column — the search simply cannot run. Changing providers is therefore not a config flip: it requires `ALTER`ing the column type to the new dimension, dropping and rebuilding the HNSW index, *and* re-embedding every document from scratch. We mitigate this by pinning a single provider through the `LLM_PROVIDER` environment variable so seeding and querying always agree.

## Section 5 — AI Tool Usage Evidence

The cases below are drawn from the project's development log; each one shows how the AI output was *validated* rather than trusted.

| # | Context | Prompt (summary) | Outcome |
|---|---------|------------------|---------|
| 1 | Implementing the metro↔rail transfer relationships in Neo4j | "Design the graph schema for interchanges between the metro and national rail networks" | **AI got it wrong:** the assistant named the relationship `INTERCHANGES_WITH`, but the assignment spec requires `INTERCHANGE_TO`. Caught while cross-checking the grading guide; renamed in the seeder and every graph query, and added a simulation check asserting zero legacy relationship names remain. |
| 2 | Merging the password-security branch | "Refactor password hashing/verification into shared helpers" | **AI got it wrong:** after the merge, `_hash_password` / `_verify_password` were referenced 5 times but defined nowhere, and the `contextmanager` import was lost — login/register crashed with NameError. Diagnosed with a repo-wide grep, re-implemented both helpers with Argon2id using the same PasswordHasher settings as the seeder. |
| 3 | Designing schedule-stop storage | "Design the schema for storing the ordered stops of each schedule" | **AI got it wrong:** the first design stored stops as a JSONB array, which the grading guide explicitly disallows. Rebuilt as junction tables with 0-based stop_order, composite PK, and FKs; rewrote seeder and five query functions to JOIN through them. |
| 4 | Writing the delay-ripple query | "Write a Cypher query returning all stations within N hops of a delayed station" | **AI got it wrong:** it parameterised the variable-length upper bound (`*1..$hops`), which Cypher does not support — syntax error at runtime. Replaced with a clamped integer interpolation (`max(1, min(int(hops), 10))`) and documented why. |

## Section 6 — Reflection & Trade-offs

1. **Natural VARCHAR keys vs UUID/SERIAL.** We chose human-readable business keys (`MS01`, `NR_SCH01`, `BK-XXXXXX`) because the mock dataset ships with stable, cross-referenced identifiers and readable keys make graded query output easy to inspect. The cost is that natural keys are guessable and enumerable. For production we would move operational tables to opaque UUIDs to prevent enumeration attacks and ID-collision risk across data sources.

2. **Junction table vs JSONB for schedule stops.** Normalising the stops gives us referential integrity and index-backed ordering queries (the `stop_order` self-join), at the price of more complex seeding and one extra JOIN whenever a full route needs to be reassembled. We accepted that trade-off because correctness and query power matter more than seed simplicity here.

3. **Dual-store stations (Postgres + Neo4j).** PostgreSQL is the system of record for transactional data (bookings, payments, credentials); Neo4j holds only the topology used for routing. The cost is that two seeders must be kept in sync — a station added in one store must appear in the other. In production we would designate Postgres as the single source of truth and derive the graph from it via change-data-capture (CDC) rather than a parallel seeder.

4. **Production hardening checklist.** Before this project could ship we would add: parameterised connection pooling; schema migrations managed by a tool such as Alembic or Flyway instead of rebuilding Docker volumes; secret management through a vault rather than a committed `.env`; HNSW parameter tuning together with explicit embedding-dimension governance; and monitoring of transaction retry/rollback rates to catch contention early.

## Section 7 — Bonus Extension Motivation, Changes, Example Queries, and Testing Evidence

### 7.1 Motivation

The bonus extension adds a database analytics tool to the relational database layer. The goal is to provide meaningful operational insight beyond the existing booking and schedule queries, making this extension eligible for the full database bonus marks.

This extension is intentionally database-focused, because database extensions are eligible for the full +15 marks. It adds a query that aggregates booking activity, revenue, and refund totals, which is a useful new capability for an analytics dashboard or operational reporting interface.

### 7.2 Changes Made

**Database Layer:**
- Added `query_booking_revenue_summary(start_date: Optional[str] = None, end_date: Optional[str] = None) -> dict`
  - Returns operational metrics: total bookings, active/cancelled counts, revenue, refunds
  - Queries `bookings` table with optional date range filtering

- Added `query_trip_history(user_email: str, limit: int = 20) -> dict`
  - Returns user's trip history with full booking details
  - Queries `bookings` joined with `national_rail_schedules`
  - Includes station names, dates, fares, refund status, and booking IDs

- Added `query_route_visualization(origin_station: str, destination_station: str, route_type: str) -> dict`
  - Returns detailed route information for route planning
  - Queries `national_rail_schedules` or `metro_schedules` with stop details
  - Includes stops in order, travel times, and fare classes

**UI Layer:**
- Added analytics dashboard panel in sidebar:
  - Date range inputs and refresh button
  - Displays total/active/cancelled bookings, revenue, and refunds

- Added trip history panel in sidebar:
  - "Load my trip history" button (available only when logged in)
  - Displays user's trips as a markdown table with columns:
    - Booking ID, Origin, Destination, Date, Fare Class, Amount, Status
  - Surfaces personal trip data that chat interface cannot show

- Added route visualizer panel in sidebar:
  - Origin and destination station ID inputs
  - Route type dropdown (national_rail or metro)
  - "Visualize Route" button
  - Displays route information including:
    - Line number, service type, direction, schedule ID
    - First/last train times and frequency
    - All stops with travel times from origin
    - Fare classes with pricing details

- Added `render_trip_history(user_email: str) -> str`
  - Formats trip history into a markdown table for display

- Added `render_route_visualization(origin: str, destination: str, route_type: str) -> str`
  - Formats route details into a markdown display with visual hierarchy

- Added documentation files:
  - `TASK6.md` listing all modified files and functions for the bonus requirement.
  - `Team45_DESIGN_DOC.md` containing Section 7 for motivation, changes, example queries, and testing evidence.

### 7.3 Example Queries

**Analytics (Operational):**
- `query_booking_revenue_summary()`
  - Returns overall booking revenue metrics for all dates.
- `query_booking_revenue_summary(start_date='2026-04-01', end_date='2026-04-30')`
  - Returns booking and revenue summary metrics for April 2026.

**Trip History (Personal):**
- `query_trip_history(user_email='alice@example.com')`
  - Returns Alice's 10 most recent trips with full details (booking ID, route, date, fare, status)
- `query_trip_history(user_email='bob@example.com', limit=5)`
  - Returns Bob's 5 most recent trips

**Route Visualization (Route Planning):**
- `query_route_visualization('NR01', 'NR05', 'national_rail')`
  - Returns all national rail routes between station NR01 and NR05
  - Shows stops in order with travel times and fare classes
- `query_route_visualization('MS01', 'MS10', 'metro')`
  - Returns all metro routes between station MS01 and MS10

The returned data can be used directly by dashboards or by the AI agent to support questions such as:

**Operational questions:**
- "How much revenue did national rail bookings generate last month?"
- "What is the total refund payout for cancelled bookings in April?"
- "How many active bookings exist in the system today?"

**Personal questions (after login):**
- "Show me my recent trips"
- "When was my last booking?"
- "How much have I spent on rail bookings?"

**Route planning questions:**
- "What routes are available from NR01 to NR05?"
- "Show me metro routes to station MS10"
- "What are the fare classes for the NR01-NR05 route?"

### 7.4 Testing Evidence

Verification steps performed during development:

1. Confirmed the new function is syntactically valid and integrated into the existing relational queries module.
2. Verified that `databases/relational/queries.py` contains the required `# TASK 6 EXTENSION:` marker near the top.
3. Confirmed the new function can run against the existing `bookings` table using the current PostgreSQL schema and returns correct aggregated metrics.
4. Added root-level `TASK6.md` to satisfy the bonus requirement for a file list and modified-file tracking.

The new query is intentionally simple and compatible with the current schema, which minimizes risk while adding a meaningful new database capability.
