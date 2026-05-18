#!/usr/bin/env python3
"""
Spades Fact Checker
Verify facts trong bài viết trước khi đăng.

Cách chạy:
  python check.py outputs/posts/tilt_control_xxx.md     # check 1 bài
  python check.py --pipeline "tilt control"             # scan → write → check luôn
"""

import re
import json
import anthropic
import os
import sys
import time
import threading
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime, date

CHECKER_AGENT     = Path(__file__).parent / "agents" / "fact-checker.md"
SCANNER_AGENT     = Path(__file__).parent / "agents" / "story-scanner.md"
WRITER_AGENT      = Path(__file__).parent / "agents" / "content-writer.md"
STRATEGIST_AGENT  = Path(__file__).parent / "agents" / "content-strategist.md"
REVIEWER_AGENT    = Path(__file__).parent / "agents" / "content-reviewer.md"
MODEL          = "claude-sonnet-4-6"

# Pricing claude-sonnet-4-6 (USD per million tokens)
PRICE_INPUT    = 3.00
PRICE_OUTPUT   = 15.00

_client        = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
_session_usage: list[dict] = []
_session_lock  = threading.Lock()

# 8 domain — router chọn 1-2 phù hợp nhất với topic
_SCAN_DOMAINS = [
    ("Thể thao",               "bóng đá, tennis, pickleball, vận động viên Việt Nam, giải đấu thể thao, thi đấu đỉnh cao"),
    ("Kinh doanh / Startup",   "founder stories, CEO decisions, startup pivot, business under pressure, entrepreneur"),
    ("Đầu tư / Forex / Crypto","forex trader, stock market, crypto, Warren Buffett, trading under pressure, market crash"),
    ("Lịch sử / Chiến tranh",  "battle strategy, military commanders, historical decisions under pressure, war turning points"),
    ("Phim / Gaming / Esports","movie scenes with strategy, pro gamer decisions, esports tournaments, game theory moments"),
    ("Bida",                   "billiards, snooker, pool, cue sports, precision shots, professional billiard players"),
    ("Khoa học / Khám phá",    "scientific discovery, NASA, space missions, experiments with unexpected results, researchers"),
    ("Triết học phương Tây",   "stoicism, Marcus Aurelius, Seneca, Epictetus, philosophical decisions, ancient wisdom applied"),
]
_SCAN_DOMAIN_MAP = {name: hints for name, hints in _SCAN_DOMAINS}

# Router: chọn 1-2 domain dựa trên cơ chế/cảm xúc của topic
_ROUTER_PROMPT = """Nhận topic poker, chọn 1-2 domain có khả năng cao tìm được story thật bridge tự nhiên.

Domains: Thể thao | Kinh doanh / Startup | Đầu tư / Forex / Crypto | Lịch sử / Chiến tranh | Phim / Gaming / Esports | Bida | Khoa học / Khám phá | Triết học phương Tây

Gợi ý mapping:
- Precision / chi tiết nhỏ quyết định → Thể thao, Bida
- Cảm xúc / tilt / kiểm soát tâm lý → Triết học phương Tây, Thể thao
- Bluff / deception / kiểm soát narrative → Lịch sử / Chiến tranh, Phim / Gaming / Esports
- Resource / tiền / survival → Đầu tư / Forex / Crypto, Kinh doanh / Startup
- Kỳ vọng / table image / perception → Phim / Gaming / Esports, Triết học phương Tây
- Variance / may mắn vs kỹ năng → Đầu tư / Forex / Crypto, Thể thao
- Long-term thinking / discipline → Kinh doanh / Startup, Triết học phương Tây
- Strategy / game theory / đọc đối thủ → Lịch sử / Chiến tranh, Phim / Gaming / Esports, Bida
- Position / timing / chờ đúng lúc → Bida, Lịch sử / Chiến tranh
- Data / xác suất / EV → Khoa học / Khám phá, Đầu tư / Forex / Crypto

Output JSON duy nhất, không giải thích: {"domains": ["Domain A", "Domain B"]}"""

