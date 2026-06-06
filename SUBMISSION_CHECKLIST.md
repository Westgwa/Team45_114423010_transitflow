# 提交前檢查清單(Team 45)

> 隊長:郭明儒(114423010)。`[x]` = 已完成。

## GitHub Repo

- [ ] Repo 改名為 **`Team45_114423010_transitflow`**
      — GitHub → Settings → General → Repository name
- [ ] Repo 設為 **public** — Settings → General → Danger Zone → Change visibility
- [ ] 本地 commits push 到 GitHub(`git push`)
- [ ] Repo 連結交到 EEClass

## 檔名與占位符

- [x] `Team45_DESIGN_DOC.md` — 已改名、Team 45 與成員資訊已填
- [x] `Team45_WORK_ALLOCATION.md` — 已改名、分工 / 貢獻百分比(60/20/20,加總 100%)已填
- [ ] 補填分工表中卓少筠、林楷崋的 **GitHub Username 與 Email**(僅剩這 4 格 `<待填>`)
- [ ] 三位成員在分工表 Team Declaration 簽名(`<請簽名>`)

## 個人提交(每人各自)

- [ ] 郭明儒:`Team45_114423010_PEER_REVIEW.md`(confidential,個別交)
- [ ] 卓少筠:`Team45_113403005_PEER_REVIEW.md`
- [ ] 林楷崋:`Team45_113403018_PEER_REVIEW.md`

## 向量資料庫(policy_documents)

- [x] Ollama 已可用(NVIDIA 驅動已更新至 595.97,GPU 模式正常)
- [x] `seed_vectors.py` 已跑過,policy_documents = 13 筆
- [ ] **Demo / Live Testing 當天**重新確認 Ollama 在跑(桌面程式會隨開機自動啟動;丟一個問題暖機約 30 秒)

## 最後驗證(交件前在乾淨環境跑一次)

> 2026-06-07 已全程驗證過一輪:模擬 Live Testing **38/38 PASS**。交件前建議再跑一次確認。

- [ ] `docker compose down -v; docker compose up -d`
- [ ] `python skeleton/seed_postgres.py`(跑兩次,第二次不能報錯)
- [ ] `python skeleton/seed_vectors.py`(需要 Ollama 在跑)
- [ ] `python skeleton/seed_neo4j.py`(跑兩次)
- [ ] `python scripts/live_test_simulation.py` → 38/38 PASS
- [ ] `python skeleton/ui.py` 啟動,聊天 + 登入 + Task 6 面板都正常
