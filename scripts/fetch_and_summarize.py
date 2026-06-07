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
    "龍頭個股": [
        # ── 各產業龍頭新聞 ──
        google_news_rss("台積電 聯電 股價 法人"),
        google_news_rss("聯發科 瑞昱 IC設計 股票"),
        google_news_rss("廣達 鴻海 緯創 AI伺服器"),
        google_news_rss("國巨 華新科 被動元件"),
        google_news_rss("台達電 光寶科 電源 散熱"),
        google_news_rss("日月光 封裝測試 半導體"),
        google_news_rss("欣興 南電 景碩 載板"),
        google_news_rss("奇鋐 雙鴻 散熱 AI"),
    ],
}

# ─── 產業龍頭監控名單（依圖片整理）────────────────────────────────────────────

WATCHLIST = {
    "記憶體":      [("南亞科","5347"), ("群聯","8299")],
    "晶圓代工":    [("台積電","2330"), ("聯電","2303")],
    "ASIC客製晶片":[("世芯-KY","3661"), ("創意","3443")],
    "被動元件":    [("國巨","2327"), ("華新科","2492")],
    "老AI":        [("緯創","3231"), ("廣達","2382"), ("鴻海","2317")],
    "矽晶圓":      [("環球晶","6488"), ("台勝科","3532")],
    "散熱":        [("奇鋐","3017"), ("雙鴻","3324")],
    "矽光子":      [("聯亞","3081"), ("光聖","6442")],
    "載板":        [("欣興","3037"), ("南電","8046"), ("景碩","3189")],
    "IC設計":      [("聯發科","2454"), ("瑞昱","2379")],
    "電源供應器":  [("台達電","2308"), ("光寶科","2301")],
    "半導體設備":  [("弘塑","3131"), ("漢唐","2404")],
    "封裝測試":    [("日月光投控","3711"), ("京元電子","2449")],
    "銅箔基板":    [("金像電","2368"), ("台光電","2383")],
}

# 所有個股代號列表（供新聞搜尋用）
ALL_STOCKS = [(name, code) for stocks in WATCHLIST.values() for name, code in stocks]

def fetch_stock_price(code: str, name: str) -> dict:
    """從台灣證交所 API 抓取即時股價與基本資料"""
    result = {"code": code, "name": name, "price": None, "pe": None,
              "pbr": None, "week52_high": None, "week52_low": None, "error": None}
    try:
        # 使用 twse openapi 抓取即時行情
        url = f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=tse_{code}.tw&json=1&delay=0"
        resp = requests.get(url, timeout=8,
                            headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code == 200:
            data = resp.json()
            items = data.get("msgArray", [])
            if items:
                item = items[0]
                price = float(item.get("z", item.get("y", 0)) or 0)
                high  = float(item.get("h", 0) or 0)
                low   = float(item.get("l", 0) or 0)
                result.update({
                    "price": price,
                    "today_high": high,
                    "today_low": low,
                    "open": float(item.get("o", 0) or 0),
                    "prev_close": float(item.get("y", 0) or 0),
                    "volume": item.get("v", "0"),
                })
    except Exception as e:
        result["error"] = str(e)
    return result

def fetch_all_prices() -> dict:
    """抓取所有監控個股股價"""
    prices = {}
    print("  📈 抓取產業龍頭股價...")
    for name, code in ALL_STOCKS:
        p = fetch_stock_price(code, name)
        prices[code] = p
        status = f"${p['price']}" if p['price'] else f"失敗({p['error'][:20] if p['error'] else '無資料'})"
        print(f"    {name}({code})：{status}")
    return prices

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

def parse_pub_time(entry) -> datetime.datetime:
    """解析 RSS 發布時間，轉為 UTC datetime"""
    import email.utils, time
    for field in ("published_parsed", "updated_parsed"):
        t = entry.get(field)
        if t:
            try:
                return datetime.datetime(*t[:6], tzinfo=datetime.timezone.utc)
            except Exception:
                pass
    # 嘗試解析字串格式
    for field in ("published", "updated"):
        s = entry.get(field, "")
        if s:
            try:
                ts = email.utils.parsedate_to_datetime(s)
                return ts.astimezone(datetime.timezone.utc)
            except Exception:
                pass
    return datetime.datetime.now(datetime.timezone.utc)  # 無法解析則視為現在

def fetch_news_from_feeds(category: str, max_per_feed: int = 8,
                          hours_limit: int = 48) -> list[dict]:
    """從 RSS feeds 抓取新聞（只保留近48小時，含 User-Agent、去重）"""
    articles = []
    feeds = NEWS_FEEDS.get(category, [])
    ok_count = 0
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours_limit)

    for feed_url in feeds:
        is_google = "news.google.com" in feed_url
        label = "Google News" if is_google else feed_url.split("/")[2]
        try:
            parsed = feedparser.parse(feed_url, request_headers=FEED_HEADERS)
            entries = parsed.entries[:max_per_feed]
            if not entries:
                print(f"    [空] {label}")
                continue

            added = 0
            for entry in entries:
                # 時間過濾：只保留近48小時
                pub_time = parse_pub_time(entry)
                if pub_time < cutoff:
                    continue

                title = entry.get("title", "")
                source_name = parsed.feed.get("title", label)
                if is_google and " - " in title:
                    parts = title.rsplit(" - ", 1)
                    title = parts[0].strip()
                    source_name = parts[1].strip()

                # 計算距現在幾小時
                hours_ago = (datetime.datetime.now(datetime.timezone.utc) - pub_time).total_seconds() / 3600

                articles.append({
                    "title": title,
                    "summary": entry.get("summary", entry.get("description", ""))[:300],
                    "link": entry.get("link", ""),
                    "published": entry.get("published", ""),
                    "published_dt": pub_time.isoformat(),
                    "hours_ago": round(hours_ago, 1),
                    "source": source_name,
                    "feed_type": "google_news" if is_google else "rss",
                })
                added += 1

            ok_count += 1
            print(f"    ✓ {label}：{added} 筆（近48h）")
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

    # 過濾太短標題
    unique = [a for a in unique if len(a["title"]) > 8]

    # 按發布時間排序（最新在前）
    unique.sort(key=lambda x: x.get("published_dt",""), reverse=True)

    print(f"    → 去重後有效新聞：{len(unique)} 筆")
    return unique[:25]  # 每類最多25筆給 AI

