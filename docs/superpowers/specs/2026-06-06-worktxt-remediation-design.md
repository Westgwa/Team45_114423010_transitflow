# Design: work.txt 評分要求補完 (TransitFlow Remediation)

**Date:** 2026-06-06
**Status:** Approved
**Source of requirements:** `work.txt` (老師評分指南分析: Static Code 100 / Design Document 100 / Live Testing 100)

## Goal

修復程式碼與評分指南的所有落差,補齊設計文件 Section 1~6 與分工報告模板,並以模擬 Live Testing 流程驗證。團隊專屬資訊 (隊號、學號、成員分工) 以占位符標記,由團隊後續填寫。

## Audit Summary (current state)

| # | 問題 | 位置 | 嚴重度 |
|---|------|------|--------|
| 1 | `_hash_password` / `_verify_password` 被呼叫但全 repo 未定義 (合併 `add-password-security` 時遺失) | `databases/relational/queries.py:204,1203,1255,1261,1369` | Critical (runtime crash) |
| 2 | `@contextmanager` 使用但未 import | `databases/relational/queries.py:61` | Critical (import crash) |
| 3 | Neo4j 轉乘關係用 `INTERCHANGES_WITH`,指南要求 `INTERCHANGE_TO` | `skeleton/seed_neo4j.py:106,112`, `databases/graph/queries.py:272` | Critical (Live C4 直接失敗) |
| 4 | schedule stops 用 JSONB array,指南要求 junction table | `schema.sql:48,54,93,102` + seed + 5 處 query + agent.py | High (Task 1 = 40 分重點) |
| 5 | PostgreSQL 缺 `metro_stations` / `national_rail_stations` 表 (車站只在 Neo4j) | `schema.sql` (無此表) | High (Live Section A 點名檢查) |
| 6 | `TIMESTAMP` 應為 `TIMESTAMPTZ` | `schema.sql` 約 20 處 | High |
| 7 | `query_user_bookings` 回傳 list,規格要求 `{"national_rail": [...], "metro": [...]}` | `queries.py:649` | High (Live B7) |
| 8 | 設計文件只有 Section 7,缺 Section 1~6 | `DESIGN_DOCUMENT.md` | High (Design Doc 100 分) |
| 9 | schema 缺 PK 選擇 (UUID/SERIAL) 理由註解 | `schema.sql` | Medium |
| 10 | 缺分工報告 `Team<Id>_WORK_ALLOCATION.md` | repo 根目錄 | Medium |
| 11 | Repo 名 `DB-FinalProject` 不符 `Team<Id>_<隊長學號>_transitflow` 格式 | GitHub | Medium (需團隊操作) |
| 12 | `get_db_connection` 被 `@contextmanager` 裝飾但用 `return` 而非 `yield`,且連線從未歸還 pool — 所有 `with get_db_connection()` 一執行就 TypeError | `queries.py:61-70` | Critical (runtime crash) |
| 13 | `execute_booking` 完全沒有寫入 `payments` 表 — work.txt B9 要求 booking+payment 同一 transaction | `queries.py:888-1039` | High (Live B9 = 5 分) |
| 14 | Neo4j 連線關係用 `CONNECTS_TO`,指南 Task 4 + Live Section A 要求 `METRO_LINK` / `RAIL_LINK` (三種關係: METRO_LINK, RAIL_LINK, INTERCHANGE_TO) | `seed_neo4j.py:88`, `graph/queries.py` 全部路由查詢 | Critical (Task 4 = 8 分 + Live Section A) |
| 15 | `query_cheapest_route` 無 `fare_class` 參數 — work.txt C2 要求 fare class 影響 cost | `graph/queries.py:121` | High (Live C2 = 7 分) |
| 16 | `query_delay_ripple` 在變長路徑用 `*1..$hops` 參數 — Cypher 不支援,執行即語法錯誤 | `graph/queries.py:325` | Critical (Live C5) |

已符合、不需改動: 密碼欄位設計 (`user_credentials.password_hash`)、seed 冪等性 (`ON CONFLICT DO NOTHING` + Neo4j `MERGE`)、所有 query function 無 `pass`/`NotImplementedError`、FK 皆有 ON DELETE 策略、票價 NUMERIC、Task 6 標記 (`TASK6.md` + `# TASK 6 EXTENSION:`) 齊全。

## Part 1 — 緊急修復 (runtime crash)

### 1a. 密碼 hash 函式 (`databases/relational/queries.py`)

- 新增 import: `from contextlib import contextmanager`、`from argon2 import PasswordHasher, exceptions as argon2_exceptions`
- 模組級 `_ph = PasswordHasher()` — 與 `skeleton/seed_postgres.py:32,45` 同一套設定,確保 seed 出的 hash 可被 `login_user` 驗證
- `_hash_password(plain: str) -> str`: 回傳 argon2id hash 字串
- `_verify_password(stored_hash: str, plain: str) -> tuple[bool, bool]`: 回傳 `(是否正確, 是否需要 rehash)`,對應 `queries.py:1255` 既有解包;驗證失敗 (含 hash 格式錯誤) 回 `(False, False)`,不拋例外

