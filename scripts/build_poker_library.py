"""
Build poker knowledge library theo từng chủ đề vào agents/shared/library/.

Chạy: python scripts/build_poker_library.py
Output:
  agents/shared/library/tam-ly.md     — 29 bài tâm lý
  agents/shared/library/khai-niem.md  — 10 bài khái niệm
  agents/shared/library/dinh-ly.md    — 4 bài định lý
  agents/shared/library/co-ban.md     — 10 bài cơ bản

Để chỉ build 1 chủ đề: python scripts/build_poker_library.py tam-ly
"""

import io
import os
import sys
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import requests
import anthropic
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# ─── Config ───────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent.parent
load_dotenv(BASE_DIR / ".env")

ARTICLE_URLS = {
    "tam-ly": [
        # Trang 1
        "https://wikipoker.net/adhd-va-poker/",
        "https://wikipoker.net/kiem-soat-tam-ly-trong-poker/",
        "https://wikipoker.net/vi-sao-mot-hanh-dong-nho-cung-khien-ban-tilt/",
        "https://wikipoker.net/dac-diem-chung-cua-nhung-chuyen-gia-poker/",
        "https://wikipoker.net/ky-luat-trong-poker/",
        "https://wikipoker.net/can-bang-giua-poker-va-cuoc-song/",
        "https://wikipoker.net/su-may-man-trong-poker/",
        "https://wikipoker.net/gia-tri-that-su-cua-viec-bluff/",
        "https://wikipoker.net/su-binh-than-trong-poker/",
        "https://wikipoker.net/quyet-dinh-boc-dong-tren-ban-poker/",
        # Trang 2
        "https://wikipoker.net/su-kien-nhan-trong-poker/",
        "https://wikipoker.net/hieu-ung-dunning-kruger-trong-poker/",
        "https://wikipoker.net/khi-nao-nen-dung-lai/",
        "https://wikipoker.net/lam-the-nao-de-hoc-poker-dung-cach/",
        "https://wikipoker.net/fomo-trong-poker/",
        "https://wikipoker.net/choi-poker-o-trang-thai-tot-nhat/",
        "https://wikipoker.net/thanh-kien-trong-poker/",
        "https://wikipoker.net/tank-trong-poker-la-gi/",
        "https://wikipoker.net/cai-thien-ket-qua-choi-poker/",
        "https://wikipoker.net/loi-suy-nghi-tieu-cuc-trong-poker/",
        # Trang 3
        "https://wikipoker.net/su-tap-trung-tren-ban-poker/",
        "https://wikipoker.net/session-poker-thua/",
        "https://wikipoker.net/muc-tieu-poker/",
        "https://wikipoker.net/hinh-anh-nguoi-choi-tren-ban-poker/",
        "https://wikipoker.net/downswing-la-gi-poker/",
        "https://wikipoker.net/badbeat-poker-la-gi/",
        "https://wikipoker.net/bi-kip-doc-bai/",
        "https://wikipoker.net/poker-face-la-gi/",
        "https://wikipoker.net/tilt-la-gi-poker/",
    ],
    "khai-niem": [
        "https://wikipoker.net/ev-trong-poker/",
        "https://wikipoker.net/kha-nang-duy-tri-equity-trong-poker/",
        "https://wikipoker.net/card-dead/",
        "https://wikipoker.net/cac-hanh-dong-tren-ban-poker/",
        "https://wikipoker.net/slow-roll-trong-poker/",
        "https://wikipoker.net/2-bet/",
        "https://wikipoker.net/face-card-trong-poker/",
        "https://wikipoker.net/range-phan-cuc-va-range-tuyen-tinh/",
        "https://wikipoker.net/control-pot-trong-poker/",
        "https://wikipoker.net/kha-nang-choi-cua-mot-hand/",
    ],
    "dinh-ly": [
        "https://wikipoker.net/dinh-ly-poker-co-ban/",
        "https://wikipoker.net/dinh-ly-clarkmeister-la-gi/",
        "https://wikipoker.net/dinh-ly-balugawhale-la-gi/",
        "https://wikipoker.net/dinh-ly-zeebo/",
    ],
    "co-ban": [
        "https://wikipoker.net/poker-la-gi/",
        "https://wikipoker.net/truc-giac-va-ky-thuat-trong-poker/",
        "https://wikipoker.net/poker-icm/",
        "https://wikipoker.net/3-bet-poker/",
        "https://wikipoker.net/app-quan-ly-bankroll-poker/",
        "https://wikipoker.net/size-bet-preflop/",
        "https://wikipoker.net/gto-va-solver-co-huu-ich-o-low-stakes-khong/",
        "https://wikipoker.net/cach-nhan-biet-fish-trong-poker-low-stakes/",
        "https://wikipoker.net/meo-choi-poker-hay/",
        "https://wikipoker.net/chien-luoc-preflop-poker/",
    ],
}

