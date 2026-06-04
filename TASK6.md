# TASK 6 EXTENSION: Modified File List

This file documents every file modified or added for the bonus extension.

- `databases/relational/queries.py`
  - Added `# TASK 6 EXTENSION:` marker near the top.
  - Added the `query_booking_revenue_summary` function.
  - Database tables referenced: `bookings`.
  - Purpose: add a new analytics database operation for booking revenue, cancellation, and refund metrics.

- `skeleton/ui.py`
  - Added `# TASK 6 EXTENSION:` marker near the top.
  - Added a booking analytics dashboard panel in the sidebar.
  - Added inputs for `start_date` and `end_date`, with a refresh button.
  - Added a trip history panel with "Load my trip history" button.
  - Added `render_trip_history()` function to format user bookings as a markdown table.
  - Purpose: surface new operational analytics and personal trip history data that the chat interface does not currently show.

- `databases/relational/queries.py`
  - Added `query_trip_history(user_email, limit=20)` function.
  - Database table referenced: `bookings`, `national_rail_schedules`.
  - Purpose: retrieve detailed trip history with station names, dates, fares, and refund status for logged-in users.

- `DESIGN_DOCUMENT.md`
  - Added Section 7 describing the motivation, changes, example queries, and testing evidence for this extension.

- `TASK6.md`
  - Added as the required root-level task list file.