# Prompt nhẹ chỉ để evaluate — không cần search instructions
_EVALUATE_PROMPT = """Bạn là story editor cho Spades Board Game Cafe — quán poker giải trí tại HCM.
Target audience: Nam 23-40 tuổi, HCM — quan tâm thể thao, kinh doanh, đầu tư, phát triển bản thân.

Dưới đây là story candidates từ 4 domain. Chọn top 3, đánh số lại STORY #1 / #2 / #3, giữ nguyên nội dung, output format chuẩn + SUMMARY.

TIÊU CHÍ ĐÁNH GIÁ (theo thứ tự ưu tiên):

1. BRIDGE RÕ KHÔNG CẦN GIẢI THÍCH — người đọc tự thấy liên quan mà không cần dẫn dắt. Nếu phải viết "giống như trong poker..." thì bridge yếu.

2. KHOẢNH KHẮC QUYẾT ĐỊNH CỤ THỂ — story phải có 1 moment rõ ràng: ai, quyết định gì, dưới áp lực nào. "Airbnb bán hộp ngũ cốc để trả tiền thuê" > "startup vượt khó khăn."

3. CON SỐ / NHÂN VẬT THẬT — tên người thật + số liệu cụ thể = credibility. Story không có tên người thật hoặc không có con số → điểm thấp.

4. CONTRAST / TWIST — story có điểm đảo ngược bất ngờ sẽ đọc tốt hơn trên feed. "Ron Wayne bán 10% Apple với $800" > "người bỏ lỡ cơ hội."

5. RELEVANCE với Nam 23-40 HCM — thể thao lớn, founder nổi tiếng, tên tuổi quen thuộc sẽ có hook mạnh hơn.

Bridge sang poker hoạt động khi story có: ra quyết định dưới áp lực, risk/reward, đọc người/tình huống, variance, discipline/tilt, bluff/table image, bankroll management, long-term vs short-term.
Bridge BỎ khi phải giải thích dài mới thấy liên quan."""


def _calc_cost(input_tokens: int, output_tokens: int) -> float:
    return (input_tokens * PRICE_INPUT + output_tokens * PRICE_OUTPUT) / 1_000_000


def _print_usage(label: str, input_tokens: int, output_tokens: int) -> None:
    cost = _calc_cost(input_tokens, output_tokens)
    with _session_lock:
        _session_usage.append({"label": label, "input": input_tokens, "output": output_tokens, "cost": cost})
    print(f"  📊 {label}: {input_tokens:,} in + {output_tokens:,} out = ${cost:.4f}")


def print_cost_summary() -> None:
    if not _session_usage:
        return
    print(f"\n{'='*60}")
    print("  COST BREAKDOWN")
    print(f"{'='*60}")
    total_in = total_out = total_cost = 0
    for u in _session_usage:
        print(f"  {u['label']:<25} {u['input']:>7,} in  {u['output']:>6,} out  ${u['cost']:.4f}")
        total_in   += u["input"]
        total_out  += u["output"]
        total_cost += u["cost"]
    print(f"  {'─'*53}")
    print(f"  {'TOTAL':<25} {total_in:>7,} in  {total_out:>6,} out  ${total_cost:.4f}")
    print(f"{'='*60}\n")