CATEGORY_TITLES = {
    "tam-ly":    "TÂM LÝ POKER",
    "khai-niem": "KHÁI NIỆM POKER",
    "dinh-ly":   "ĐỊNH LÝ POKER",
    "co-ban":    "CƠ BẢN POKER",
}

EXTRACT_PROMPT = """\
Bạn nhận được nội dung 1 bài viết về poker. Hãy extract thành format sau, KHÔNG viết thêm gì ngoài format:

**Tên khái niệm:** [tên ngắn gọn, 2-6 từ]
**Định nghĩa:** [1-2 câu giải thích khái niệm là gì]
**Cơ chế tâm lý:** [tại sao chiều sai xảy ra — cơ chế não bộ/tâm lý cụ thể, 1 câu. Đây là "Z" trong bridge: story ngoài đời và poker chia sẻ cùng cơ chế này]
**Chiều sai:** [hành động cụ thể người chơi thường làm sai → hậu quả cụ thể]
**Chiều đúng:** [hành động cụ thể → cơ chế → kết quả]
**Ví dụ bàn:** [1 tình huống cụ thể tại bàn poker, 2-3 câu]

Nếu bài không có đủ data cho 1 field → viết "N/A".
Trả lời bằng tiếng Việt. Tổng độ dài: tối đa 180 words.
"""

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

# ─── Helpers ──────────────────────────────────────────────────────────────────

def fetch_article_text(url: str) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["nav", "header", "footer", "aside", "script", "style",
                     "noscript", ".wp-block-buttons", ".sharedaddy"]):
        tag.decompose()
    article = (
        soup.find("article")
        or soup.find(class_=["entry-content", "post-content", "article-content"])
        or soup.find("main")
    )
    target = article if article else soup
    parts = []
    for el in target.find_all(["h1", "h2", "h3", "p", "li", "blockquote"]):
        text = el.get_text(separator=" ", strip=True)
        if text and len(text) > 20:
            parts.append(text)
    return "\n".join(parts[:80])


def summarize_with_claude(client: anthropic.Anthropic, article_text: str) -> str:
    for attempt in range(1, 5):
        try:
            msg = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=700,
                messages=[{
                    "role": "user",
                    "content": f"{EXTRACT_PROMPT}\n\nNội dung bài:\n{article_text[:3000]}"
                }]
            )
            return msg.content[0].text.strip()
        except anthropic.RateLimitError:
            wait = 60 * attempt
            print(f"  Rate limit, chờ {wait}s...")
            time.sleep(wait)
        except anthropic.APIStatusError as e:
            if e.status_code == 529:
                wait = 15 * attempt
                print(f"  Overloaded (attempt {attempt}), chờ {wait}s...")
                time.sleep(wait)
            else:
                return f"**Lỗi extract:** {e}"
        except Exception as e:
            return f"**Lỗi extract:** {e}"
    return "**Lỗi extract:** Overloaded sau 4 lần retry"


def build_category(client: anthropic.Anthropic, category: str, urls: list[str], output_path: Path):
    title = CATEGORY_TITLES[category]
    lines = [
        f"## {title}",
        "",
        "Tham chiếu để bridge story → poker insight chính xác.",
        "Dùng **Chiều sai / Chiều đúng** và **Ví dụ bàn** khi viết poker section.",
        "",
    ]
    total = len(urls)
    for i, url in enumerate(urls, 1):
        print(f"  [{i}/{total}] {url}")
        try:
            text = fetch_article_text(url)
            summary = summarize_with_claude(client, text)
            lines.append(summary)
            lines.append("")
            lines.append(f"*Nguồn: {url}*")
            lines.append("")
            lines.append("---")
            lines.append("")
        except Exception as e:
            print(f"  !! Lỗi fetch: {e}")
            lines.append(f"**[Lỗi load bài: {url}]**")
            lines.append("")
        time.sleep(3)

    output_path.write_text("\n".join(lines), encoding="utf-8")
    size_kb = output_path.stat().st_size / 1024
    print(f"  → Saved: {output_path.name} ({size_kb:.1f} KB)")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY không có trong .env")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)

    # Xác định category cần build
    target = sys.argv[1] if len(sys.argv) > 1 else None
    if target and target not in ARTICLE_URLS:
        print(f"ERROR: category '{target}' không hợp lệ. Chọn: {list(ARTICLE_URLS.keys())}")
        sys.exit(1)

    categories = {target: ARTICLE_URLS[target]} if target else ARTICLE_URLS

    output_dir = BASE_DIR / "agents" / "shared" / "library"
    output_dir.mkdir(parents=True, exist_ok=True)

    for category, urls in categories.items():
        print(f"\n=== Building: {CATEGORY_TITLES[category]} ({len(urls)} bài) ===")
        output_path = output_dir / f"{category}.md"
        build_category(client, category, urls, output_path)

    print("\nDone!")


if __name__ == "__main__":
    main()
