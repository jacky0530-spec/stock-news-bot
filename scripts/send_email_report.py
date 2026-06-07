#!/usr/bin/env python3
"""
股市新聞 Email 報告寄送
從 Firebase 讀取最新摘要，用 Resend（免費）寄出 HTML 信件
"""

import os
import json
import datetime
import requests
import firebase_admin
from firebase_admin import credentials, firestore

# ── Firebase 初始化 ──────────────────────────────────────────

def init_firebase():
    key_dict = json.loads(os.environ["FIREBASE_SERVICE_ACCOUNT_KEY"])
    cred = credentials.Certificate(key_dict)
    if not firebase_admin._apps:
        firebase_admin.initialize_app(cred)
    return firestore.client()

# ── 讀取最新摘要 ─────────────────────────────────────────────

def get_latest_summary(db) -> dict:
    doc = db.collection("latest").document("summary").get()
    if not doc.exists:
        raise ValueError("Firebase 尚無摘要資料")
    return doc.to_dict()

# ── 組 HTML 信件 ─────────────────────────────────────────────

def build_email_html(data: dict, now: datetime.datetime) -> str:
    date_str  = now.strftime("%Y年%m月%d日")
    time_str  = now.strftime("%H:%M")
    slot      = data.get("last_slot_label", "定時報告")
    us        = data.get("summary_us", "（無資料）")
    tw        = data.get("summary_tw", "（無資料）")
    fi        = data.get("summary_fi", "（無資料）")
    conclude  = data.get("conclusion", "")
    updated   = data.get("last_updated", "")

    def fmt(text: str) -> str:
        """把純文字換行轉成 HTML"""
        lines = []
        for line in text.strip().split("\n"):
            line = line.strip()
            if not line:
                lines.append("<br>")
            elif line.startswith("1.") or line.startswith("2.") or line.startswith("3."):
                lines.append(f'<div style="margin:4px 0;padding-left:8px;border-left:2px solid #4f8ef7;">{line}</div>')
            else:
                lines.append(f'<p style="margin:4px 0;">{line}</p>')
        return "\n".join(lines)

    return f"""<!DOCTYPE html>
<html lang="zh-TW">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#0d0f14;font-family:'Helvetica Neue',Arial,sans-serif;">
<div style="max-width:640px;margin:0 auto;padding:24px 16px;">

  <!-- Header -->
  <div style="background:#141720;border:0.5px solid rgba(255,255,255,0.1);border-radius:12px;padding:24px;margin-bottom:16px;">
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;">
      <div style="width:8px;height:8px;border-radius:50%;background:#38c9a0;"></div>
      <span style="color:#7a8099;font-size:12px;letter-spacing:1px;text-transform:uppercase;">股市新聞自動整理</span>
    </div>
    <h1 style="color:#e8eaf0;font-size:22px;font-weight:700;margin:0 0 4px;">{date_str} · {slot}</h1>
    <p style="color:#7a8099;font-size:12px;margin:0;">產生時間：{time_str} ｜ 資料更新：{updated}</p>
  </div>

  <!-- 整體結論 -->
  {"" if not conclude else f'''
  <div style="background:linear-gradient(135deg,rgba(79,142,247,0.1),rgba(56,201,160,0.07));border:0.5px solid rgba(79,142,247,0.25);border-radius:12px;padding:16px 20px;margin-bottom:16px;">
    <div style="color:#4f8ef7;font-size:11px;font-weight:600;letter-spacing:1px;margin-bottom:8px;">💡 整體結論</div>
    <div style="color:#e8eaf0;font-size:15px;font-weight:500;line-height:1.7;">{conclude}</div>
  </div>'''}

  <!-- 美股 -->
  <div style="background:#141720;border:0.5px solid rgba(255,255,255,0.08);border-radius:12px;padding:20px;margin-bottom:12px;">
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:14px;">
      <div style="width:34px;height:34px;border-radius:8px;background:rgba(79,142,247,0.12);display:flex;align-items:center;justify-content:center;font-size:18px;">🇺🇸</div>
      <div>
        <div style="color:#e8eaf0;font-size:14px;font-weight:600;">美股重點</div>
        <div style="color:#7a8099;font-size:11px;">US Markets</div>
      </div>
    </div>
    <div style="color:#c8ccda;font-size:13px;line-height:1.85;">{fmt(us)}</div>
  </div>

  <!-- 台股 -->
  <div style="background:#141720;border:0.5px solid rgba(255,255,255,0.08);border-radius:12px;padding:20px;margin-bottom:12px;">
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:14px;">
      <div style="width:34px;height:34px;border-radius:8px;background:rgba(56,201,160,0.12);display:flex;align-items:center;justify-content:center;font-size:18px;">🇹🇼</div>
      <div>
        <div style="color:#e8eaf0;font-size:14px;font-weight:600;">台股重點</div>
        <div style="color:#7a8099;font-size:11px;">TWSE Markets</div>
      </div>
    </div>
    <div style="color:#c8ccda;font-size:13px;line-height:1.85;">{fmt(tw)}</div>
  </div>

  <!-- 投信投顧 -->
  <div style="background:#141720;border:0.5px solid rgba(255,255,255,0.08);border-radius:12px;padding:20px;margin-bottom:16px;">
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:14px;">
      <div style="width:34px;height:34px;border-radius:8px;background:rgba(247,192,79,0.12);display:flex;align-items:center;justify-content:center;font-size:18px;">📊</div>
      <div>
        <div style="color:#e8eaf0;font-size:14px;font-weight:600;">投信投顧分析</div>
        <div style="color:#7a8099;font-size:11px;">Fund Analysis</div>
      </div>
    </div>
    <div style="color:#c8ccda;font-size:13px;line-height:1.85;">{fmt(fi)}</div>
  </div>

  <!-- Footer -->
  <div style="text-align:center;padding:12px;color:#4a5068;font-size:11px;">
    此報告由 AI 自動整理，僅供參考，不構成投資建議<br>
    Stock News Bot · 自動排程 06:00 / 09:00 / 12:00 / 15:00 / 21:00
  </div>

</div>
</body>
</html>"""

# ── 寄信（Resend API）────────────────────────────────────────

def send_email(html: str, subject: str):
    """使用 Resend 免費 API 寄信（每月 3000 封免費）"""
    resend_api_key = os.environ["RESEND_API_KEY"]
    to_emails      = os.environ["EMAIL_TO"].split(",")   # 多個收件人用逗號分隔
    from_email     = os.environ.get("EMAIL_FROM", "stock-bot@resend.dev")

    response = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {resend_api_key}",
            "Content-Type": "application/json",
        },
        json={
            "from": from_email,
            "to": [e.strip() for e in to_emails],
            "subject": subject,
            "html": html,
        },
        timeout=30,
    )

    if response.status_code not in (200, 201):
        raise RuntimeError(f"Resend API 失敗：{response.status_code} {response.text}")

    result = response.json()
    print(f"  ✅ Email 寄送成功，ID：{result.get('id','—')}")
    return result

# ── 主流程 ───────────────────────────────────────────────────

def main():
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
    print(f"📧 Email 報告寄送：{now.strftime('%Y/%m/%d %H:%M')}")

    db   = init_firebase()
    data = get_latest_summary(db)

    slot  = data.get("last_slot_label", "定時報告")
    date_ = now.strftime("%m/%d")
    subject = f"【股市快報】{date_} {slot} — AI 市場摘要"

    html = build_email_html(data, now)
    send_email(html, subject)
    print("✅ 完成")

if __name__ == "__main__":
    main()
