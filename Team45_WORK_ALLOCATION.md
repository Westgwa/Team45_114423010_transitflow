# TransitFlow — Work Allocation Report (Team 45)

## 1. Team Members

| Full Name | Student ID | GitHub Username | Email |
|-----------|------------|-----------------|-------|
| 郭明儒 (Team Leader) | 114423010 | Westgwa | asd0804445@gmail.com |
| 卓少筠 | 113403005 | carol941228 | newcarol941228@gmail.com |
| 林楷崋 | 113403018 | tsczta | kaihualin94@gmail.com |

## 2. Task Ownership

### Code Tasks

| Task | Scope | Primary Owner | Supporting Members | Notes |
|------|-------|---------------|--------------------|-------|
| Task 1 | `databases/relational/schema.sql` | 郭明儒 | 卓少筠 | 卓少筠 owned the schema-hardening pass: added the missing foreign keys on `bookings` / `payments` (preventing ghost bookings and corrupted records) and the CHECK data-validation constraints on status-type columns (e.g. `status`, `payment_status`, `fare_class`); pushed to GitHub independently. |
| Task 2a | availability & fare queries | 郭明儒 | — | |
| Task 2b | seat & user queries | 郭明儒 | — | |
| Task 2c | booking / cancellation | 郭明儒 | — | |
| Task 2d | authentication | 郭明儒 | — | |
| Task 3 | `skeleton/seed_postgres.py` | 郭明儒 | — | |
| Task 4 | Neo4j graph design & seeding | 郭明儒 | — | |
| Task 5 | `databases/graph/queries.py` | 郭明儒 | — | |
| Task 6 | optional extension (bonus) | 林楷崋 | 郭明儒、卓少筠 | Team effort led by 林楷崋: English annotation across code/database content (branch `english-annotation`) and system/database optimisation (branch `bonus-items`) — query-efficiency improvements, code cleanup, and table-design adjustments. |

### Documentation Tasks

| Section | Content | Primary Owner | Supporting Members | Notes |
|---------|---------|---------------|--------------------|-------|
| Section 1 | ER Diagram | 郭明儒 | — | |
| Section 2 | Normalisation | 郭明儒 | 卓少筠 | FK / CHECK constraint rationale contributed by 卓少筠. |
| Section 3 | Graph Database | 郭明儒 | — | |
| Section 4 | Vector / RAG | 郭明儒 | — | |
| Section 5 | AI Tool Usage | 郭明儒 | — | |
| Section 6 | Reflection | 郭明儒 | — | |
| Section 7 | Optional Extension | 林楷崋 | 郭明儒 | Documents the Task 6 bonus work. |

## 3. Estimated Contribution Percentages

> Percentages must total 100%. Each member needs a short justification.

| Member | Contribution % | Justification |
|--------|----------------|---------------|
| 郭明儒 | 60% | Team leader. Designed and implemented the core relational schema, all PostgreSQL query functions (availability, fares, seats, users, booking/cancellation, authentication), data seeding, the Neo4j graph design and all routing queries, the RAG/vector pipeline, integration testing, and design-document Sections 1–6. |
| 卓少筠 | 20% | Owned the `schema.sql` hardening pass: completed the missing foreign-key constraints on `bookings` / `payments` to guarantee referential integrity, and added CHECK data-validation constraints so status-type columns only accept legal values; delivered and pushed the changes to GitHub. |
| 林楷崋 | 20% | Led the Task 6 bonus extension: English annotation of code and database content for readability, plus system and database optimisation (query efficiency, code cleanup, table-design adjustments); co-authored Section 7. |

**Total: 100%**

## 4. Mid-Project Changes

No changes.

## 5. Team Declaration

We declare that the above allocation truthfully reflects each member's contribution.

| Name | Signature | Date |
|------|-----------|------|
| 郭明儒 | <請簽名> | 2026-06-07 |
| 卓少筠 | <請簽名> | 2026-06-07 |
| 林楷崋 | <請簽名> | 2026-06-07 |
