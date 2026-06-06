# 📊 股市新聞自動整理機器人

每日自動在五個時段抓取美股、台股、投信投顧最新資訊，透過 Claude AI 整理重點並儲存到 Firebase。

## 執行時段

| 時段 | 台灣時間 | UTC cron | 重點內容 |
|------|---------|----------|---------|
| 早盤前 | 06:00 | `0 22 * * *` | 前日美股收盤、亞洲早盤、隔夜消息 |
| 開盤前 | 09:00 | `0 1 * * *`  | 台股展望、外資期貨、法人動向 |
| 午盤  | 12:00 | `0 4 * * *`  | 上午盤勢回顧、強弱族群 |
| 收盤  | 15:00 | `0 7 * * *`  | 台股總結、明日預判 |
| 美股前 | 21:00 | `0 13 * * *` | 美股預市、Fed、重要財報 |

---

## 🚀 設定步驟

### 1. Firebase 設定

1. 前往 [Firebase Console](https://console.firebase.google.com/)
2. 建立新專案（例如：`stock-news-bot`）
3. 啟用 **Firestore Database**（選 Production mode）
4. 前往「專案設定」→「服務帳戶」→「產生新的私密金鑰」
5. 下載 JSON 金鑰檔案，後續會用到

**Firestore 安全規則**（貼到 Firebase Console → Firestore → 規則）：

```
rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {
    // 僅允許服務帳戶寫入，前端只讀
    match /stock_summaries/{doc} {
      allow read: if true;
      allow write: if false;
    }
    match /latest/{doc} {
      allow read: if true;
      allow write: if false;
    }
  }
}
```

### 2. GitHub Secrets 設定

在 GitHub repo → Settings → Secrets and variables → Actions → New repository secret：

| Secret 名稱 | 說明 |
|------------|------|
| `ANTHROPIC_API_KEY` | 從 [console.anthropic.com](https://console.anthropic.com) 取得 |
| `FIREBASE_SERVICE_ACCOUNT_KEY` | 把整個 JSON 金鑰檔案內容貼入（一行） |

**FIREBASE_SERVICE_ACCOUNT_KEY 格式**（把下載的 JSON 壓成一行）：
```
{"type":"service_account","project_id":"你的專案ID","private_key_id":"...","private_key":"-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n","client_email":"...","client_id":"...","auth_uri":"...","token_uri":"...","auth_provider_x509_cert_url":"...","client_x509_cert_url":"..."}
```

### 3. 推送到 GitHub

```bash
git init
git add .
git commit -m "feat: 股市新聞自動整理機器人"
git remote add origin https://github.com/jacky0530-spec/stock-news-bot.git
git push -u origin main
```

推送後 GitHub Actions 會自動依排程執行。

---

## 🧪 手動測試

在 GitHub Actions 頁面 → 選「股市新聞自動整理」→「Run workflow」

可在 `slot_override` 欄位輸入指定時段（`am6` / `am9` / `pm12` / `pm15` / `pm21`）

或本機測試：
```bash
# 安裝依賴
pip install -r requirements.txt

# 設定環境變數
export ANTHROPIC_API_KEY="sk-ant-..."
export FIREBASE_SERVICE_ACCOUNT_KEY='{"type":"service_account",...}'
export SLOT_OVERRIDE="am9"  # 可選，強制指定時段

# 執行
python scripts/fetch_and_summarize.py
```

---

## 📁 Firestore 資料結構

### `stock_summaries/{YYYY-MM-DD_slot}` — 每次執行的完整記錄

```json
{
  "slot_key": "am9",
  "slot_label": "開盤前（09:00）",
  "date": "2026-06-06",
  "timestamp": "2026-06-06T09:00:12+08:00",
  "summary_us": "【美股重點】...",
  "summary_tw": "【台股重點】...",
  "summary_fi": "【投信投顧動態】...",
  "summary_conclusion": "整體結論...",
  "news_us": [...],
  "news_tw": [...],
  "tokens_input": 850,
  "tokens_output": 620,
  "status": "success"
}
```

### `latest/summary` — 最新摘要快取（前端即時讀取）

```json
{
  "last_updated": "2026-06-06T09:00:12+08:00",
  "last_slot": "am9",
  "summary_us": "...",
  "summary_tw": "...",
  "summary_fi": "...",
  "conclusion": "..."
}
```

---

## 💰 費用估算（每月）

| 項目 | 用量 | 費用 |
|------|------|------|
| Claude API (claude-opus-4-5) | 5次/天 × 30天 × ~1500 tokens | 約 $3-5 USD |
| Firebase Firestore | 150次寫入/月 | 免費方案內 |
| GitHub Actions | 150分鐘/月 | 免費方案內 |
| **合計** | | **約 $3-5 USD/月** |

---

## 🔧 常見問題

**Q：為什麼某些 RSS 抓不到？**
部分財經網站有防爬機制，可在 `NEWS_FEEDS` 中更換其他來源。

**Q：如何新增 LINE 通知？**
在 `fetch_and_summarize.py` 的 `main()` 最後加入：
```python
import requests
requests.post("https://notify-api.line.me/api/notify",
    headers={"Authorization": f"Bearer {os.environ['LINE_TOKEN']}"},
    data={"message": summary["sections"].get("整體結論", "")})
```
並在 GitHub Secrets 新增 `LINE_TOKEN`。

**Q：如何新增 Email 通知？**
可整合 SendGrid 或 Resend API，在執行完成後寄送 HTML 格式的摘要報告。
