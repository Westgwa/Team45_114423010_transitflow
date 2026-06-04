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
  - Added a route visualizer panel with origin/destination/route-type inputs.
  - Added `render_route_visualization()` function to format route information with stops, times, and fares.
  - Purpose: surface new operational analytics, personal trip history, and route planning data that the chat interface does not currently show.

- `databases/relational/queries.py`
  - Added `query_trip_history(user_email, limit=20)` function.
  - Added `query_route_visualization(origin_station, destination_station, route_type)` function.
  - Database tables referenced: `bookings`, `national_rail_schedules`, `metro_schedules`.
  - Purpose: retrieve detailed trip history and route information for logged-in users and route planning.

- `DESIGN_DOCUMENT.md`
  - Added Section 7 describing the motivation, changes, example queries, and testing evidence for this extension.

- `TASK6.md`
  - Added as the required root-level task list file.
