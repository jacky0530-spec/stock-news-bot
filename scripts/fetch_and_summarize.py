#!/usr/bin/env python3
"""
股市新聞自動整理機器人
每日五個時段自動執行：06:00 / 09:00 / 12:00 / 15:00 / 21:00（台灣時間）
"""

import os
import json
import datetime
import hashlib
import urllib.parse
from google import genai
import firebase_admin
from firebase_admin import credentials, firestore
import feedparser
import requests

# ─── 設定 ───────────────────────────────────────────────────────────────────

SLOT_CONFIG = {
    "am6":  {"label": "早盤前（06:00）", "focus": "前日美股收盤、亞洲早盤、隔夜重大消息"},
    "am9":  {"label": "開盤前（09:00）", "focus": "台股今日展望、外資期貨未平倉、法人動向"},
    "pm12": {"label": "午盤（12:00）",   "focus": "上午盤勢回顧、強弱族群、盤中異動個股"},
    "pm15": {"label": "收盤（15:00）",   "focus": "台股收盤總結、主力動向、明日預判"},
    "pm21": {"label": "美股前（21:00）", "focus": "美股預市、Fed動態、重要財報、台積電ADR"},
}

# feedparser 使用的 User-Agent（模擬瀏覽器，提高成功率）
FEED_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; StockNewsBot/1.0; +https://github.com/stockbot)"
}

def google_news_rss(query: str, lang: str = "zh-TW", region: str = "TW") -> str:
    """產生 Google News RSS URL（最穩定的免費新聞源）"""
    q = urllib.parse.quote(query)
    return f"https://news.google.com/rss/search?q={q}&hl={lang}&gl={region}&ceid={region}:{lang}"

# RSS 新聞來源（三層設計：主要 / Google News 備援 / 官方公告）
NEWS_FEEDS = {
    "美股": [
        # ── 主要來源 ──
        "https://feeds.finance.yahoo.com/rss/2.0/headline?s=^GSPC&region=US&lang=en-US",
        "https://www.cnbc.com/id/100003114/device/rss/rss.html",
        "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
        # ── Google News 補強（繁中）──
        google_news_rss("美股 S&P500 NASDAQ"),
        google_news_rss("聯準會 Fed 利率 升降息"),
        google_news_rss("台積電 ADR TSMC 美股"),
        google_news_rss("科技股 AI 輝達 NVIDIA"),
    ],
    "台股": [
        # ── 主要來源 ──
        "https://www.cnyes.com/rss/cat/tw_stock.xml",
        "https://www.moneydj.com/KMDJ/RSS/RSSViewer.aspx?English=index",
        # ── Google News 補強 ──
        google_news_rss("台股 加權指數 今日行情"),
        google_news_rss("台積電 台股 半導體"),
        google_news_rss("外資 投信 買超 賣超 台股"),
        google_news_rss("ETF 00878 00940 台股"),
        # ── 官方公告（最穩定）──
        "https://www.twse.com.tw/rss/zh/",                        # 證交所
        "https://www.tpex.org.tw/web/regular_emerging/rss.php",   # 櫃買中心
    ],
    "投信投顧": [
        # ── Google News（最穩定的投信消息源）──
        google_news_rss("投信 投顧 市場分析 操作建議"),
        google_news_rss("基金 ETF 台灣 申購 配息"),
        google_news_rss("元大 國泰 富邦 群益 投信 基金"),
        google_news_rss("法人 外資 投信 買賣超 台股"),
        # ── 官方來源 ──
        "https://www.twse.com.tw/rss/zh/",
    ],
    "AI產業": [
        # ── AI 核心議題 ──
        google_news_rss("AI 人工智慧 產業 趨勢"),
        google_news_rss("NVIDIA 輝達 AI晶片 GPU"),
        google_news_rss("OpenAI ChatGPT Gemini Claude AI"),
        google_news_rss("AI伺服器 CoWoS 台積電 AI封裝"),
        google_news_rss("AI股 美超微 廣達 緯穎 AI概念股"),
        google_news_rss("機器學習 大語言模型 LLM 生成式AI"),
        # ── 英文來源補強 ──
        google_news_rss("AI artificial intelligence investment", lang="en", region="US"),
        google_news_rss("NVIDIA AMD Intel AI chip earnings", lang="en", region="US"),
    ],
}

