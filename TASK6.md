# TASK 6 EXTENSION: Modified File List

This file documents every file modified or added for the bonus extension.

- `databases/relational/schema.sql`
  - Added `-- TASK 6 EXTENSION:` markers near the top and at section "6b. Booking analytics".
  - Added the `vw_booking_revenue_daily` view: a per-`travel_date` rollup of booking counts, active/cancelled counts, revenue, and refunds over the `bookings` table.
  - Added the `idx_bookings_travel_date` index to support the analytics date-range filter.
  - Database objects added: view `vw_booking_revenue_daily`, index `idx_bookings_travel_date`.
  - Purpose: introduce a dedicated analytics database structure that backs the booking-revenue dashboard.

- `databases/relational/queries.py`
  - Added `# TASK 6 EXTENSION:` marker near the top.
  - Added the `query_booking_revenue_summary` function, which now reads from the `vw_booking_revenue_daily` view (with a fallback to the `bookings` table when the view is absent).
  - Added the `_view_exists` helper used to select the view-or-table source.
  - Database objects referenced: view `vw_booking_revenue_daily`, table `bookings`.
  - Purpose: add a new analytics database operation for booking revenue, cancellation, and refund metrics, served by the new rollup view.

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

- `requirements.txt`
  - Added `fastapi`, `uvicorn`, and `websockets` dependencies.
  - Purpose: support the FastAPI/uvicorn server and the WebSocket real-time notification channel.

- `skeleton/notifications.py`
  - Added as a new file containing the `NotificationManager` class and a shared `notifications` singleton.
  - Manages a set of connected WebSocket clients (thread-safe), exposes a `websocket_endpoint` handler, a `broadcast` coroutine, and a `notify` method that schedules broadcasts onto the server event loop from worker threads.
  - Purpose: push real-time booking and cancellation notifications to all connected browsers.

- `skeleton/server.py`
  - Added as a new file that builds a FastAPI application, mounts the Gradio UI at `/`, and registers the `/ws/notifications` WebSocket endpoint.
  - Captures the running event loop on startup so background notifications can be dispatched, and exposes a `run()` entry point served by uvicorn (default port 7860).
  - Purpose: serve the Gradio UI and the live-notification WebSocket from a single ASGI server.

- `skeleton/agent.py`
  - Added an import of the `notifications` singleton.
  - After a successful `create_booking`, emits a `booking` notification (booking id, schedule id, travel date).
  - After a successful `cancel_booking`, emits a `cancellation` notification (booking id, refund amount).
  - Purpose: trigger real-time notifications when bookings are created or cancelled through the agent.

- `skeleton/agent.py` (true chat end-to-end integration)
  - Imported `query_booking_revenue_summary` and `query_trip_history`.
  - Registered two new agent tools in `TOOLS` and `TOOLS_SCHEMA`: `get_booking_analytics(start_date?, end_date?)` and `get_trip_history()`.
  - Added the matching `_execute_tool` branches (analytics → `query_booking_revenue_summary` via the `vw_booking_revenue_daily` view; trip history → `query_trip_history`, login enforced).
  - Added deterministic keyword fallbacks (revenue / analytics / total bookings / trip history …) so the small local model still routes these questions correctly.
  - Hardened the tool-execution guard to drop empty optional params and skip only on missing *required* params (so all-optional tools like `get_booking_analytics` actually run).
  - Purpose: make the analytics bonus reachable through the full UI chat → Agent → Tool → DB → LLM → UI loop, not only the sidebar panel.

- `databases/relational/queries.py` (RAG seed de-duplication)
  - Added the read-only helper `policy_document_exists(title, source_file)`. The provided scaffold `store_policy_document` / `query_policy_vector_search` are left unmodified.
  - Purpose: let the vector seeder check existence before inserting.

- `skeleton/seed_vectors.py` (idempotent seeding)
  - Skips any document already stored under the same `(title, source_file)` before embedding, so re-running inserts zero duplicate policy documents / embeddings.
  - Forces `stdout` to UTF-8 so the emoji status lines do not crash the seeder on a Windows cp950 console.
  - Database objects referenced: table `policy_documents`. Vector `schema.sql` is intentionally NOT modified (embedding stays `vector(768)`).
  - Purpose: satisfy the "seeding must not produce duplicate data" requirement in the Python layer rather than the schema.

- `skeleton/ui.py` (bonus-items additions on top of the earlier Task 6 work)
  - Added `_write_csv_file()` helper plus `export_booking_analytics()` and `export_trip_history()` to generate downloadable CSV reports.
  - Added "Export analytics CSV" and "Export trip history CSV" buttons with file-download outputs.
  - Added `render_route_visualization_graph()`, which renders the route as an interactive node graph using vis-network, and wired it to the "Visualize Route" button alongside the existing text view.
  - Added a "Live Notifications" panel that opens a WebSocket to `/ws/notifications` and displays incoming booking/cancellation messages in real time.
  - Changed the `__main__` entry point to launch via `skeleton/server.py` (FastAPI + uvicorn) instead of `demo.launch()`.
  - Database tables referenced (indirectly via queries): `bookings`, `national_rail_schedules`, `metro_schedules`.
  - Purpose: add CSV export, graphical route visualization, and real-time notifications to the dashboard.

- `DESIGN_DOCUMENT.md`
  - Added Section 7 describing the motivation, changes, example queries, and testing evidence for this extension.

- `TASK6.md`
  - Added as the required root-level task list file.