def fetch_all_news() -> dict[str, list[dict]]:
    """抓取所有分類新聞"""
    result = {}
    for category in ["美股", "台股", "投信投顧", "AI產業", "龍頭個股"]:
        print(f"\n  📡 抓取【{category}】新聞...")
        result[category] = fetch_news_from_feeds(category)
        print(f"  → 有效新聞：{len(result[category])} 筆")
    return result

# ─── AI 摘要 ─────────────────────────────────────────────────────────────────

def build_prompt(slot_key: str, news_data: dict, now: datetime.datetime,
                 prices: dict = None) -> str:
    slot = SLOT_CONFIG[slot_key]
    date_str = now.strftime("%Y/%m/%d %H:%M")

    # 每類取前15筆完整標題與摘要，附上時間標記
    news_text = ""
    for category, articles in news_data.items():
        news_text += f"\n\n【{category}原始新聞】（近48小時，共{len(articles)}筆）"
        for i, a in enumerate(articles[:15], 1):
            summary = a.get("summary", "")[:150]
            hours_ago = a.get("hours_ago", "?")
            source = a.get("source", "")
            news_text += f"\n{i}. [{hours_ago}小時前｜{source}] {a['title']}"
            if summary:
                news_text += f"\n   摘要：{summary}"

    # 整理股價資料給 AI
    price_text = ""
    if prices:
        price_text = "\n\n【產業龍頭即時股價】"
        for sector, stocks in WATCHLIST.items():
            price_text += f"\n▍{sector}"
            for name, code in stocks:
                p = prices.get(code, {})
                if p.get("price"):
                    prev = p.get("prev_close", 0)
                    curr = p.get("price", 0)
                    chg = ((curr - prev) / prev * 100) if prev else 0
                    arrow = "↑" if chg > 0 else "↓" if chg < 0 else "→"
                    price_text += f"\n  • {name}({code})：${curr:.1f} {arrow}{chg:+.1f}% (昨收${prev:.1f})"
                else:
                    price_text += f"\n  • {name}({code})：無資料"

    return f"""你是資深財經分析師，擁有20年台美股操盤經驗。現在是 {date_str}，本時段「{slot['label']}」。
本時段關注：{slot['focus']}

以下是最新新聞原始資料，請仔細閱讀並進行深度分析：
{news_text}
{price_text}

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

【產業龍頭進場評估】
針對以上股價資料，逐一分析每檔個股，格式如下：
• 個股名稱(代號) $現價｜今日 ↑↓→X%
  - 基本面：本業獲利狀況、近期營收趨勢（依新聞判斷）
  - 技術面：目前位置偏高/偏低/合理，近期支撐壓力
  - 估值評估：目前本益比區間是否合理
  - 建議進場價：保守價 $XXX ／ 積極價 $XXX
  - 操作建議：買進/觀望/減碼，理由一句話
  - 風險提示：需注意的主要風險

（依序列出所有有股價資料的個股，無資料者標示「待確認」）

【整體結論】
• 今日市場主軸：說明最重要的驅動因素
• 最大風險：說明需要警戒的風險
• 短線操作建議：具體方向與注意事項
• 中線展望：未來1-2週市場方向預判
• 本日最值得關注的3檔個股：列出並說明理由"""

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

