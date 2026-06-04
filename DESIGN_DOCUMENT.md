# TASK 6 EXTENSION: Design Document

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
  - `DESIGN_DOCUMENT.md` containing Section 7 for motivation, changes, example queries, and testing evidence.

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