def load_agent(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Agent file không tìm thấy: {path}")
    return path.read_text(encoding="utf-8")


def _with_retry(fn, max_retries: int = 5, wait_s: int = 60):
    """Chạy fn(), tự retry khi gặp RateLimitError."""
    for attempt in range(1, max_retries + 1):
        try:
            return fn()
        except anthropic.RateLimitError:
            if attempt < max_retries:
                print(f"\n⏳ Rate limit — đợi {wait_s}s (lần {attempt}/{max_retries})...", flush=True)
                time.sleep(wait_s)
            else:
                raise


def run_router(query: str) -> list[str]:
    """Chọn 1-2 domain phù hợp nhất với topic. Chi phí ~$0.001."""
    import json
    response = _client.messages.create(
        model=MODEL,
        max_tokens=60,
        system=_ROUTER_PROMPT,
        messages=[{"role": "user", "content": query}],
    )
    _print_usage("Router", response.usage.input_tokens, response.usage.output_tokens)
    try:
        domains = json.loads(response.content[0].text).get("domains", [])
        valid = [d for d in domains if d in _SCAN_DOMAIN_MAP]
        if valid:
            return valid
    except Exception:
        pass
    return [_SCAN_DOMAINS[0][0], _SCAN_DOMAINS[1][0]]  # fallback


def _has_strong_story(text: str) -> bool:
    """Kiểm tra scanner output có story STRONG không."""
    return bool(re.search(r'BRIDGE QUALITY.*STRONG', text))


def stream_agent(system: str, user: str, label: str,
                 max_tokens: int = 3000, use_search: bool = False,
                 max_retries: int = 5, retry_wait: int = 60) -> str:
    client = _client

    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}\n")

    kwargs = dict(
        model=MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    if use_search:
        kwargs["tools"] = [{"type": "web_search_20250305", "name": "web_search"}]

    def _do_stream():
        full_response = ""
        total_in = total_out = 0
        with client.messages.stream(**kwargs) as stream:
            for event in stream:
                if not hasattr(event, "type"):
                    continue
                if event.type == "message_start":
                    u = getattr(getattr(event, "message", None), "usage", None)
                    if u:
                        total_in  += getattr(u, "input_tokens", 0)
                        total_out += getattr(u, "output_tokens", 0)
                elif event.type == "message_delta":
                    u = getattr(event, "usage", None)
                    if u:
                        total_out += getattr(u, "output_tokens", 0)
                elif event.type == "content_block_start":
                    if hasattr(event, "content_block"):
                        if getattr(event.content_block, "type", "") == "tool_use":
                            if getattr(event.content_block, "name", "") == "web_search":
                                print(f"\n🔍 Searching...", end="", flush=True)
                elif event.type == "content_block_delta":
                    if hasattr(event, "delta") and hasattr(event.delta, "text"):
                        t = event.delta.text
                        print(t, end="", flush=True)
                        full_response += t
        return full_response, total_in, total_out

    full_response, total_in, total_out = _with_retry(_do_stream, max_retries, retry_wait)
    print(f"\n\n{'='*60}\n")
    _print_usage(label, total_in, total_out)
    return full_response


def run_checker(article: str, source_label: str = "") -> str:
    system = load_agent(CHECKER_AGENT)
    user = f"""Verify tất cả facts trong bài viết này trước khi đăng.

=== BÀI VIẾT ===
{article}
=== END ===

Xuất đầy đủ FACT CHECK REPORT theo format đã định."""
    return stream_agent(system, user,
                        label=f"FACT CHECKER — {source_label or 'bài viết'}",
                        max_tokens=3000, use_search=True)


def _scan_domain_mini(query: str, domain: str, hints: str, scanner_system: str) -> tuple[str, int, int]:
    """Scan 1 domain, không print streaming (chạy trong thread). Trả về (text, in_tok, out_tok)."""
    client = _client
    user = (
        f"/scan {query}\n\n"
        f"Focus ONLY on {domain} domain ({hints}). "
        f"Search tối đa 3 lần. "
        f"Tìm 1 story candidate tốt nhất. "
        f"Output đúng 1 STORY block theo format chuẩn rồi dừng — không cần SUMMARY."
    )
    kwargs = dict(
        model=MODEL,
        max_tokens=1500,
        system=scanner_system,
        messages=[{"role": "user", "content": user}],
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
    )
    def _do_mini():
        text = ""
        total_in = total_out = 0
        with client.messages.stream(**kwargs) as stream:
            for event in stream:
                if not hasattr(event, "type"):
                    continue
                if event.type == "message_start":
                    u = getattr(getattr(event, "message", None), "usage", None)
                    if u:
                        total_in  += getattr(u, "input_tokens", 0)
                        total_out += getattr(u, "output_tokens", 0)
                elif event.type == "message_delta":
                    u = getattr(event, "usage", None)
                    if u:
                        total_out += getattr(u, "output_tokens", 0)
                elif event.type == "content_block_delta":
                    if hasattr(event, "delta") and hasattr(event.delta, "text"):
                        text += event.delta.text
        return text, total_in, total_out

    return _with_retry(_do_mini, max_retries=5, wait_s=60)


def run_scanner(query: str) -> str:
    """Parallel domain scan (4 threads) + evaluate call. ~70% ít token hơn single-call."""
    print(f"\n{'='*60}")
    print(f"  STORY SCANNER — {query}")
    print(f"{'='*60}\n")

    # [A] Router chọn 1-2 domain phù hợp nhất
    chosen_domains = run_router(query)
    print(f"  Domains được chọn: {', '.join(chosen_domains)}\n")

    # [B+C] Sequential scan + early stop + tối đa 2 searches/domain
    scanner_system = load_agent(SCANNER_AGENT)
    domain_results: dict[str, str] = {}
    total_in = total_out = 0

    for domain in chosen_domains:
        hints = _SCAN_DOMAIN_MAP.get(domain, "")
        print(f"  Scanning {domain}...")
        try:
            text, in_tok, out_tok = _scan_domain_mini(query, domain, hints, scanner_system)
            domain_results[domain] = text
            total_in  += in_tok
            total_out += out_tok
            print(f"  ✓ {domain}")
            if _has_strong_story(text):
                print(f"  ⚡ Story STRONG — dừng scan sớm")
                break
        except anthropic.RateLimitError:
            print(f"  ⏳ Rate limit — đợi 60s...")
            time.sleep(60)
            try:
                text, in_tok, out_tok = _scan_domain_mini(query, domain, hints, scanner_system)
                domain_results[domain] = text
                total_in += in_tok
                total_out += out_tok
                print(f"  ✓ {domain} (retry)")
                if _has_strong_story(text):
                    print(f"  ⚡ Story STRONG — dừng scan sớm")
                    break
            except Exception as e:
                print(f"  ✗ {domain}: {e}")
        except Exception as e:
            print(f"  ✗ {domain}: {e}")

    _print_usage("Scanner", total_in, total_out)

    # Cache domain results phòng evaluate crash
    _cache_dir = Path("outputs/stories")
    _cache_dir.mkdir(parents=True, exist_ok=True)
    _cache_file = _cache_dir / f"_domains_{query.replace(' ', '_')}.txt"
    _cache_file.write_text(
        "\n\n".join(f"=== DOMAIN: {d} ===\n{t}" for d, t in domain_results.items() if t),
        encoding="utf-8"
    )

    # Evaluate — không search, chọn top stories từ candidates
    print(f"\n  Evaluating candidates...\n")
    combined = "\n\n".join(
        f"=== DOMAIN: {d} ===\n{t}"
        for d, t in domain_results.items() if t
    )
    eval_kwargs = dict(
        model=MODEL,
        max_tokens=2500,
        system=_EVALUATE_PROMPT,
        messages=[{"role": "user", "content": f"Query: {query}\n\n{combined}"}],
    )
    def _do_eval():
        eval_text = ""
        total_in = total_out = 0
        with _client.messages.stream(**eval_kwargs) as stream:
            for event in stream:
                if not hasattr(event, "type"):
                    continue
                if event.type == "message_start":
                    u = getattr(getattr(event, "message", None), "usage", None)
                    if u:
                        total_in  += getattr(u, "input_tokens", 0)
                        total_out += getattr(u, "output_tokens", 0)
                elif event.type == "message_delta":
                    u = getattr(event, "usage", None)
                    if u:
                        total_out += getattr(u, "output_tokens", 0)
                elif event.type == "content_block_delta":
                    if hasattr(event, "delta") and hasattr(event.delta, "text"):
                        t = event.delta.text
                        print(t, end="", flush=True)
                        eval_text += t
        return eval_text, total_in, total_out

    eval_text, total_in, total_out = _with_retry(_do_eval, max_retries=8, wait_s=90)
    print(f"\n\n{'='*60}\n")
    _print_usage("Story Evaluator", total_in, total_out)
    return eval_text


def run_strategist(story: str, query: str) -> str:
    system = load_agent(STRATEGIST_AGENT)
    # Đính kèm 5 bài gần nhất để tránh lặp angle/audience
    recent = load_content_log()[-5:]
    history_section = ""
    if recent:
        lines = ["=== 5 BÀI GẦN NHẤT (tránh lặp angle/audience) ==="]
        for e in recent:
            lines.append(f"- [{e['date']}] {e['topic']} | {e.get('audience','')} | {e.get('angle','')}")
        history_section = "\n" + "\n".join(lines) + "\n=== END HISTORY ==="
    user = f"Query: {query}{history_section}\n\n=== STORY ĐÃ CHỌN ===\n{story}\n=== END ==="
    return stream_agent(system, user,
                        label=f"CONTENT STRATEGIST — {query}",
                        max_tokens=400, use_search=False)


def confirm_brief(brief: str) -> str:
    """Hiển thị brief, cho user confirm hoặc chỉnh sửa. Trả về brief cuối cùng."""
    print("\n" + "="*60)
    print("  MARKETING BRIEF — xác nhận trước khi viết bài")
    print("="*60)
    print(brief)
    print("\n" + "-"*60)
    print("Nhấn Enter để dùng brief này, hoặc nhập chỉnh sửa:")
    edit = input("> ").strip()
    if edit:
        print("✓ Đã cập nhật brief.\n")
        return edit
    print("✓ Dùng brief gốc.\n")
    return brief


def run_writer(story: str, query: str, brief: str = "") -> str:
    system = load_agent(WRITER_AGENT)
    brief_section = f"\n=== MARKETING BRIEF ===\n{brief}\n=== END BRIEF ===" if brief else ""
    user = f"""Dưới đây là story đã chọn và marketing brief.{brief_section}

=== STORY ===
{story}
=== END ===

Viết bài theo lamwork style, bám sát AUDIENCE / GOAL / ANGLE / POKER LENS / CTA trong brief.
Xuất đủ PHẦN 1 (bài viết) và PHẦN 2 (checklist verify)."""
    return stream_agent(system, user,
                        label=f"CONTENT WRITER — {query}",
                        max_tokens=3000, use_search=False)


def run_reviewer(article: str, query: str) -> str:
    system = load_agent(REVIEWER_AGENT)
    user = f"""Đánh giá chất lượng bài viết này theo 6 tiêu chí.

=== BÀI VIẾT ===
{article}
=== END ===

Chấm điểm thật, đề xuất cụ thể."""
    return stream_agent(system, user,
                        label=f"CONTENT REVIEWER — {query}",
                        max_tokens=2000, use_search=False)


def parse_stories(scanner_output: str) -> list[dict]:
    """Tách từng story từ scanner output, trả về list dict với title + nội dung."""
    stories = []

    # Pattern 1: format chuẩn ═══ STORY #N ═══
    blocks = re.split(r'═{5,}[\s\n]+STORY\s*#\s*\d+[\s\n]+═{5,}', scanner_output)
    # Pattern 2 fallback: chỉ có header STORY #N (không có ═══)
    if len(blocks) <= 1:
        blocks = re.split(r'\n(?:STORY|Story)\s*#\s*(\d+)\s*\n', scanner_output)
    # Pattern 3 fallback: markdown header ## 1. hoặc **STORY #1**
    if len(blocks) <= 1:
        blocks = re.split(r'\n(?:#{1,3}|\*{2})\s*(?:STORY\s*)?#?\s*\d+', scanner_output)

    for i, block in enumerate(blocks[1:], start=1):
        title = "Không rõ"
        domain = ""
        bridge = ""
        verdict = ""

        m = re.search(r'Tiêu đề[:：]\s*(.+)', block)
        if m:
            title = m.group(1).strip()

        m = re.search(r'DOMAIN[:：]\s*(.+)', block)
        if m:
            domain = m.group(1).strip()

        m = re.search(r'BRIDGE QUALITY[:：]\s*(.+)', block)
        if m:
            bridge = m.group(1).strip()

        m = re.search(r'Dùng được[:：]\s*(.+)', block)
        if m:
            verdict = m.group(1).strip()

        stories.append({
            "index": i,
            "title": title,
            "domain": domain,
            "bridge": bridge,
            "verdict": verdict,
            "content": block.strip(),
        })
    return stories


def pick_story(scanner_output: str) -> str:
    """Hiển thị danh sách stories và hỏi người dùng chọn."""
    stories = parse_stories(scanner_output)

    if not stories:
        print("⚠️  Không parse được stories — dùng toàn bộ scanner output.")
        return scanner_output

    print("\n" + "="*60)
    print("  CHỌN STORY ĐỂ VIẾT BÀI")
    print("="*60)
    for s in stories:
        print(f"\n  [{s['index']}] {s['title']}")
        if s['domain']:
            print(f"      Domain  : {s['domain']}")
        if s['bridge']:
            print(f"      Bridge  : {s['bridge']}")
        if s['verdict']:
            print(f"      Verdict : {s['verdict']}")
    print()

    valid = [str(s["index"]) for s in stories]
    while True:
        choice = input(f"Chọn story ({'/'.join(valid)}): ").strip()
        if choice in valid:
            selected = next(s for s in stories if str(s["index"]) == choice)
            print(f"\n✓ Đã chọn: {selected['title']}\n")
            return selected["content"]
        print(f"❌ Nhập {' hoặc '.join(valid)}")


def extract_article(writer_output: str) -> str:
    """Trích phần bài viết từ output của writer (PHẦN 1)."""
    if "PHẦN 1" in writer_output:
        parts = writer_output.split("PHẦN 2")
        article_block = parts[0]
        # Bỏ header "PHẦN 1 — BÀI VIẾT" và dấu ---
        lines = article_block.split("\n")
        cleaned = []
        skip_header = True
        for line in lines:
            if skip_header and ("PHẦN 1" in line or line.strip() == "---"):
                skip_header = False
                continue
            cleaned.append(line)
        return "\n".join(cleaned).strip()
    return writer_output.strip()


CONTENT_LOG_PATH = Path("outputs/content_log.json")


def load_content_log() -> list[dict]:
    """Đọc lịch sử bài đã viết."""
    if not CONTENT_LOG_PATH.exists():
        return []
    try:
        return json.loads(CONTENT_LOG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def log_article(topic: str, brief: str, post_path: Path) -> None:
    """Lưu thông tin bài vừa viết vào content log."""
    log = load_content_log()
    entry = {"date": str(date.today()), "topic": topic, "post": str(post_path)}
    for field in ("AUDIENCE", "GOAL", "ANGLE", "CTA"):
        m = re.search(rf'{field}\s*[:：]\s*(.+)', brief)
        entry[field.lower()] = m.group(1).strip() if m else ""
    log.append(entry)
    CONTENT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONTENT_LOG_PATH.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")


def save_output(content: str, label: str, output_type: str) -> Path:
    output_dir = Path("outputs") / output_type
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in label)[:35]
    path = output_dir / f"{safe.strip().replace(' ', '_')}_{timestamp}.md"
    header = f"# {label}\n_{datetime.now().strftime('%d/%m/%Y %H:%M')}_\n\n---\n\n"
    path.write_text(header + content, encoding="utf-8")
    return path


def main():
    parser = argparse.ArgumentParser(description="Spades Fact Checker")
    parser.add_argument("article_file", nargs="?",
                        help="File bài viết cần check (.md)")
    parser.add_argument("--pipeline",
                        help="Full pipeline: scan → write → check")
    parser.add_argument("--no-check", action="store_true",
                        help="Bỏ qua bước Fact Checker")
    parser.add_argument("--no-brief", action="store_true",
                        help="Bỏ qua bước Content Strategist, viết thẳng")
    parser.add_argument("--no-review", action="store_true",
                        help="Bỏ qua bước Content Reviewer")
    args = parser.parse_args()

    # ── MODE 1: Full pipeline ──────────────────────────────────────
    if args.pipeline:
        query = args.pipeline
        _session_usage.clear()
        steps = "Scanner → Strategist → Writer" + ("" if args.no_check else " → Fact Checker")
        print(f"\n🔄 FULL PIPELINE: {query}")
        print(f"   {steps}\n")

        # Bước 1 — scan
        scanner_out = run_scanner(query)
        s_path = save_output(scanner_out, f"stories_{query}", "stories")
        print(f"✓ Stories: {s_path}")

        # Bước 2 — chọn story
        chosen_story = pick_story(scanner_out)

        # Bước 3 — brief (optional)
        if args.no_brief:
            final_brief = ""
        else:
            final_brief = confirm_brief(run_strategist(chosen_story, query))

        # Bước 4 — viết bài
        writer_out = run_writer(chosen_story, query, brief=final_brief)
        w_path = save_output(writer_out, query, "posts")
        log_article(query, final_brief, w_path)
        print(f"✓ Bài viết: {w_path}")

        article = extract_article(writer_out)

        # Bước 5 — review chất lượng (optional)
        r_path = None
        if not args.no_review:
            review_out = run_reviewer(article, query)
            r_path = save_output(review_out, f"review_{query}", "checks")
            print(f"✓ Review  : {r_path}")

        # Bước 6 — fact check (optional)
        c_path = None
        if not args.no_check:
            check_out = run_checker(article, query)
            c_path = save_output(check_out, f"check_{query}", "checks")
            print(f"✓ Fact check: {c_path}")

        print(f"\n{'='*60}")
        print("PIPELINE HOÀN TẤT")
        print(f"  Stories : {s_path}")
        print(f"  Bài viết: {w_path}")
        if r_path:
            print(f"  Review  : {r_path}")
        if c_path:
            print(f"  Check   : {c_path}")
        print(f"{'='*60}\n")
        print_cost_summary()
        return

    # ── MODE 2: Check 1 file ───────────────────────────────────────
    if args.article_file:
        article_path = Path(args.article_file)
        if not article_path.exists():
            print(f"❌ File không tìm thấy: {args.article_file}")
            sys.exit(1)
        content = article_path.read_text(encoding="utf-8")
        article = extract_article(content)
        result = run_checker(article, article_path.name)

        save = input("\nLưu report ra file? (y/n): ").strip().lower()
        if save == "y":
            p = save_output(result, f"check_{article_path.stem}", "checks")
            print(f"✓ Đã lưu: {p}")
        return

    # ── MODE 3: Interactive ────────────────────────────────────────
    print("\n✅ SPADES FACT CHECKER")
    print("="*40)
    print("1. Check một file bài viết")
    print("2. Full pipeline (scan → write → check)")
    choice = input("\nChọn (1/2): ").strip()

    if choice == "1":
        f = input("Path file bài viết: ").strip()
        path = Path(f)
        if not path.exists():
            print("❌ File không tìm thấy"); sys.exit(1)
        article = extract_article(path.read_text(encoding="utf-8"))
        result  = run_checker(article, path.name)
        save = input("\nLưu report? (y/n): ").strip().lower()
        if save == "y":
            p = save_output(result, f"check_{path.stem}", "checks")
            print(f"✓ Đã lưu: {p}")

    elif choice == "2":
        query   = input("Topic/concept: ").strip()
        s_out   = run_scanner(query)
        chosen  = pick_story(s_out)
        brief   = confirm_brief(run_strategist(chosen, query))
        w_out   = run_writer(chosen, query, brief=brief)
        article = extract_article(w_out)
        c_out  = run_checker(article, query)
        save = input("\nLưu tất cả? (y/n): ").strip().lower()
        if save == "y":
            print(f"✓ Stories : {save_output(s_out, f'stories_{query}', 'stories')}")
            print(f"✓ Bài viết: {save_output(w_out, query, 'posts')}")
            print(f"✓ Check   : {save_output(c_out, f'check_{query}', 'checks')}")
    else:
        print("❌ Lựa chọn không hợp lệ")


if __name__ == "__main__":
    main()