# ─── Firebase 初始化 ─────────────────────────────────────────────────────────

def init_firebase():
    firebase_key_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT_KEY")
    if not firebase_key_json:
        raise ValueError("缺少 FIREBASE_SERVICE_ACCOUNT_KEY 環境變數")
    
    key_dict = json.loads(firebase_key_json)
    cred = credentials.Certificate(key_dict)
    
    if not firebase_admin._apps:
        firebase_admin.initialize_app(cred)
    
    return firestore.client()

# ─── 新聞抓取 ────────────────────────────────────────────────────────────────

def fetch_news_from_feeds(category: str, max_per_feed: int = 5) -> list[dict]:
    """從 RSS feeds 抓取新聞（含 User-Agent、重試、來源標記）"""
    articles = []
    feeds = NEWS_FEEDS.get(category, [])
    ok_count = 0

    for feed_url in feeds:
        is_google = "news.google.com" in feed_url
        label = "Google News" if is_google else feed_url.split("/")[2]
        try:
            # feedparser 支援傳入 request_headers
            parsed = feedparser.parse(feed_url, request_headers=FEED_HEADERS)
            entries = parsed.entries[:max_per_feed]
            if not entries:
                print(f"    [空] {label}")
                continue
            for entry in entries:
                # Google News 的標題格式：「新聞標題 - 媒體名稱」，拆開取媒體名
                title = entry.get("title", "")
                source_name = parsed.feed.get("title", label)
                if is_google and " - " in title:
                    parts = title.rsplit(" - ", 1)
                    title = parts[0].strip()
                    source_name = parts[1].strip()

                articles.append({
                    "title": title,
                    "summary": entry.get("summary", entry.get("description", ""))[:300],
                    "link": entry.get("link", ""),
                    "published": entry.get("published", ""),
                    "source": source_name,
                    "feed_type": "google_news" if is_google else "rss",
                })
            ok_count += 1
            print(f"    ✓ {label}：{len(entries)} 筆")
        except Exception as e:
            print(f"    [警告] {label} 失敗：{e}")
    
    print(f"    共 {ok_count}/{len(feeds)} 個來源成功")

    # 去重（以標題前30字 hash）
    seen = set()
    unique = []
    for a in articles:
        h = hashlib.md5(a["title"][:30].encode()).hexdigest()
        if h not in seen:
            seen.add(h)
            unique.append(a)

    # 濾掉標題太短的
    unique = [a for a in unique if len(a["title"]) > 8]
    return unique[:20]  # 每類最多20筆給 AI

def fetch_all_news() -> dict[str, list[dict]]:
    """抓取所有分類新聞"""
    result = {}
    for category in ["美股", "台股", "投信投顧", "AI產業"]:
        print(f"\n  📡 抓取【{category}】新聞...")
        result[category] = fetch_news_from_feeds(category)
        print(f"  → 有效新聞：{len(result[category])} 筆")
    return result

# ─── AI 摘要 ─────────────────────────────────────────────────────────────────

