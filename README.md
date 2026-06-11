# TransitFlow — Team 45

An LLM + RAG transit assistant backed by three databases: **PostgreSQL** (relational + pgvector) and **Neo4j** (graph routing). Built for IM2002 Database Management.

| Member | Student ID | GitHub |
|--------|-----------|--------|
| 郭明儒 *(Team Leader)* | 114423010 | [Westgwa](https://github.com/Westgwa) |
| 卓少筠 | 113403005 | [carol941228](https://github.com/carol941228) |
| 林楷崋 | 113403018 | [tsczta](https://github.com/tsczta) |

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

# 4. Launch the Gradio chat UI
python skeleton/ui.py              # http://127.0.0.1:7860
```

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
scripts/
  live_test_simulation.py    # 42-check grading simulation
train-mock-data/             # course-provided dataset (JSON)
docs/er_diagram.*            # rendered ER diagram (mermaid source + png/svg)
Team45_DESIGN_DOC.md         # design document, Sections 1–7
TASK6.md                     # bonus extension: modified files and functions
```

## Task 6 — Bonus extension

Database-backed analytics added on top of the core spec: booking-revenue summary, per-user trip history, and a route visualiser panel in the UI. Every touched file carries a `# TASK 6 EXTENSION:` marker; see [`TASK6.md`](TASK6.md) for the full file/function list and [`Team45_DESIGN_DOC.md`](Team45_DESIGN_DOC.md) Section 7 for motivation, example queries, and testing evidence.
