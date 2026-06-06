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
import anthropic
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
    for category in ["美股", "台股", "投信投顧"]:
        print(f"\n  📡 抓取【{category}】新聞...")
        result[category] = fetch_news_from_feeds(category)
        print(f"  → 有效新聞：{len(result[category])} 筆")
    return result

# ─── AI 摘要 ─────────────────────────────────────────────────────────────────

def build_prompt(slot_key: str, news_data: dict, now: datetime.datetime) -> str:
    slot = SLOT_CONFIG[slot_key]
    date_str = now.strftime("%Y/%m/%d %H:%M")
    
    news_text = ""
    for category, articles in news_data.items():
        news_text += f"\n\n【{category}原始新聞】\n"
        for i, a in enumerate(articles, 1):
            news_text += f"{i}. {a['title']}\n   {a['summary'][:150]}\n"
    
    return f"""你是專業財經分析師，現在是 {date_str}，本時段為「{slot['label']}」。
本時段重點關注：{slot['focus']}

以下是剛抓取的最新新聞：{news_text}

請整理成以下格式的結構化摘要（繁體中文）：

【美股重點】
1. 三大重點新聞（每條一句話，含數字）
2. 盤勢氛圍：多/空/中性 + 主因
3. 關注個股/主題：（列3個）
4. 操作方向：積極/保守/觀望

【台股重點】
1. 三大重點新聞（每條一句話，含數字）
2. 盤勢氛圍：多/空/中性 + 主因
3. 關注族群：（列3個）
4. 操作方向：積極/保守/觀望

【投信投顧動態】
1. 本週主推商品/ETF
2. 法人動向摘要
3. 市場操作建議

【整體結論】
一句話總結今日市場氛圍與操作重點。

格式要求：條列清晰、專業易讀、善用數字，每區塊200字以內。"""

def generate_summary(slot_key: str, news_data: dict, now: datetime.datetime) -> dict:
    """呼叫 Claude API 生成摘要"""
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    
    prompt = build_prompt(slot_key, news_data, now)
    
    message = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}]
    )
    
    full_text = message.content[0].text
    
    # 解析各區塊
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
    
    return {
        "full_text": full_text,
        "sections": sections,
        "usage": {
            "input_tokens": message.usage.input_tokens,
            "output_tokens": message.usage.output_tokens,
        }
    }

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
        
        # AI 摘要
        "summary_full": summary["full_text"],
        "summary_us": summary["sections"].get("美股重點", ""),
        "summary_tw": summary["sections"].get("台股重點", ""),
        "summary_fi": summary["sections"].get("投信投顧動態", ""),
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
        "summary_us": summary["sections"].get("美股重點", ""),
        "summary_tw": summary["sections"].get("台股重點", ""),
        "summary_fi": summary["sections"].get("投信投顧動態", ""),
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
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))  # 台灣時間
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
    
    # 2. 抓取新聞
    print("\n[2/4] 抓取最新新聞...")
    news_data = fetch_all_news()
    total = sum(len(v) for v in news_data.values())
    print(f"  ✅ 共抓取 {total} 筆新聞")
    
    # 3. AI 摘要
    print("\n[3/4] 呼叫 Claude AI 生成摘要...")
    summary = generate_summary(slot_key, news_data, now)
    print(f"  ✅ 摘要生成完成（{summary['usage']['output_tokens']} tokens）")
    
    # 4. 儲存
    print("\n[4/4] 儲存到 Firestore...")
    doc_id = save_to_firestore(db, slot_key, now, news_data, summary)
    
    print(f"\n{'='*60}")
    print(f"✅ 完成！文件 ID：{doc_id}")
    print(f"{'='*60}\n")
    
    # 印出摘要預覽
    print("📋 摘要預覽：")
    print(summary["full_text"][:500] + "...")

if __name__ == "__main__":
    main()
