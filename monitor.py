"""
금감원 제재사례 자동 모니터링
- 매일 09:00, 13:00: 당일 신규 건만 확인 → 있을 때만 메일
- 매주 금요일 10:00: 주간 게시 건 모아서 메일 (없으면 없다고 발송)
"""

import os
import json
import smtplib
import hashlib
import requests
import tempfile
from datetime import date, datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from bs4 import BeautifulSoup
from google import genai
import pdfplumber

# ── 환경변수 ──────────────────────────────────────────────
GEMINI_API_KEY  = os.environ["GEMINI_API_KEY"]
GMAIL_EMAIL     = os.environ["GMAIL_EMAIL"]
GMAIL_PASSWORD  = os.environ["GMAIL_PASSWORD"]
RECIPIENT_EMAIL = os.environ.get("RECIPIENT_EMAIL", GMAIL_EMAIL)
MY_PROFILE      = os.environ.get("MY_PROFILE", "보험업 종사자. 관심: 불완전판매, 허위고지, 모집질서, 보험금 지급, 과태료, 설계사 제재")
EVENT_SCHEDULE  = os.environ.get("GITHUB_EVENT_SCHEDULE", "")

# ── 상수 ──────────────────────────────────────────────────
FSS_BASE     = "https://www.fss.or.kr"
FSS_LIST_URL = f"{FSS_BASE}/fss/job/openInfo/list.do?menuNo=200476"
SEEN_FILE    = "seen_posts.json"
WEEKLY_FILE  = "weekly_posts.json"

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# cron 스케줄로 실행 모드 판단
IS_WEEKLY = "0 1 * * 5" in EVENT_SCHEDULE


def fetch_post_list():
    res = requests.get(FSS_LIST_URL, headers=HEADERS, timeout=15)
    res.raise_for_status()
    soup = BeautifulSoup(res.text, "html.parser")

    posts = []
    for row in soup.select("table tbody tr"):
        a_tag = row.find("a", href=True)
        if not a_tag:
            continue
        href  = a_tag["href"]
        title = a_tag.get_text(strip=True)

        # 날짜 추출 (td 중 날짜 형식 찾기)
        post_date = None
        for td in row.find_all("td"):
            text = td.get_text(strip=True)
            if len(text) == 10 and text[4] == "-" and text[7] == "-":
                post_date = text
                break

        post_id = hashlib.md5(href.encode()).hexdigest()[:12]
        posts.append({
            "id":    post_id,
            "title": title,
            "url":   href if href.startswith("http") else FSS_BASE + href,
            "date":  post_date,
        })
    return posts


def fetch_pdf_links(post_url):
    res = requests.get(post_url, headers=HEADERS, timeout=15)
    res.raise_for_status()
    soup = BeautifulSoup(res.text, "html.parser")

    pdf_links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(strip=True)
        if ".pdf" in href.lower() or ".pdf" in text.lower():
            full = href if href.startswith("http") else FSS_BASE + href
            pdf_links.append({"name": text or "첨부.pdf", "url": full})
    return pdf_links


def extract_pdf_text(pdf_url):
    res = requests.get(pdf_url, headers=HEADERS, timeout=30)
    res.raise_for_status()

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(res.content)
        tmp_path = f.name

    text = ""
    try:
        with pdfplumber.open(tmp_path) as pdf:
            for page in pdf.pages[:15]:
                t = page.extract_text()
                if t:
                    text += t + "\n"
    finally:
        os.unlink(tmp_path)

    return text[:5000]


def analyze_with_gemini(pdf_text, post_title):
    client = genai.Client(api_key=GEMINI_API_KEY)

    prompt = f"""금융감독원 제재사례 분석 전문가입니다.
아래 제재사례와 사용자 프로필의 관련성을 분석해 JSON만 출력하세요.

## 사용자 프로필
{MY_PROFILE}

## 제재사례 제목
{post_title}

## 제재사례 내용
{pdf_text}

## JSON 출력 (마크다운 없이)
{{
  "relevance": "high" | "medium" | "low" | "none",
  "summary": "제재사례 2-3줄 요약",
  "reason": "관련성 판단 근거",
  "keywords": ["키워드1", "키워드2"],
  "action": "권고 조치 (없으면 빈 문자열)",
  "sanctioned_entity": "제재 대상",
  "sanction_type": "제재 종류"
}}"""

    response = client.models.generate_content(
        model="gemini-2.5-flash-lite",
        contents=prompt
    )
    raw = response.text.strip().replace("```json", "").replace("```", "").strip()
    return json.loads(raw)


def send_email(subject, html_body):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_EMAIL
    msg["To"]      = RECIPIENT_EMAIL
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
        smtp.starttls()
        smtp.login(GMAIL_EMAIL, GMAIL_PASSWORD)
        smtp.sendmail(GMAIL_EMAIL, RECIPIENT_EMAIL, msg.as_string())
    print(f"✅ 이메일 발송 → {RECIPIENT_EMAIL}")


