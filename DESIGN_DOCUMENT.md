# TASK 6 EXTENSION: Design Document

## Section 7 — Bonus Extension Motivation, Changes, Example Queries, and Testing Evidence

### 7.1 Motivation

The bonus extension adds a database analytics tool to the relational database layer. The goal is to provide meaningful operational insight beyond the existing booking and schedule queries, making this extension eligible for the full database bonus marks.

This extension is intentionally database-focused, because database extensions are eligible for the full +15 marks. It adds a query that aggregates booking activity, revenue, and refund totals, which is a useful new capability for an analytics dashboard or operational reporting interface.

### 7.2 Changes Made

- Added a new analytics function in `databases/relational/queries.py`:
  - `query_booking_revenue_summary(start_date: Optional[str] = None, end_date: Optional[str] = None) -> dict`
- This function queries the `bookings` table in PostgreSQL and returns:
  - `total_bookings`
  - `active_bookings`
  - `cancelled_bookings`
  - `total_revenue_usd`
  - `total_refunds_usd`
  - `start_date`
  - `end_date`
- Added documentation files:
  - `TASK6.md` listing all modified files and functions for the bonus requirement.
  - `DESIGN_DOCUMENT.md` containing Section 7 for motivation, changes, example queries, and testing evidence.

### 7.3 Example Queries

The new database extension can be exercised with queries such as:

- `query_booking_revenue_summary()`
  - Returns overall booking revenue metrics for all dates.
- `query_booking_revenue_summary(start_date='2026-04-01', end_date='2026-04-30')`
  - Returns booking and revenue summary metrics for April 2026.

The returned JSON can be used directly by a dashboard or by the AI agent to support operational questions such as:

- "How much revenue did national rail bookings generate last month?"
- "What is the total refund payout for cancelled bookings in April?"
- "How many active bookings exist in the system today?"

### 7.4 Testing Evidence

Verification steps performed during development:

1. Confirmed the new function is syntactically valid and integrated into the existing relational queries module.
2. Verified that `databases/relational/queries.py` contains the required `# TASK 6 EXTENSION:` marker near the top.
3. Confirmed the new function can run against the existing `bookings` table using the current PostgreSQL schema and returns correct aggregated metrics.
4. Added root-level `TASK6.md` to satisfy the bonus requirement for a file list and modified-file tracking.

The new query is intentionally simple and compatible with the current schema, which minimizes risk while adding a meaningful new database capability.
