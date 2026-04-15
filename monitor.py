"""
금감원 제재사례 자동 모니터링
매일 게시판 크롤링 → PDF 분석 → Gmail 알림
"""

import os
import json
import smtplib
import hashlib
import requests
import tempfile
from datetime import date
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from bs4 import BeautifulSoup
import anthropic
import pdfplumber

# ── 환경변수 (GitHub Secrets에서 주입) ──────────────────
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
GMAIL_EMAIL       = os.environ["GMAIL_EMAIL"]
GMAIL_PASSWORD    = os.environ["GMAIL_PASSWORD"]
RECIPIENT_EMAIL   = os.environ.get("RECIPIENT_EMAIL", GMAIL_EMAIL)
MY_PROFILE        = os.environ.get("MY_PROFILE", "보험업 종사자. 관심: 불완전판매, 허위고지, 모집질서, 보험금 지급, 과태료, 설계사 제재")

# ── 상수 ────────────────────────────────────────────────
FSS_BASE     = "https://www.fss.or.kr"
FSS_LIST_URL = f"{FSS_BASE}/fss/job/openInfo/list.do?menuNo=200476"
SEEN_FILE    = "seen_posts.json"

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def fetch_post_list():
    res = requests.get(FSS_LIST_URL, headers=HEADERS, timeout=15)
    res.raise_for_status()
    soup = BeautifulSoup(res.text, "html.parser")

    posts = []
    for row in soup.select("table tbody tr"):
        a_tag = row.find("a", href=True)
        if not a_tag:
            continue
        href = a_tag["href"]
        title = a_tag.get_text(strip=True)
        post_id = hashlib.md5(href.encode()).hexdigest()[:12]
        posts.append({
            "id": post_id,
            "title": title,
            "url": href if href.startswith("http") else FSS_BASE + href,
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


def analyze_with_claude(pdf_text, post_title):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

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

    msg = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = msg.content[0].text.strip().replace("```json", "").replace("```", "").strip()
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


def build_email_html(results, today):
    badge_text  = {"high": "🔴 관련성 높음", "medium": "🟡 관련성 보통", "low": "🟢 낮음", "none": "⚪ 없음"}
    badge_color = {"high": "#fde8e8", "medium": "#fef9e7", "low": "#f0f0f0", "none": "#f0f0f0"}
    text_color  = {"high": "#c0392b", "medium": "#9a7000", "low": "#888", "none": "#888"}

    relevant_count = sum(1 for r in results if r["relevance"] in ("high", "medium"))
    rows = ""

    for r in results:
        if r["relevance"] in ("none", "low"):
            continue
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
        rows = "<p style='color:#aaa;text-align:center;padding:48px 0;font-size:14px;'>오늘은 관련 제재사례가 없습니다.</p>"

    return f"""
    <div style="font-family:sans-serif;max-width:600px;margin:0 auto;background:#f5f0e8;">
      <div style="background:#1a1a2e;color:#f5f0e8;padding:24px 32px;border-bottom:3px solid #c9a84c;">
        <h1 style="margin:0;font-size:18px;">⚖ 금감원 제재사례 모니터링</h1>
        <p style="margin:6px 0 0;font-size:13px;color:#aaa;">{today} &nbsp;·&nbsp; 총 {len(results)}건 분석 &nbsp;·&nbsp; 관련 {relevant_count}건</p>
      </div>
      <div style="padding:24px 32px;">{rows}</div>
      <div style="background:#1a1a2e;color:#555;padding:16px 32px;font-size:11px;text-align:center;">
        금감원 제재사례 자동 모니터링 · 보험업 전용 · GitHub Actions
      </div>
    </div>"""


def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE) as f:
            return set(json.load(f))
    return set()

def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen), f, ensure_ascii=False)


def main():
    today = date.today().strftime("%Y년 %m월 %d일")
    print(f"=== 금감원 모니터링 시작 ({today}) ===")

    seen  = load_seen()
    posts = fetch_post_list()
    new_posts = [p for p in posts if p["id"] not in seen]
    print(f"전체 {len(posts)}건 / 신규 {len(new_posts)}건")

    if not new_posts:
        print("신규 게시글 없음. 종료.")
        return

    results = []
    for post in new_posts:
        print(f"\n▶ {post['title']}")
        try:
            pdfs = fetch_pdf_links(post["url"])
            if not pdfs:
                print("  PDF 없음, 건너뜀")
                seen.add(post["id"])
                continue

            pdf_text = extract_pdf_text(pdfs[0]["url"])
            result   = analyze_with_claude(pdf_text, post["title"])
            print(f"  관련성: {result['relevance']}")

            results.append({
                "title": post["title"],
                "url":   post["url"],
                "relevance": result["relevance"],
                "result": result,
            })
            seen.add(post["id"])

        except Exception as e:
            print(f"  오류: {e}")
            continue

    save_seen(seen)

    if not results:
        print("분석 결과 없음.")
        return

    relevant = [r for r in results if r["relevance"] in ("high", "medium")]
    subject  = (
        f"[금감원 제재알림] {today} · 관련 {len(relevant)}건 발견 ⚠"
        if relevant else
        f"[금감원 제재알림] {today} · 관련 사례 없음"
    )
    send_email(subject, build_email_html(results, today))
    print("\n=== 완료 ===")


if __name__ == "__main__":
    main()
