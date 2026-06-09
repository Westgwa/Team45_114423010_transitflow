# TransitFlow — Live Test Guide

An LLM + RAG transit assistant backed by three databases: **PostgreSQL** (relational + pgvector) and **Neo4j** (graph routing). Built for IM2002 Database Management.

This guide is a runbook for the live test session: how to start the stack, seed the databases, launch the app, verify correctness, and exercise the bonus features.

## Architecture

| Database | Role | Key design |
|----------|------|-----------|
| **PostgreSQL** | System of record: stations, schedules, seats, users, bookings, payments, feedback | Schedule stops normalised into junction tables (`*_schedule_stops`, composite PK `(schedule_id, stop_order)`); Argon2id password hashing; booking + payment written in one atomic transaction |
| **pgvector** | RAG store: 13 policy documents as 768-d embeddings | Cosine similarity via HNSW index; top-3 retrieval above 0.5 threshold feeds the LLM prompt |
| **Neo4j** | Network topology for routing | `Station` nodes (`MetroStation` / `NationalRailStation`); `METRO_LINK` / `RAIL_LINK` / `INTERCHANGE_TO` relationships weighted by `travel_time_min`, `fare_standard`, `fare_first`; APOC Dijkstra for shortest/cheapest paths |

Full design rationale: [`Team45_DESIGN_DOC.md`](Team45_DESIGN_DOC.md)

## Prerequisites

- **Docker Desktop** (PostgreSQL 16 + pgvector, Neo4j 5 + APOC, pgAdmin)
- **Python 3.10+** — `pip install -r requirements.txt`
- **LLM provider** (for RAG seeding and chat), either:
  - **Ollama** (default): install, then `ollama pull nomic-embed-text` and `ollama pull llama3.2:1b`
  - **Gemini**: set `GEMINI_API_KEY` and `LLM_PROVIDER=gemini` in `.env`

## Quickstart

```bash
# 1. Configure environment (defaults already match docker-compose port mappings)
cp .env.example .env

# 2. Start the database stack (Postgres on host port 5433, Neo4j bolt on 7688)
docker compose up -d

# 3. Seed all three databases (each script is idempotent — safe to re-run)
python skeleton/seed_postgres.py
python skeleton/seed_neo4j.py
python skeleton/seed_vectors.py    # requires the LLM provider to be running

# 4. Launch the app (FastAPI + Gradio, with the live-notification WebSocket)
python skeleton/ui.py              # http://127.0.0.1:7860
```

> **Note:** The entry point launches a FastAPI/uvicorn server that hosts the Gradio UI at `/`
> and a WebSocket endpoint at `/ws/notifications`. Open the UI in a browser; the "Live
> Notifications" panel connects automatically.

### Verify the installation

```bash
python scripts/live_test_simulation.py
```

Runs 42 checks mirroring the live-testing rubric (seeding integrity, all PostgreSQL query functions B1–B10, all Neo4j routing functions C1–C6). Expected output: `42 passed, 0 failed`.

### Database browsers

| Tool | URL | Credentials |
|------|-----|-------------|
| pgAdmin | http://localhost:5051 | admin@admin.com / admin (server: `postgres:5432`, user/pass/db `transitflow`) |
| Neo4j Browser | http://localhost:7475 | neo4j / transitflow |

## Exercising the core features (live test)

In the Gradio chat, try prompts such as:

- **Availability & fares:** "What national rail services run from NR01 to NR05 on 2026-04-15?" · "How much is a metro ticket from MS01 to MS10?"
- **Routing (Neo4j):** "What is the fastest route from NR01 to NR12?" · "What is the cheapest first-class route?" · "Which stations are affected if NR05 is delayed?"
- **Policy (RAG):** "What is the refund policy for a cancelled first-class ticket?"
- **Booking (after login):** register / log in, then "Book me a seat on NR_1001 for 2026-04-15" and "Cancel booking <id>".

## Exercising the Task 6 bonus features (live test)

All bonus features are reachable from the sidebar of the running app:

- **Booking analytics dashboard** — enter a date range and click *Refresh booking analytics* to see total/active/cancelled bookings, revenue, and refunds.
- **CSV export** — click *Export analytics CSV*, and (after login) *Export trip history CSV*, to download timestamped reports.
- **Trip history** — after logging in, click *Load my trip history* to list your bookings.
- **Route visualizer** — enter origin/destination station IDs and a route type, then click *Visualize Route* to see the route as text **and** an interactive node graph.
- **Live notifications** — make or cancel a booking through the chat; a notification appears in real time in the *Live Notifications* panel, pushed over `/ws/notifications`.

See [`TASK6.md`](TASK6.md) for the full list of modified files/functions, and [`Team45_DESIGN_DOC.md`](Team45_DESIGN_DOC.md) Section 7 for motivation, example queries, and testing evidence.

## Repository structure

```
databases/
  relational/schema.sql      # 14 tables: stations, schedules, junction stops, users,
                             # credentials, seats, bookings, payments, trips, feedback, policy docs
  relational/queries.py      # availability / fares / seats / bookings / auth / vector search
  graph/queries.py           # shortest, cheapest (fare-class aware), alternative,
                             # interchange, delay-ripple, connections
skeleton/
  seed_postgres.py           # idempotent relational seeding (ON CONFLICT DO NOTHING)
  seed_neo4j.py              # idempotent graph seeding (MERGE)
  seed_vectors.py            # embeds policy documents into pgvector
  agent.py / ui.py           # tool-calling LLM agent + Gradio interface
  notifications.py / server.py  # WebSocket notifications + FastAPI/uvicorn server (Task 6)
scripts/
  live_test_simulation.py    # 42-check grading simulation
train-mock-data/             # course-provided dataset (JSON)
docs/er_diagram.*            # rendered ER diagram (mermaid source + png/svg)
Team45_DESIGN_DOC.md         # design document, Sections 1–7
TASK6.md                     # bonus extension: modified files and functions
```