def build_prompt(slot_key: str, news_data: dict, now: datetime.datetime) -> str:
    slot = SLOT_CONFIG[slot_key]
    date_str = now.strftime("%Y/%m/%d %H:%M")

    # 每類取前10筆完整標題與摘要
    news_text = ""
    for category, articles in news_data.items():
        news_text += f"\n\n【{category}原始新聞】"
        for i, a in enumerate(articles[:10], 1):
            summary = a.get("summary", "")[:120]
            news_text += f"\n{i}. {a['title']}"
            if summary:
                news_text += f"\n   {summary}"

    return f"""你是資深財經分析師，擁有20年台美股操盤經驗。現在是 {date_str}，本時段「{slot['label']}」。
本時段關注：{slot['focus']}

以下是最新新聞原始資料，請仔細閱讀並進行深度分析：
{news_text}

═══════════════════════════════════
請用繁體中文輸出以下完整分析報告，每個區塊都要詳細，不得省略或濃縮。
格式嚴格遵守，逐條列出，不要用段落文字帶過。
═══════════════════════════════════

【美股重點】
▍今日大盤氛圍
• 多頭／空頭／盤整：說明主因、關鍵數據（指數漲跌幅、成交量、VIX等）
• 資金流向：說明資金偏好（科技/防禦/能源等）
▍重點新聞逐條分析（每條至少2句，含影響評估）
• 【新聞標題】→ 事件說明：影響方向（↑利多/↓利空/→中性）、受影響個股或板塊
• 【新聞標題】→ 事件說明：影響方向
• ...（列出所有重要新聞）
▍個股深度分析
• 個股名稱（代號）：今日表現原因、技術面位置、後續關注重點、短線方向↑↓→
• 個股名稱（代號）：...
• ...（至少列5檔）
▍操作策略
• 積極操作：具體建議買進方向與標的類型
• 風險提示：需要注意的關鍵風險點

【台股重點】
▍今日大盤氛圍
• 多頭／空頭／盤整：說明主因、加權指數位置、成交量、外資動向
• 類股強弱：今日強勢族群 vs 弱勢族群
▍重點新聞逐條分析（每條至少2句，含影響評估）
• 【新聞標題】→ 事件說明：影響方向（↑利多/↓利空/→中性）、受影響個股或族群
• 【新聞標題】→ 事件說明：影響方向
• ...（列出所有重要新聞）
▍個股與族群深度分析
• 個股／族群名稱：今日表現原因、基本面或題材說明、技術面位置、方向↑↓→
• 個股／族群名稱：...
• ...（至少列5個標的）
▍外資投信籌碼分析
• 外資動向：買超/賣超金額、偏好標的
• 投信動向：買超/賣超金額、偏好標的
▍操作策略
• 積極操作：具體建議
• 風險提示：關鍵風險點

【AI產業議題】
▍AI產業重大動態逐條（每條說明事件、涉及公司、產業鏈影響）
• 【事件】→ 涉及：公司名稱｜影響：說明對上中下游影響｜方向：↑↓→
• 【事件】→ ...
• ...（列出所有AI相關新聞）
▍台灣AI概念股個別分析
• 公司名稱（代號）：受影響原因、在AI產業鏈位置、短線影響方向↑↓→、關注重點
• 公司名稱（代號）：...
• ...（至少列5檔台灣AI概念股）
▍美國AI龍頭動態
• 公司名稱：最新動態、對台灣供應鏈影響
• ...
▍AI產業中長線趨勢觀察
• 趨勢一：說明
• 趨勢二：說明
• 趨勢三：說明

【投信投顧分析】
▍外資法人動向逐條
• 標的名稱：買超/賣超金額、連續天數、可能意圖分析
• ...
▍投信動向逐條
• 標的名稱：買超/賣超金額、連續天數、可能意圖分析
• ...
▍各大投信投顧市場觀點逐條
• 機構名稱：市場看法摘要、建議操作方向
• ...
▍主推商品與ETF分析
• ETF／基金名稱：投資主題、近期績效、適合族群、機構看法
• ...

【整體結論】
• 今日市場主軸：說明最重要的驅動因素
• 最大風險：說明需要警戒的風險
• 短線操作建議：具體方向與注意事項
• 中線展望：未來1-2週市場方向預判"""

# ─── 多 API 自動輪換 ─────────────────────────────────────────────────────────

def parse_sections(full_text: str) -> dict:
    """解析 AI 回傳文字，拆分各區塊"""
    sections = {}
    current_key = None
    current_lines = []
    for line in full_text.split("\n"):
        if line.startswith("【") and line.endswith("】"):
            if current_key:
                sections[current_key] = "\n".join(current_lines).strip()
            current_key = line[1:-1]
            current_lines = []
        elif current_key:
            current_lines.append(line)
    if current_key:
        sections[current_key] = "\n".join(current_lines).strip()
    return sections

