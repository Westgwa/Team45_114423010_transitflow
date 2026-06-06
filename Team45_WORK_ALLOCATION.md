# Work Allocation Report — Team 45

> **Instructions:** Complete this document as a team before or alongside your final submission.
> Submit one copy per team via EEClass. This document is shared with all markers.

---

## 1. Team Members

| Full Name | Student ID | GitHub Username | Email |
|-----------|-----------|----------------|-------|
| 郭明儒 *(Team Leader)* | 114423010 | Westgwa | kobp90927@gmail.com |
| 卓少筠 | 113403005 | carol941228 | newcarol941228@gmail.com |
| 林楷崋 | 113403018 | tsczta | kaihualin94@gmail.com |

---

## 2. Task Ownership

### Code Repository

| Task | Primary Owner | Supporting Member(s) | Notes |
|------|--------------|---------------------|-------|
| **Task 1** — Relational schema design (`schema.sql`) | 郭明儒 | 卓少筠 | 卓少筠 owned the schema-hardening pass: added the missing foreign keys on `bookings` / `payments` (preventing ghost bookings and corrupted records) and the CHECK data-validation constraints on status-type columns (`status`, `payment_status`, `fare_class`); pushed to GitHub independently. |
| **Task 2a** — Core availability & fare queries (`query_national_rail_availability`, `query_metro_schedules`, `query_national_rail_fare`, `query_metro_fare`) | 郭明儒 | — | |
| **Task 2b** — Seat & user queries (`query_available_seats`, `query_user_profile`, `query_user_bookings`, `query_payment_info`) | 郭明儒 | — | |
| **Task 2c** — Write operations (`execute_booking`, `execute_cancellation`) | 郭明儒 | — | Booking + payment inserted in a single atomic transaction. |
| **Task 2d** — Authentication queries (`login_user`, `register_user`, `get_user_secret_question`, `verify_secret_answer`, `update_password`) | 郭明儒 | — | Argon2id password hashing. |
| **Task 3** — PostgreSQL seeding (`seed_postgres.py`) | 郭明儒 | — | Idempotent via `ON CONFLICT DO NOTHING`. |
| **Task 4** — Neo4j graph design & seeding (`seed_neo4j.py`, `seed.cypher`) | 郭明儒 | — | `METRO_LINK` / `RAIL_LINK` / `INTERCHANGE_TO` with fare-class weights. |
| **Task 5** — Neo4j query functions (`graph/queries.py`) | 郭明儒 | — | |
| **Task 6** *(if attempted)* — Optional extension | 林楷崋 | 郭明儒、卓少筠 | Team effort led by 林楷崋: English annotation across code/database content (branch `english-annotation`) and system/database optimisation (branch `bonus-items`) — query-efficiency improvements, code cleanup, and table-design adjustments. |

### Design Document

| Section | Primary Author | Supporting Member(s) | Notes |
|---------|--------------|---------------------|-------|
| Section 1 — ER Diagram | 郭明儒 | — | |
| Section 2 — Normalisation Justification | 郭明儒 | 卓少筠 | FK / CHECK constraint rationale contributed by 卓少筠. |
| Section 3 — Graph Database Design Rationale | 郭明儒 | — | |
| Section 4 — Vector / RAG Design | 郭明儒 | — | |
| Section 5 — AI Tool Usage Evidence | 郭明儒 | — | |
| Section 6 — Reflection & Trade-offs | 郭明儒 | — | |
| Section 7 — Optional Extension *(if applicable)* | 林楷崋 | 郭明儒 | Documents the Task 6 bonus work. |

---

## 3. Estimated Contribution Percentages

Based on the task allocation above, what percentage of total team effort do you estimate each member contributed?
All members must sum to 100%.

| Member | Estimated % | Brief justification |
|--------|-----------|---------------------|
| 郭明儒 | % | |
| 卓少筠 | % | |
| 林楷崋 | % | |
| **Total** | **100%** | |

---

## 4. Mid-Project Changes

If any tasks were reassigned or the original plan changed significantly, document it here.

No changes.

| Change | Original plan | Revised plan | Reason |
|--------|--------------|-------------|--------|
| — | — | — | — |

---

## 5. Team Declaration

We confirm that this work allocation accurately reflects how responsibilities were divided within our team.

| Name | Signature / Typed name | Date |
|------|----------------------|------|
| 郭明儒 | | |
| 卓少筠 | | |
| 林楷崋 | | |
