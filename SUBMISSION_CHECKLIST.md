# 提交前檢查清單(需要團隊手動操作)

> 這些項目需要真實團隊資訊或 GitHub 權限,無法由程式自動完成。

## GitHub Repo

- [ ] Repo 改名為 `Team<Id>_<隊長學號>_transitflow`(例:`Team01_113403999_transitflow`)
      — GitHub → Settings → General → Repository name
- [ ] Repo 設為 **public** — Settings → General → Danger Zone → Change visibility
- [ ] Repo 連結交到 EEClass

## 檔名與占位符

- [ ] `TeamXX_DESIGN_DOC.md` → 改名 `Team<Id>_DESIGN_DOC.md`,並把文件內的 XX 與 TODO 區塊清掉
- [ ] `TeamXX_WORK_ALLOCATION.md` → 改名 `Team<Id>_WORK_ALLOCATION.md`,填完所有 `<待填>`
- [ ] 確認貢獻百分比加總 = 100%

## 個人提交(每人各自)

- [ ] `Team<Id>_<StudentID>_PEER_REVIEW.md`(confidential,個別交)

## 向量資料庫(policy_documents)

- [ ] Live Testing 前確保 embedding provider 可用(本機啟動 Ollama 並 `ollama pull nomic-embed-text`,或在 `.env` 填入 GEMINI_API_KEY 並把 LLM_PROVIDER 設為 gemini)
- [ ] 跑 `python skeleton/seed_vectors.py`,確認 `SELECT COUNT(*) FROM policy_documents` > 0

## 最後驗證(交件前在乾淨環境跑一次)

- [ ] `docker compose down -v; docker compose up -d`
- [ ] `python skeleton/seed_postgres.py`(跑兩次,第二次不能報錯)
- [ ] `python skeleton/seed_vectors.py`(需要 embedding provider)
- [ ] `python skeleton/seed_neo4j.py`(跑兩次)
- [ ] `python scripts/live_test_simulation.py` → 全部 PASS
- [ ] `python skeleton/ui.py` 啟動,聊天 + 登入 + Task 6 面板都正常