def call_claude(prompt: str) -> str:
    """Claude claude-haiku-4-5（最佳繁中財經分析，首選）"""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY 未設定")
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        json={
            "model": "claude-haiku-4-5",
            "max_tokens": 2500,
            "temperature": 0.4,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=60,
    )
    if resp.status_code == 529 or resp.status_code == 429:
        raise RuntimeError(f"429 Claude 額度或速率限制：{resp.text[:200]}")
    if resp.status_code != 200:
        raise RuntimeError(f"Claude API 錯誤：{resp.status_code} {resp.text[:200]}")
    return resp.json()["content"][0]["text"]

def call_gemini(prompt: str) -> str:
    """Gemini 2.0 Flash（免費：每日1500次）"""
    from google import genai as google_genai
    from google.genai import types
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise ValueError("GEMINI_API_KEY 未設定")
    client = google_genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt,
        config=types.GenerateContentConfig(max_output_tokens=2500, temperature=0.4)
    )
    return response.text

def call_groq(prompt: str) -> str:
    """Groq Llama 3（免費：每日14,400次，速度極快）"""
    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        raise ValueError("GROQ_API_KEY 未設定")
    resp = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": "llama-3.3-70b-versatile",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 2500,
            "temperature": 0.4,
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"429 Groq 錯誤：{resp.status_code} {resp.text[:200]}")
    return resp.json()["choices"][0]["message"]["content"]

def call_openrouter(prompt: str) -> str:
    """OpenRouter（免費模型備援）"""
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY 未設定")
    resp = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": "meta-llama/llama-3.1-8b-instruct:free",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 2500,
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"429 OpenRouter 錯誤：{resp.status_code} {resp.text[:200]}")
    return resp.json()["choices"][0]["message"]["content"]

# ── API 輪換順序：Claude 最優先，依序備援 ──
API_PROVIDERS = [
    ("Claude Haiku（最佳品質）", call_claude),
    ("Gemini 2.0 Flash",        call_gemini),
    ("Groq Llama3-70b",         call_groq),
    ("OpenRouter Llama",        call_openrouter),
]

def generate_summary(slot_key: str, news_data: dict, now: datetime.datetime) -> dict:
    """自動輪換 API：Claude → Gemini → Groq → OpenRouter"""
    prompt = build_prompt(slot_key, news_data, now)
    print(f"  prompt 長度：{len(prompt)} 字元")

    last_error = None
    for provider_name, provider_fn in API_PROVIDERS:
        try:
            print(f"  嘗試：{provider_name}...")
            full_text = provider_fn(prompt)
            print(f"  ✅ {provider_name} 成功")
            return {
                "full_text": full_text,
                "sections": parse_sections(full_text),
                "usage": {"input_tokens": 0, "output_tokens": 0},
                "provider": provider_name,
            }
        except ValueError as e:
            print(f"  ⏭️  {provider_name} 跳過（{e}）")
            continue
        except Exception as e:
            err_str = str(e)
            if any(k in err_str for k in ["429", "400", "quota", "RESOURCE_EXHAUSTED", "rate_limit", "exhausted", "overloaded", "credit", "balance", "billing", "insufficient"]):
                print(f"  ⚠️  {provider_name} 額度用盡，切換下一個...")
                last_error = e
                continue
            else:
                raise

    raise RuntimeError(f"所有 API 均失敗或額度用盡。最後錯誤：{last_error}")

# ─── Firebase 存儲 ───────────────────────────────────────────────────────────