### 1b. Neo4j 關係全面改名 (三種關係類型)

依指南 Task 4 要求的三種關係類型,seed 與 queries 同步改:

- `CONNECTS_TO` (network=metro) → `METRO_LINK`
- `CONNECTS_TO` (network=national_rail,含 NR_ALT) → `RAIL_LINK`
- `INTERCHANGES_WITH` → `INTERCHANGE_TO`
- seed 開頭已有 `MATCH (n) DETACH DELETE n` (seed_neo4j.py:199),舊關係自動清除
- 驗證: 三種關係 count > 0;`CONNECTS_TO` 與 `INTERCHANGES_WITH` count = 0

### 1c. 修復 `get_db_connection` (queries.py:61-70)

- 改為真正的 generator: `yield conn`,正常結束 `conn.commit()`、例外 `conn.rollback()` 後 re-raise、`finally` 歸還 `putconn(conn)`
- 移除 `conn.autocommit = True` — 一個 `with` 區塊 = 一個 transaction,直接滿足 B9「booking+payment 一次 commit」

### 1d. Graph queries 其他修正

- `query_cheapest_route` 新增 `fare_class: str = "standard"` 參數;seed 在關係上存 `fare_standard` / `fare_first` 兩個屬性 (metro 平價,rail first = travel_time × 0.60 vs standard × 0.35),Dijkstra 權重屬性依 fare_class 從白名單選擇
- `query_delay_ripple` 的 `*1..$hops` 改為驗證後的整數內插 (Cypher 不允許參數作為變長上限)

## Part 2 — 車站表 + Junction Table 重構 (Task 1 核心)

### 2a. 新增車站表

資料來源 `train-mock-data/metro_stations.json`、`national_rail_stations.json`:

- `metro_stations(station_id VARCHAR(20) PK, name VARCHAR NOT NULL, + JSON 其餘欄位逐一映射為對應型別欄位)`
- `national_rail_stations(station_id VARCHAR(20) PK, name VARCHAR NOT NULL, + 同上)`
- 最低契約: PK = `station_id`、`name NOT NULL`;JSON 中的純量欄位映射為一般欄位,巢狀/陣列欄位 (若有) 映射為 JSONB 並加註解說明

### 2b. Junction tables 取代 JSONB

```sql
CREATE TABLE metro_schedule_stops (
    schedule_id  VARCHAR(30) NOT NULL REFERENCES metro_schedules(schedule_id) ON DELETE CASCADE,
    station_id   VARCHAR(20) NOT NULL REFERENCES metro_stations(station_id) ON DELETE RESTRICT,
    stop_order   INT NOT NULL,
    travel_time_from_origin_min INT NOT NULL,
    PRIMARY KEY (schedule_id, stop_order),
    UNIQUE (schedule_id, station_id)
);
-- national_rail_schedule_stops 同構,FK 指向 national_rail_schedules / national_rail_stations
```

- 一次取代 `stops_in_order` 與 `travel_time_from_origin_min` 兩個平行 JSONB 欄位
- **刪除** 兩張 schedules 表的這兩個欄位與對應 GIN index (`schema.sql:387-388,401-402`) — 並存會被靜態評分認定仍用 array
- `fare_classes`、`operates_on`、`passed_through_stations` JSONB 保留,於設計文件 Section 2 以「刻意保留的彈性欄位」說明

### 2c. 改寫受影響程式

- `skeleton/seed_postgres.py`: 新增車站表 seed;schedules seed 拆 stops 進 junction table (全部維持 `ON CONFLICT DO NOTHING`)
- `databases/relational/queries.py` 5 處 (`:314,374,471,797,815,924`): 從 `_safe_json` 解析改為 JOIN junction table、`ORDER BY stop_order`
- `skeleton/agent.py:274`: 同步調整 (該處讀 query 回傳的 dict,確認鍵名相容)

## Part 3 — Schema 其他修正

- 全部 `TIMESTAMP` → `TIMESTAMPTZ` (`DEFAULT CURRENT_TIMESTAMP` 保留)
- schema.sql 開頭加註解區塊: 說明 PK 策略 — 業務代碼 VARCHAR 自然鍵 (對應 mock data 的 `MS_SCH01` 等) vs SERIAL/UUID 的取捨
- 補一行刪除策略總述註解 (各 FK 已逐一標 ON DELETE)

## Part 3.5 — Booking / Payment Transaction (B9, B10)

- `execute_booking` 在同一 `with` 區塊內 (= 同一 transaction) 於 bookings INSERT 後新增 payments INSERT (`payment_status='paid'`, amount = 計算出的 fare),回傳值附 payment 資訊
- `execute_cancellation` 在同一 transaction 內將該 booking 的 payments 標記 `payment_status='refunded'` (refund > 0 時)
- 兩者皆依賴 1c 修復後的 commit-on-exit 語義,中途失敗整筆 rollback

