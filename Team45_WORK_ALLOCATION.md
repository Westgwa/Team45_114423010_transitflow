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
| **Task 1** — Relational schema design (`schema.sql`) | | | |
| **Task 2a** — Core availability & fare queries (`query_national_rail_availability`, `query_metro_schedules`, `query_national_rail_fare`, `query_metro_fare`) | | | |
| **Task 2b** — Seat & user queries (`query_available_seats`, `query_user_profile`, `query_user_bookings`, `query_payment_info`) | | | |
| **Task 2c** — Write operations (`execute_booking`, `execute_cancellation`) | | | |
| **Task 2d** — Authentication queries (`login_user`, `register_user`, `get_user_secret_question`, `verify_secret_answer`, `update_password`) | | | |
| **Task 3** — PostgreSQL seeding (`seed_postgres.py`) | | | |
| **Task 4** — Neo4j graph design & seeding (`seed_neo4j.py`, `seed.cypher`) | | | |
| **Task 5** — Neo4j query functions (`graph/queries.py`) | | | |
| **Task 6** *(if attempted)* — Optional extension | | | |

### Design Document

| Section | Primary Author | Supporting Member(s) | Notes |
|---------|--------------|---------------------|-------|
| Section 1 — ER Diagram | | | |
| Section 2 — Normalisation Justification | | | |
| Section 3 — Graph Database Design Rationale | | | |
| Section 4 — Vector / RAG Design | | | |
| Section 5 — AI Tool Usage Evidence | | | |
| Section 6 — Reflection & Trade-offs | | | |
| Section 7 — Optional Extension *(if applicable)* | | | |

---

## 3. Estimated Contribution Percentages

Based on the task allocation above, what percentage of total team effort do you estimate each member contributed?
All members must sum to 100%.

| Member | Estimated % | Brief justification |
|--------|-----------|---------------------|
| 郭明儒 | 60% | Team leader. Designed and implemented the core relational schema, all PostgreSQL query functions (availability, fares, seats, users, booking/cancellation, authentication), data seeding, the Neo4j graph design and all routing queries, the RAG/vector pipeline, integration testing, and design-document Sections 1–6. |
| 卓少筠 | 20% | Owned the `schema.sql` hardening pass: completed the missing foreign-key constraints on `bookings` / `payments` to guarantee referential integrity, and added CHECK data-validation constraints so status-type columns only accept legal values; delivered and pushed the changes to GitHub. |
| 林楷崋 | 20% | Led the Task 6 bonus extension: English annotation of code and database content for readability, plus system and database optimisation (query efficiency, code cleanup, table-design adjustments); co-authored Section 7. |
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
| 郭明儒 | 郭明儒 | 2026-06-07 |
| 卓少筠 | | |
| 林楷崋 | | |