def build_email_html(results, title, subtitle):
    badge_text  = {"high": "🔴 관련성 높음", "medium": "🟡 관련성 보통", "low": "🟢 낮음", "none": "⚪ 없음"}
    badge_color = {"high": "#fde8e8", "medium": "#fef9e7", "low": "#f0f0f0", "none": "#f0f0f0"}
    text_color  = {"high": "#c0392b", "medium": "#9a7000", "low": "#888", "none": "#888"}

    relevant_count = sum(1 for r in results if r["relevance"] in ("high", "medium"))
    rows = ""

    for r in results:
        kws = ", ".join(r["result"].get("keywords", []))
        action_html = f"<p style='color:#c0392b;margin:8px 0 0;font-size:13px;'><strong>⚠ 권고:</strong> {r['result']['action']}</p>" if r["result"].get("action") else ""
        rows += f"""
        <div style="border:1px solid #e0d8c8;border-radius:8px;padding:20px;margin-bottom:16px;background:#fff;">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px;">
            <strong style="font-size:14px;color:#1a1a2e;">{r['title']}</strong>
            <span style="white-space:nowrap;font-size:11px;font-weight:700;padding:3px 12px;border-radius:20px;background:{badge_color[r['relevance']]};color:{text_color[r['relevance']]};">{badge_text[r['relevance']]}</span>
          </div>
          <p style="margin:10px 0 4px;font-size:13px;color:#333;line-height:1.6;">{r['result']['summary']}</p>
          <p style="font-size:12px;color:#999;margin:0;">제재 대상: {r['result'].get('sanctioned_entity','')} &nbsp;·&nbsp; {r['result'].get('sanction_type','')}</p>
          {f'<p style="font-size:12px;color:#aaa;margin:4px 0 0;">키워드: {kws}</p>' if kws else ''}
          {action_html}
          <a href="{r['url']}" style="display:inline-block;margin-top:12px;font-size:12px;color:#c9a84c;text-decoration:none;">원문 보기 →</a>
        </div>"""

    if not rows:
        rows = "<p style='color:#aaa;text-align:center;padding:48px 0;font-size:14px;'>해당 기간 관련 제재사례가 없습니다.</p>"

    return f"""
    <div style="font-family:sans-serif;max-width:600px;margin:0 auto;background:#f5f0e8;">
      <div style="background:#1a1a2e;color:#f5f0e8;padding:24px 32px;border-bottom:3px solid #c9a84c;">
        <h1 style="margin:0;font-size:18px;">⚖ {title}</h1>
        <p style="margin:6px 0 0;font-size:13px;color:#aaa;">{subtitle} &nbsp;·&nbsp; 총 {len(results)}건 분석 &nbsp;·&nbsp; 관련 {relevant_count}건</p>
      </div>
      <div style="padding:24px 32px;">{rows}</div>
      <div style="background:#1a1a2e;color:#555;padding:16px 32px;font-size:11px;text-align:center;">
        금감원 제재사례 자동 모니터링 · 보험업 전용 · GitHub Actions
      </div>
    </div>"""


def load_json(filepath):
    if os.path.exists(filepath):
        with open(filepath) as f:
            return json.load(f)
    return {}

def save_json(filepath, data):
    with open(filepath, "w") as f:
        json.dump(data, f, ensure_ascii=False)


def run_daily():
    """매일 09:00, 13:00 실행 — 당일 신규 건만 처리"""
    today_str = date.today().strftime("%Y-%m-%d")
    print(f"=== 일간 모니터링 ({today_str}) ===")

    seen  = set(load_json(SEEN_FILE).keys()) if os.path.exists(SEEN_FILE) else set()
    posts = fetch_post_list()

    # 당일 게시 + 미처리 건
    new_posts = [
        p for p in posts
        if p["id"] not in seen and p.get("date") == today_str
    ]
    print(f"당일 신규: {len(new_posts)}건")

    if not new_posts:
        print("당일 신규 없음. 메일 미발송.")
        return

    seen_data  = load_json(SEEN_FILE)
    weekly_data = load_json(WEEKLY_FILE)
    results = []

    for post in new_posts:
        print(f"▶ {post['title']}")
        try:
            pdfs = fetch_pdf_links(post["url"])
            if not pdfs:
                print("  PDF 없음")
                seen_data[post["id"]] = today_str
                continue

            pdf_text = extract_pdf_text(pdfs[0]["url"])
            result   = analyze_with_gemini(pdf_text, post["title"])
            print(f"  관련성: {result['relevance']}")

            entry = {
                "title":     post["title"],
                "url":       post["url"],
                "date":      today_str,
                "relevance": result["relevance"],
                "result":    result,
            }
            results.append(entry)
            seen_data[post["id"]]   = today_str
            weekly_data[post["id"]] = entry

        except Exception as e:
            print(f"  오류: {e}")

    save_json(SEEN_FILE, seen_data)
    save_json(WEEKLY_FILE, weekly_data)

    if results:
        relevant = [r for r in results if r["relevance"] in ("high", "medium")]
        subject  = f"[금감원 제재알림] {today_str} 신규 {len(results)}건" + (" ⚠" if relevant else "")
        send_email(subject, build_email_html(results, "금감원 제재사례 모니터링", f"{today_str} 당일 신규"))


def run_weekly():
    """매주 금요일 — 주간 누적 건 발송"""
    today = date.today()
    week_start = (today - timedelta(days=4)).strftime("%Y-%m-%d")
    week_end   = today.strftime("%Y-%m-%d")
    print(f"=== 주간 리포트 ({week_start} ~ {week_end}) ===")

    weekly_data = load_json(WEEKLY_FILE)
    results = list(weekly_data.values())

    # 주간 파일 초기화
    save_json(WEEKLY_FILE, {})

    relevant = [r for r in results if r["relevance"] in ("high", "medium")]
    subject  = (
        f"[금감원 주간리포트] {week_start}~{week_end} · {len(results)}건"
        if results else
        f"[금감원 주간리포트] {week_start}~{week_end} · 신규 없음"
    )
    send_email(subject, build_email_html(
        results,
        "금감원 제재사례 주간 리포트",
        f"{week_start} ~ {week_end}"
    ))


def main():
    if IS_WEEKLY:
        run_weekly()
    else:
        run_daily()


if __name__ == "__main__":
    main()