def generate_summary(slot_key: str, news_data: dict, now: datetime.datetime,
                     prices: dict = None) -> dict:
    """自動輪換 API：Claude → Gemini → Groq → OpenRouter"""
    prompt = build_prompt(slot_key, news_data, now, prices)
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

def clean_for_firestore(obj):
    """遞迴清理資料，確保 Firestore 可接受（移除 None、轉換 float）"""
    if isinstance(obj, dict):
        return {k: clean_for_firestore(v) for k, v in obj.items()
                if v is not None and k != "published_dt"}
    elif isinstance(obj, list):
        return [clean_for_firestore(i) for i in obj if i is not None]
    elif isinstance(obj, float):
        return round(obj, 4) if obj == obj else 0  # nan check
    elif isinstance(obj, (int, str, bool)):
        return obj
    else:
        return str(obj)

def clean_prices(prices: dict) -> dict:
    """清理股價資料，只保留 Firestore 安全的欄位"""
    result = {}
    for code, p in (prices or {}).items():
        if not p.get("price"):
            continue
        result[code] = {
            "code": str(code),
            "name": str(p.get("name", "")),
            "price": float(p.get("price") or 0),
            "prev_close": float(p.get("prev_close") or 0),
            "today_high": float(p.get("today_high") or 0),
            "today_low": float(p.get("today_low") or 0),
            "open": float(p.get("open") or 0),
            "volume": str(p.get("volume", "0")),
        }
    return result

def save_to_firestore(db, slot_key: str, now: datetime.datetime,
                       news_data: dict, summary: dict, prices: dict = None):
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

        # 產業龍頭股價快照
        "prices": clean_prices(prices),

        # AI 摘要
        "summary_full": summary["full_text"],
        "summary_us": summary["sections"].get("美股重點", ""),
        "summary_tw": summary["sections"].get("台股重點", ""),
        "summary_ai": summary["sections"].get("AI產業議題", ""),
        "summary_fi": summary["sections"].get("投信投顧分析", ""),
        "summary_valuation": summary["sections"].get("產業龍頭進場評估", ""),
        "summary_conclusion": summary["sections"].get("整體結論", ""),
        
        # Token 用量
        "tokens_input": summary["usage"]["input_tokens"],
        "tokens_output": summary["usage"]["output_tokens"],
        "status": "success",
    }
    
    # 清理資料並寫入 Firestore
    doc_data = clean_for_firestore(doc_data)
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
        "summary_valuation": summary["sections"].get("產業龍頭進場評估", ""),
        "conclusion": summary["sections"].get("整體結論", ""),
        "prices": clean_prices(prices),
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

    # 2.5 抓取產業龍頭股價
    print("\n[2.5/4] 抓取產業龍頭即時股價...")
    prices = fetch_all_prices()
    ok_prices = sum(1 for p in prices.values() if p.get("price"))
    print(f"  ✅ 成功取得 {ok_prices}/{len(prices)} 檔股價")

    # 3. AI 摘要（失敗直接中止，不重試）
    print("\n[3/4] 呼叫 AI 生成摘要與估值分析...")
    summary = generate_summary(slot_key, news_data, now, prices)
    print(f"  ✅ 摘要生成完成（input:{summary['usage']['input_tokens']} / output:{summary['usage']['output_tokens']} tokens）")

    # 4. 儲存
    print("\n[4/4] 儲存到 Firestore...")
    doc_id = save_to_firestore(db, slot_key, now, news_data, summary, prices)

    print(f"\n{'='*60}")
    print(f"✅ 完成！文件 ID：{doc_id}")
    print(f"{'='*60}\n")
    print("📋 摘要預覽：")
    print(summary["full_text"][:400] + "...")

if __name__ == "__main__":
    main()