def save_to_firestore(db, slot_key: str, now: datetime.datetime, 
                       news_data: dict, summary: dict):
    """儲存結果到 Firestore"""
    date_str = now.strftime("%Y-%m-%d")
    doc_id = f"{date_str}_{slot_key}"
    
    doc_data = {
        "slot_key": slot_key,
        "slot_label": SLOT_CONFIG[slot_key]["label"],
        "date": date_str,
        "timestamp": now.isoformat(),
        "created_at": firestore.SERVER_TIMESTAMP,
        
        # 新聞原始資料
        "news_count": {cat: len(articles) for cat, articles in news_data.items()},
        "news_us": news_data.get("美股", [])[:5],
        "news_tw": news_data.get("台股", [])[:5],
        "news_fi": news_data.get("投信投顧", [])[:5],
        "news_ai": news_data.get("AI產業", [])[:5],

        # AI 摘要
        "summary_full": summary["full_text"],
        "summary_us": summary["sections"].get("美股重點", ""),
        "summary_tw": summary["sections"].get("台股重點", ""),
        "summary_ai": summary["sections"].get("AI產業議題", ""),
        "summary_fi": summary["sections"].get("投信投顧分析", ""),
        "summary_conclusion": summary["sections"].get("整體結論", ""),
        
        # Token 用量
        "tokens_input": summary["usage"]["input_tokens"],
        "tokens_output": summary["usage"]["output_tokens"],
        "status": "success",
    }
    
    # 寫入每日詳細記錄
    db.collection("stock_summaries").document(doc_id).set(doc_data)
    
    # 更新「最新摘要」快取（前端讀取用）
    db.collection("latest").document("summary").set({
        "last_updated": now.isoformat(),
        "last_slot": slot_key,
        "last_slot_label": SLOT_CONFIG[slot_key]["label"],
        "provider": summary.get("provider", ""),
        "summary_us": summary["sections"].get("美股重點", ""),
        "summary_tw": summary["sections"].get("台股重點", ""),
        "summary_ai": summary["sections"].get("AI產業議題", ""),
        "summary_fi": summary["sections"].get("投信投顧分析", ""),
        "conclusion": summary["sections"].get("整體結論", ""),
    })
    
    print(f"  ✅ 已儲存到 Firestore：{doc_id}")
    return doc_id

# ─── 主流程 ──────────────────────────────────────────────────────────────────

def get_slot_key(now: datetime.datetime) -> str:
    """根據執行時間判斷時段"""
    hour = now.hour
    if hour < 7:   return "am6"
    elif hour < 10: return "am9"
    elif hour < 13: return "pm12"
    elif hour < 18: return "pm15"
    else:           return "pm21"

def main():
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
    slot_key = os.environ.get("SLOT_OVERRIDE") or get_slot_key(now)
    slot_info = SLOT_CONFIG[slot_key]

    print(f"{'='*60}")
    print(f"🕐 執行時間：{now.strftime('%Y/%m/%d %H:%M:%S')} (台灣時間)")
    print(f"📌 時段：{slot_info['label']}")
    print(f"{'='*60}")

    # 1. 初始化 Firebase
    print("\n[1/4] 初始化 Firebase...")
    db = init_firebase()
    print("  ✅ Firebase 連線成功")

    # ── 防重複執行：今日此時段已成功則跳過 ──
    date_str = now.strftime("%Y-%m-%d")
    doc_id   = f"{date_str}_{slot_key}"
    existing = db.collection("stock_summaries").document(doc_id).get()
    if existing.exists and existing.to_dict().get("status") == "success":
        force = os.environ.get("FORCE_RUN", "false").lower() == "true"
        if not force:
            print(f"\n⏭️  今日 {slot_info['label']} 已執行完成，跳過（設定 FORCE_RUN=true 可強制重跑）")
            return
        print("  ⚠️  FORCE_RUN=true，強制重新執行")

    # 2. 抓取新聞
    print("\n[2/4] 抓取最新新聞...")
    news_data = fetch_all_news()
    total = sum(len(v) for v in news_data.values())
    print(f"  ✅ 共抓取 {total} 筆新聞")

    # 3. AI 摘要（失敗直接中止，不重試）
    print("\n[3/4] 呼叫 Gemini AI 生成摘要...")
    summary = generate_summary(slot_key, news_data, now)
    print(f"  ✅ 摘要生成完成（input:{summary['usage']['input_tokens']} / output:{summary['usage']['output_tokens']} tokens）")

    # 4. 儲存
    print("\n[4/4] 儲存到 Firestore...")
    doc_id = save_to_firestore(db, slot_key, now, news_data, summary)

    print(f"\n{'='*60}")
    print(f"✅ 完成！文件 ID：{doc_id}")
    print(f"{'='*60}\n")
    print("📋 摘要預覽：")
    print(summary["full_text"][:400] + "...")

if __name__ == "__main__":
    main()