## Part 4 — `query_user_bookings` 行為修正

改回傳 `{"national_rail": [...], "metro": [...]}`:

- `national_rail`: 既有 bookings JOIN national_rail_schedules 查詢結果
- `metro`: 查 `metro_trips` 表 (schema.sql:276 已存在)
- 查無 user 時回 `{"national_rail": [], "metro": []}` — 取代現行 `[{"error": ...}]` (評分要求「永遠有兩個 key」且不能 exception)
- 更新呼叫端 `skeleton/agent.py:298` (回傳 dict 直接 `json.dumps`;確認 agent prompt 端對新格式的呈現)

## Part 5 — 文件

### 5a. 設計文件

`git mv DESIGN_DOCUMENT.md TeamXX_DESIGN_DOC.md` (XX 占位,團隊改實際隊號),補 Section 1~6,既有 Section 7 移至最後:

| Section | 分數 | 內容要點 |
|---------|------|----------|
| S1 ER Diagram | 25 | Mermaid erDiagram;全部表 (含新增 4 張);cardinality 標在線上 (`||--o{` 等);每 entity 列 PK、重要 FK、2~3 代表欄位 |
| S2 Normalisation | 20 | junction table 拆分 = 3NF 決策 (消除非原子值與傳遞依賴);argon2id vs MD5/SHA-1 具體理由 (memory-hard、cost factor、key stretching、salt 防 rainbow table);保留的 JSONB 欄位作為刻意反正規化討論 |
| S3 Graph Rationale | 25 | node/relationship/property 劃分;Dijkstra vs SQL recursive CTE 對比;shortest path + delay ripple 兩種查詢具體說明;node identity = `station_id` |
| S4 Vector/RAG | 15 | RAG 五步驟流程 (query→embedding→cosine similarity→retrieve→prompt→LLM);dimension 表 (nomic-embed-text 768 / Gemini 3072);dimension mismatch 風險說明 |
| S5 AI Usage | 10 | 4 個案例,各含 Context/Prompt/Outcome 三欄;至少 2 個「AI 給錯→發現→修正」: INTERCHANGES_WITH 命名、hash 函式合併遺失、JSONB array 不符正規化 (皆為本專案真實事件) |
| S6 Reflection | 5 | ≥2 個設計決策與理由 (junction table、argon2、Neo4j 適用性);production 改進 (connection pooling、migration tool、secret management、index strategy) |

### 5b. 分工報告

新建 `TeamXX_WORK_ALLOCATION.md`: Team Members / Task Ownership (Task 1, 2a~2d, 3~6 + 文件 S1~S7,各任務 Primary Owner + Supporting + Notes) / Contribution % (總和 100%) / Mid-Project Changes / Team Declaration。團隊資訊全用 `<待填>` 占位。

### 5c. 提交檢查清單

新建 `SUBMISSION_CHECKLIST.md`: repo 改名為 `Team<Id>_<隊長學號>_transitflow`、設 public、檔名占位符替換、Peer Review 各自提交等需要團隊手動操作的項目。

## Part 6 — 驗證 (模擬 Live Testing)

新建 `scripts/live_test_simulation.py` (不引入新依賴),逐項輸出 PASS/FAIL:

1. `docker compose up -d` → 等服務健康
2. `seed_postgres.py` 連跑兩次: 第二次無 traceback、各表筆數不變 (冪等)
3. `seed_neo4j.py` 連跑兩次: 同上;`INTERCHANGE_TO` count > 0、`INTERCHANGES_WITH` count = 0
4. Section A 點名表 (`metro_stations`, `national_rail_stations`, `metro_schedules`, `users`, `seat_layouts`, `policy_documents`) `COUNT(*) > 0`
5. B1~B10 逐項: 含 not-found 情境回 `[]`/`None`/`(False, msg)` 不拋例外;`execute_booking` 單一 transaction;重複訂位/重複取消不炸
6. C1~C6 逐項: 回傳 dict 格式 (`path`/`total_time_min` 等);`query_interchange_path` 走 `INTERCHANGE_TO`;`query_alternative_routes` 遵守 `max_routes` 與避站

此腳本本身可作為交付的測試證據。

## Out of Scope

- Repo 改名與設 public (需團隊 GitHub 權限與實際隊號,列入 SUBMISSION_CHECKLIST.md)
- Peer Review 報告 (每人各自撰寫,confidential)
- Task 6 新功能 (現有 Task 6 已完成且標記齊全,不再擴充)
- 團隊真實資訊填寫 (占位符標記)

## Error Handling 原則 (貫穿全部 query)

work.txt 明定: 查無資料回 `[]` / `None` / `(False, message)`,絕不拋例外到呼叫端。所有改動的 query 都遵守此契約。

## Testing 原則

- 不引入 pytest 等新依賴;驗證腳本只用專案既有依賴
- 每個 Part 完成後跑對應驗證,最後跑完整 live_test_simulation
