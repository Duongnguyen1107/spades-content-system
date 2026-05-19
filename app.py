#!/usr/bin/env python3
"""
Spades Content Bot — Webhook mode
Deploy: CloudPanel Python Site tại diymode.work
"""

import asyncio
import os
import re
import sys
import threading
from io import BytesIO
from pathlib import Path

import json as _json

import anthropic as _anthropic_lib
from dotenv import load_dotenv
from flask import Flask, request, jsonify
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

load_dotenv()

BASE_DIR   = Path(__file__).parent
_pending_templates: dict[int, dict] = {}  # chat_id → {topic, templates}
_pending_story: dict[int, dict] = {}      # chat_id → {slug, step, stories}
_pending_guided: dict[int, dict] = {}     # chat_id → {state, topic, messages, anchor, slug}

_anthropic_client = _anthropic_lib.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

_ANCHOR_CHAT_SYSTEM = """Bạn giúp làm rõ topic trước khi tìm story cho Spades Board Game Cafe — quán poker giải trí tại HCM.

Mục tiêu: hỏi 2-3 lượt để hiểu chính xác khía cạnh nào của topic người dùng muốn viết, rồi đúc kết thành anchor cụ thể đủ để tìm story và bridge sang poker.

Cách hỏi:
- Mỗi lượt hỏi đúng 1 câu, ngắn gọn, dùng ngôn ngữ đời thường
- Đưa ra 3-4 options cụ thể đánh số [1][2][3] + [0] nhập tay — options phải khác nhau về cơ chế
- Không giải thích dài dòng, không dùng từ kỹ thuật poker
- Sau 2-3 lượt: tóm tắt và hỏi confirm bằng "Reply Y để xác nhận, hoặc tiếp tục chỉnh."

Khi đủ rõ VÀ user xác nhận (reply Y/y/yes/ok/đồng ý), output ĐÚNG format này — không thêm gì khác:
<STRATEGY>
CONCEPT      : [cơ chế cốt lõi — X xảy ra → Y, không phải định nghĩa]
CHIỀU SAI    : [hành động cụ thể người hay làm] → [hậu quả cụ thể ở bàn poker]
CHIỀU ĐÚNG   : [hành động thay thế] → [lý do cơ học nó work] → [kết quả]
POKER MOMENT : [khoảnh khắc cụ thể ở bàn — ai, đang làm gì]
FORMAT       : [Story-Bridge / Personal Reflection]
AUDIENCE     : [Tier 1 — Chưa chơi / Tier 2 — Mới chơi / Tier 3 — Thường xuyên / Broad]
HOOK TYPE    : [Kết quả trước / Vào giữa trận / Số liệu đối lập / Câu hỏi tu từ / Đối lập hành động]
DOMAIN       : [domain tốt nhất — 1 trong: Bóng đá / Esports Gaming / MMA Boxing / Đầu tư Forex Crypto / Kinh doanh thương hiệu lớn / Triết học Stoicism / Hàng không vũ trụ / Lịch sử thế giới / Phim Series triết học]
STORY PATTERN: [tìm story về ai + làm gì + hậu quả gì — đủ cụ thể để Google]
EMOTIONAL ARC: [cảm xúc hook — lý do] → [cảm xúc bridge — lý do] → [cảm xúc sau CTA — lý do]
SHARE TRIGGER: [Identity signal / Recognition gift / Conversation starter] — [element nào kích hoạt]
</STRATEGY>"""
TOKEN      = os.getenv("TELEGRAM_BOT_TOKEN")
WH_SECRET  = os.getenv("WEBHOOK_SECRET", "spades2026bot")
DOMAIN     = os.getenv("WEBHOOK_DOMAIN", "diymode.work")
WEBHOOK_URL = f"https://{DOMAIN}/webhook/{WH_SECRET}"

# ─── Dedicated async loop (chạy song song với Flask) ────────────────────────
_loop = asyncio.new_event_loop()
threading.Thread(target=_loop.run_forever, daemon=True).start()

# ─── PTB Application ────────────────────────────────────────────────────────
ptb_app = Application.builder().token(TOKEN).build()


# ─── Helpers ────────────────────────────────────────────────────────────────

async def run_cmd(cmd: list[str]) -> tuple[str, str, int]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(BASE_DIR),
        env={**os.environ},
    )
    stdout, stderr = await proc.communicate()
    return (
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
        proc.returncode,
    )


def extract_slug(text: str) -> str | None:
    m = re.search(r"[Ss]lug\s*[:\[]\s*(\S+?)[\]\s]", text) or \
        re.search(r"[Ss]lug\s*:\s*(\S+)", text)
    return m.group(1).strip() if m else None


def extract_cost(text: str) -> str:
    m = re.search(r"Total cost.*", text)
    return m.group() if m else ""


async def send_long(update: Update, text: str):
    for i in range(0, len(text), 4000):
        await update.message.reply_text(text[i : i + 4000])


def parse_story_titles(content: str) -> list[tuple[int, str, str]]:
    """Trả về list (index, title, domain) từ story file."""
    # Chỉ đọc phần evaluate (sau separator ====) nếu có
    sep = "=" * 60
    if sep in content:
        parts = content.split(sep)
        content = parts[-1]  # Lấy phần cuối = evaluate output

    results = []
    seen_titles = set()
    blocks = re.split(r'(?=STORY\s*#\d+)', content)
    for block in blocks:
        m_idx = re.search(r'STORY\s*#(\d+)', block)
        if not m_idx:
            continue
        idx = int(m_idx.group(1))
        title = ""
        domain = ""
        m_title = re.search(r'Tiêu đề\s*[:：]\s*([^\n]+)', block)
        if m_title:
            title = m_title.group(1).strip()[:80]
        m_domain = re.search(r'DOMAIN\s*[:：]\s*([^\n]+)', block)
        if m_domain:
            domain = m_domain.group(1).strip()[:40]
        if (title or domain) and title not in seen_titles:
            seen_titles.add(title)
            results.append((idx, title or f"Story #{idx}", domain))
    return results


async def show_story_picker(update: Update, slug: str, step: str) -> bool:
    """Hiển thị danh sách stories để user chọn. Trả về True nếu cần chờ chọn."""
    story_files = [f for f in sorted(BASE_DIR.glob(f"outputs/stories/{slug}*.md"))
                   if "_brief" not in f.stem]
    if not story_files:
        await update.message.reply_text(f"Không tìm thấy story cho: `{slug}`", parse_mode="Markdown")
        return False

    content = story_files[0].read_text(encoding="utf-8")
    stories = parse_story_titles(content)

    if len(stories) <= 1:
        return False  # Chỉ 1 story → không cần chọn

    chat_id = update.message.chat_id
    _pending_story[chat_id] = {"slug": slug, "step": step, "count": len(stories)}

    lines = [f"File có *{len(stories)} stories* — chọn story nào?\n"]
    for idx, title, domain in stories:
        lines.append(f"*{idx}.* [{domain}] {title}")
    lines.append(f"\nReply số (*1*–*{len(stories)}*) để chọn.")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    return True


async def send_content(update: Update, content: str, filename: str):
    txt_name = Path(filename).stem + ".txt"
    bio = BytesIO(content.encode("utf-8"))
    bio.name = txt_name
    await update.message.reply_document(bio, filename=txt_name)


def latest_file(pattern: str) -> Path | None:
    files = sorted(BASE_DIR.glob(pattern), key=lambda f: f.stat().st_mtime, reverse=True)
    return files[0] if files else None


# ─── Command handlers ────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "*Spades Content Bot* 🃏\n\n"
        "/template `<topic>` — Sinh 3 angle, chọn rồi scan\n"
        "/scan `<topic>` — Scan story trực tiếp\n"
        "/brief `<slug>` — Tạo marketing brief\n"
        "/write `<slug>` — Viết bài từ story\n"
        "/review `<slug>` — Review + chấm điểm bài\n"
        "/factcheck `<slug>` — Kiểm tra facts\n"
        "/generate `<topic>` — Full pipeline tự động\n"
        "/guided `<topic>` — Full pipeline từng bước\n"
        "/list — Xem files gần nhất\n"
        "/get `<slug>` — Lấy file theo slug",
        parse_mode="Markdown",
    )


async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topic = " ".join(context.args) if context.args else ""
    if not topic:
        await update.message.reply_text("Usage: /scan <topic>")
        return

    await update.message.reply_text(f"Đang scan story: *{topic}*...", parse_mode="Markdown")
    stdout, stderr, code = await run_cmd(
        [sys.executable, "pipeline.py", "--step", "scan", "--topic", topic]
    )

    if code != 0:
        await update.message.reply_text(f"Lỗi:\n{stderr[-2000:] or stdout[-2000:]}")
        return

    slug = extract_slug(stdout)
    if slug:
        f = latest_file(f"outputs/stories/{slug}*.md")
        if f:
            await send_content(update, f.read_text(encoding="utf-8"), f.name)
        await update.message.reply_text(
            f"Slug: `{slug}`\n\nBước tiếp:\n"
            f"• /write `{slug}`\n• /brief `{slug}`",
            parse_mode="Markdown",
        )
    else:
        await send_long(update, stdout[-3000:])


async def run_brief(update: Update, slug: str, story_num: str):
    await update.message.reply_text(f"Đang tạo brief: *{slug}* story #{story_num}...", parse_mode="Markdown")
    stdout, stderr, code = await run_cmd(
        [sys.executable, "pipeline.py", "--step", "brief", slug, "--story", story_num]
    )
    if code != 0:
        await update.message.reply_text(f"Lỗi:\n{stderr[-2000:] or stdout[-2000:]}")
        return
    brief = latest_file(f"outputs/stories/{slug}*_brief*.md")
    if brief:
        await send_content(update, brief.read_text(encoding="utf-8"), brief.name)
        await update.message.reply_text(f"Dùng /write `{slug}` để viết bài", parse_mode="Markdown")
    else:
        await send_long(update, stdout[-3000:])


async def run_write(update: Update, slug: str, story_num: str):
    story_files = [f for f in sorted(BASE_DIR.glob(f"outputs/stories/{slug}*.md")) if "_brief" not in f.stem]
    await update.message.reply_text(f"Đang viết bài: *{slug}* story #{story_num}...", parse_mode="Markdown")
    stdout, stderr, code = await run_cmd(
        [sys.executable, "pipeline.py", "--step", "write", slug, "--story", story_num, "--auto"]
    )
    if code != 0:
        await update.message.reply_text(f"Lỗi:\n{stderr[-2000:] or stdout[-2000:]}")
        return
    post = latest_file(f"outputs/posts/{slug}*.md")
    if post:
        await send_content(update, post.read_text(encoding="utf-8"), post.name)
        await update.message.reply_text(
            f"Bước tiếp:\n• /review `{slug}`\n• /factcheck `{slug}`",
            parse_mode="Markdown",
        )
    else:
        await send_long(update, stdout[-3000:])


async def cmd_brief(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /brief <slug>")
        return

    slug = context.args[0]
    needs_pick = await show_story_picker(update, slug, "brief")
    if needs_pick:
        return  # Chờ user reply số

    await run_brief(update, slug, "1")

    if code != 0:
        await update.message.reply_text(f"Lỗi:\n{stderr[-2000:] or stdout[-2000:]}")
        return

    brief = latest_file(f"outputs/stories/{slug}*_brief*.md")
    if brief:
        await send_content(update, brief.read_text(encoding="utf-8"), brief.name)
        await update.message.reply_text(f"Dùng /write `{slug}` để viết bài", parse_mode="Markdown")
    else:
        await send_long(update, stdout[-3000:])


async def cmd_write(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /write <slug> [story_num]")
        return

    slug = context.args[0]
    # Nếu user truyền số trực tiếp → dùng luôn
    if len(context.args) > 1:
        await run_write(update, slug, context.args[1])
        return

    needs_pick = await show_story_picker(update, slug, "write")
    if needs_pick:
        return

    await run_write(update, slug, "1")


async def cmd_review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /review <slug>")
        return

    slug = context.args[0]
    post = latest_file(f"outputs/posts/{slug}*.md")
    if not post:
        await update.message.reply_text(f"Không tìm thấy post cho: `{slug}`", parse_mode="Markdown")
        return

    await update.message.reply_text(f"Đang review: *{slug}*...", parse_mode="Markdown")
    stdout, stderr, code = await run_cmd(
        [sys.executable, "pipeline.py", "--step", "review", slug]
    )

    if code != 0:
        await update.message.reply_text(f"Lỗi:\n{stderr[-2000:] or stdout[-2000:]}")
        return

    review = latest_file(f"outputs/checks/{slug}*_review.md")
    if review:
        await send_content(update, review.read_text(encoding="utf-8"), review.name)
    else:
        await send_long(update, stdout[-3000:])


async def cmd_factcheck(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /factcheck <slug>")
        return

    slug = context.args[0]
    post = latest_file(f"outputs/posts/{slug}*.md")
    if not post:
        await update.message.reply_text(f"Không tìm thấy post cho: `{slug}`", parse_mode="Markdown")
        return

    await update.message.reply_text(f"Đang fact-check: *{slug}*...", parse_mode="Markdown")
    stdout, stderr, code = await run_cmd(
        [sys.executable, "pipeline.py", "--step", "factcheck", slug]
    )

    if code != 0:
        await update.message.reply_text(f"Lỗi:\n{stderr[-2000:] or stdout[-2000:]}")
        return

    check = latest_file(f"outputs/checks/{slug}*_check.md")
    if check:
        await send_content(update, check.read_text(encoding="utf-8"), check.name)
    else:
        await send_long(update, stdout[-3000:])


async def cmd_generate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topic = " ".join(context.args) if context.args else ""
    if not topic:
        await update.message.reply_text("Usage: /generate <topic>")
        return

    await update.message.reply_text(
        f"Full pipeline: *{topic}*\nSẽ mất ~5-10 phút, mình báo khi xong...",
        parse_mode="Markdown",
    )
    stdout, stderr, code = await run_cmd(
        [sys.executable, "pipeline.py", "--pipeline", topic, "--auto"]
    )

    if code != 0:
        await update.message.reply_text(f"Lỗi:\n{stderr[-2000:] or stdout[-2000:]}")
        return

    slug = extract_slug(stdout)
    cost = extract_cost(stdout)

    if slug:
        post = latest_file(f"outputs/posts/{slug}*.md")
        if post:
            await send_content(update, post.read_text(encoding="utf-8"), post.name)

        review = latest_file(f"outputs/checks/{slug}*_review.md")
        if review:
            await send_content(update, review.read_text(encoding="utf-8"), f"{slug}_review.txt")

        summary = f"Pipeline xong! Slug: `{slug}`"
        if cost:
            summary += f"\n{cost}"
        await update.message.reply_text(summary, parse_mode="Markdown")
    else:
        await send_long(update, stdout[-3000:])


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    posts = sorted(BASE_DIR.glob("outputs/posts/*.md"), key=lambda f: f.stat().st_mtime, reverse=True)
    stories = [
        f for f in sorted(BASE_DIR.glob("outputs/stories/*.md"), key=lambda f: f.stat().st_mtime, reverse=True)
        if "_brief" not in f.stem
    ]

    async def send_md_chunks(lines: list[str]):
        chunk, size = [], 0
        for line in lines:
            if size + len(line) > 3800:
                await update.message.reply_text("\n".join(chunk), parse_mode="Markdown")
                chunk, size = [], 0
            chunk.append(line)
            size += len(line)
        if chunk:
            await update.message.reply_text("\n".join(chunk), parse_mode="Markdown")

    if posts:
        await send_md_chunks(["*Posts:*"] + [f"• `{f.stem}`" for f in posts])
    else:
        await update.message.reply_text("Chưa có post nào.")

    if stories:
        await send_md_chunks(["*Stories:*"] + [f"• `{f.stem}`" for f in stories])
    else:
        await update.message.reply_text("Chưa có story nào.")

    await update.message.reply_text("Dùng /get `<slug>` để lấy file", parse_mode="Markdown")


async def cmd_get(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /get <slug>")
        return

    slug = context.args[0]
    for pattern in [
        f"outputs/posts/{slug}*.md",
        f"outputs/stories/{slug}*.md",
        f"outputs/checks/{slug}*.md",
    ]:
        f = latest_file(pattern)
        if f:
            await send_content(update, f.read_text(encoding="utf-8"), f.name)
            return

    await update.message.reply_text(f"Không tìm thấy file cho: `{slug}`", parse_mode="Markdown")


async def cmd_template(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topic = " ".join(context.args) if context.args else ""
    if not topic:
        await update.message.reply_text("Usage: /template <topic>\nVí dụ: /template variance tâm lý")
        return

    await update.message.reply_text(f"Đang sinh 3 angle templates cho: *{topic}*...", parse_mode="Markdown")
    stdout, stderr, code = await run_cmd(
        [sys.executable, "pipeline.py", "--step", "template", "--topic", topic]
    )

    if code != 0:
        await update.message.reply_text(f"Lỗi:\n{stderr[-2000:] or stdout[-2000:]}")
        return

    m = re.search(r'__TEMPLATES__\s*(.*?)\s*__END_TEMPLATES__', stdout, re.DOTALL)
    if not m:
        await update.message.reply_text("Không parse được templates.")
        return

    try:
        data = _json.loads(m.group(1))
        templates = data.get("templates", [])
    except Exception:
        await update.message.reply_text("Lỗi parse JSON templates.")
        return

    if not templates:
        await update.message.reply_text("Không sinh được template nào.")
        return

    chat_id = update.message.chat_id
    _pending_templates[chat_id] = {"topic": topic, "templates": templates}

    # Gửi toàn bộ templates dưới dạng file txt để đọc đầy đủ
    full_text = f"TEMPLATES cho: {topic}\n{'='*60}\n\n"
    for i, t in enumerate(templates, 1):
        full_text += f"{'='*60}\nTEMPLATE {i}\n{'='*60}\n{t}\n\n"
    full_text += "Reply 1, 2, hoặc 3 để chọn → bot scan ngay."

    bio = BytesIO(full_text.encode("utf-8"))
    bio.name = f"templates_{topic[:30].replace(' ', '_')}.txt"
    await update.message.reply_document(bio, filename=bio.name)
    await update.message.reply_text(
        f"Xem file trên → Reply *1*, *2*, hoặc *3* để chọn angle.",
        parse_mode="Markdown",
    )


async def cmd_guided(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topic = " ".join(context.args) if context.args else ""
    if not topic:
        await update.message.reply_text("Usage: /guided <topic>\nVí dụ: /guided tư duy logic")
        return

    chat_id = update.message.chat_id
    _pending_guided[chat_id] = {
        "state": "anchor",
        "topic": topic,
        "messages": [{"role": "user", "content": f"Topic: {topic}"}],
        "anchor": None,
        "slug": None,
    }

    await update.message.reply_text(f"Guided mode: *{topic}*\nĐang nghĩ câu hỏi...", parse_mode="Markdown")

    loop = asyncio.get_event_loop()
    session = _pending_guided[chat_id]
    ai_text = await loop.run_in_executor(
        None,
        lambda: _anthropic_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=500,
            system=_ANCHOR_CHAT_SYSTEM,
            messages=session["messages"],
        ).content[0].text.strip()
    )
    session["messages"].append({"role": "assistant", "content": ai_text})

    anchor_match = re.search(r'<ANCHOR>(.*?)</ANCHOR>', ai_text, re.DOTALL)
    if anchor_match:
        session["anchor"] = anchor_match.group(1).strip()
        session["state"] = "scan"
        outside = re.sub(r'<ANCHOR>.*?</ANCHOR>', '', ai_text, flags=re.DOTALL).strip()
        if outside:
            await update.message.reply_text(outside)
        await _guided_run_scan(update, session)
        return

    outside = re.sub(r'<ANCHOR>.*?</ANCHOR>', '', ai_text, flags=re.DOTALL).strip()
    await update.message.reply_text(outside or ai_text)


def _parse_strategy_fields(text: str) -> dict:
    """Parse <STRATEGY> hoặc <ANCHOR> block thành dict để truyền xuống scanner."""
    fields = {}
    # Thử <STRATEGY> trước
    m = re.search(r'<STRATEGY>(.*?)</STRATEGY>', text, re.DOTALL)
    if not m:
        m = re.search(r'<ANCHOR>(.*?)</ANCHOR>', text, re.DOTALL)
    if not m:
        return fields
    for line in m.group(1).splitlines():
        if ':' in line:
            k, _, v = line.partition(':')
            key = k.strip().upper().replace(' ', '_').replace('Ê', 'E').replace('Ô', 'O')
            fields[key] = v.strip()
    # Normalize keys từ ANCHOR format sang STRATEGY format
    if 'STORY_PATTERN' not in fields and 'STORY_PATTERN_' in str(fields):
        pass
    if 'KHI_A_C_NH' in fields:  # Khía cạnh
        fields['CONCEPT'] = fields['KHI_A_C_NH']
    return fields


async def _guided_run_scan(update: Update, session: dict):
    topic = session["topic"]
    anchor = session["anchor"]

    # Parse strategy fields từ anchor để scanner dùng pattern scan
    strategy_fields = _parse_strategy_fields(anchor) if anchor else {}

    # Xây query với các fields rõ ràng để run_scanner tự parse được
    if strategy_fields.get("STORY_PATTERN"):
        parts = [topic]
        for key, label in [
            ("STORY_PATTERN", "STORY PATTERN cần tìm"),
            ("CONCEPT", "CONCEPT cần bridge"),
            ("CHI_U_SAI", "CHIỀU SAI"),
            ("CHI_U__NG", "CHIỀU ĐÚNG"),
        ]:
            val = strategy_fields.get(key, "")
            if val:
                parts.append(f"{label}: {val}")
        full_query = "\n".join(parts)
    else:
        full_query = f"{topic}\n\nANCHOR:\n{anchor}" if anchor and anchor != topic else topic

    await update.message.reply_text("Đang scan story (~2-3 phút)...")
    stdout, stderr, code = await run_cmd(
        [sys.executable, "pipeline.py", "--step", "scan", "--topic", full_query]
    )

    if code != 0:
        await update.message.reply_text(f"Scan lỗi:\n{stderr[-2000:] or stdout[-2000:]}")
        chat_id = update.message.chat_id
        _pending_guided.pop(chat_id, None)
        return

    slug = extract_slug(stdout)
    if not slug:
        await send_long(update, stdout[-3000:])
        chat_id = update.message.chat_id
        _pending_guided.pop(chat_id, None)
        return

    session["slug"] = slug
    session["state"] = "story_pick"

    story_file = latest_file(f"outputs/stories/{slug}*.md")
    if story_file:
        await send_content(update, story_file.read_text(encoding="utf-8"), story_file.name)

    stories = parse_story_titles(story_file.read_text(encoding="utf-8") if story_file else "")
    if stories:
        lines = ["*Chọn story để viết bài:*\n"]
        for idx, title, domain in stories:
            lines.append(f"*{idx}.* [{domain}] {title}")
        lines.append(f"\nReply *1* hoặc *{len(stories)}* để chọn.")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    else:
        await update.message.reply_text("Reply *1* hoặc *2* để chọn story.", parse_mode="Markdown")


async def handle_template_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    text = update.message.text.strip()

    # Xử lý guided session
    if chat_id in _pending_guided:
        session = _pending_guided[chat_id]
        state = session["state"]

        if state == "anchor":
            session["messages"].append({"role": "user", "content": text})
            loop = asyncio.get_event_loop()
            ai_text = await loop.run_in_executor(
                None,
                lambda: _anthropic_client.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=500,
                    system=_ANCHOR_CHAT_SYSTEM,
                    messages=session["messages"],
                ).content[0].text.strip()
            )
            session["messages"].append({"role": "assistant", "content": ai_text})

            anchor_match = re.search(r'<ANCHOR>(.*?)</ANCHOR>', ai_text, re.DOTALL)
            if anchor_match:
                session["anchor"] = anchor_match.group(1).strip()
                outside = re.sub(r'<ANCHOR>.*?</ANCHOR>', '', ai_text, flags=re.DOTALL).strip()
                if outside:
                    await update.message.reply_text(outside)
                anchor_preview = "\n".join(
                    f"• {l.strip()}" for l in session["anchor"].splitlines() if l.strip()
                )
                await update.message.reply_text(
                    f"*Anchor đã rõ:*\n{anchor_preview}\n\nReply *Y* để scan, hoặc tiếp tục chỉnh.",
                    parse_mode="Markdown",
                )
                session["state"] = "anchor_confirm"
            else:
                outside = re.sub(r'<ANCHOR>.*?</ANCHOR>', '', ai_text, flags=re.DOTALL).strip()
                await update.message.reply_text(outside or ai_text)

            if len(session["messages"]) >= 12:
                await update.message.reply_text("Đủ rồi — chạy scan với anchor hiện tại.")
                session["anchor"] = session["topic"]
                session["state"] = "scan"
                await _guided_run_scan(update, session)
            return

        if state == "anchor_confirm":
            if text.lower() in ("y", "yes", "ok", "đồng ý", "được", "oke"):
                session["state"] = "scan"
                await _guided_run_scan(update, session)
            else:
                session["messages"].append({"role": "user", "content": text})
                session["state"] = "anchor"
                loop = asyncio.get_event_loop()
                ai_text = await loop.run_in_executor(
                    None,
                    lambda: _anthropic_client.messages.create(
                        model="claude-sonnet-4-6",
                        max_tokens=500,
                        system=_ANCHOR_CHAT_SYSTEM,
                        messages=session["messages"],
                    ).content[0].text.strip()
                )
                session["messages"].append({"role": "assistant", "content": ai_text})
                await update.message.reply_text(ai_text)
            return

        if state == "story_pick":
            if not text.isdigit():
                await update.message.reply_text("Reply số (1 hoặc 2) để chọn story.")
                return
            session["story_num"] = text
            session["state"] = "brief"
            slug = session["slug"]
            await update.message.reply_text(f"Đã chọn story #{text} — đang tạo brief...")
            story_file = latest_file(f"outputs/stories/{slug}*.md")
            stdout, stderr, code = await run_cmd([
                sys.executable, "pipeline.py", "--step", "brief",
                str(story_file), "--story", text, "--auto",
            ])
            if code != 0:
                await update.message.reply_text(f"Brief lỗi:\n{stderr[-2000:] or stdout[-2000:]}")
                _pending_guided.pop(chat_id, None)
                return
            brief_file = latest_file(f"outputs/stories/{slug}*_brief*.md")
            if brief_file:
                await send_content(update, brief_file.read_text(encoding="utf-8"), brief_file.name)
            session["state"] = "brief_confirm"
            await update.message.reply_text(
                "Brief xong. Reply *Y* để viết bài, hoặc /cancel để dừng.",
                parse_mode="Markdown",
            )
            return

        if state == "brief_confirm":
            if text.lower() in ("y", "yes", "ok", "đồng ý", "được", "oke"):
                slug = session["slug"]
                story_num = session.get("story_num", "1")
                _pending_guided.pop(chat_id, None)
                story_file = latest_file(f"outputs/stories/{slug}*.md")
                await run_write(update, slug, story_num)
            else:
                await update.message.reply_text("Reply *Y* để viết bài, hoặc /cancel để huỷ.", parse_mode="Markdown")
            return

    # Xử lý chọn story (brief/write)
    if chat_id in _pending_story:
        if not text.isdigit():
            await update.message.reply_text("Nhập số để chọn story.")
            return
        state = _pending_story.pop(chat_id)
        slug = state["slug"]
        step = state["step"]
        if step == "brief":
            await run_brief(update, slug, text)
        else:
            await run_write(update, slug, text)
        return

    # Xử lý chọn template (scan)
    if chat_id not in _pending_templates:
        return

    if text not in ("1", "2", "3"):
        await update.message.reply_text("Nhập 1, 2, hoặc 3 để chọn template.")
        return

    state = _pending_templates.pop(chat_id)
    templates = state["templates"]
    idx = int(text) - 1

    if idx >= len(templates):
        await update.message.reply_text(f"Chỉ có {len(templates)} template.")
        return

    chosen = templates[idx]
    await update.message.reply_text(f"Đã chọn template {text}. Đang scan story...")

    stdout, stderr, code = await run_cmd(
        [sys.executable, "pipeline.py", "--step", "scan", "--topic", chosen]
    )

    if code != 0:
        await update.message.reply_text(f"Lỗi:\n{stderr[-2000:] or stdout[-2000:]}")
        return

    slug = extract_slug(stdout)
    if slug:
        f = latest_file(f"outputs/stories/{slug}*.md")
        if f:
            await send_content(update, f.read_text(encoding="utf-8"), f.name)
        await update.message.reply_text(
            f"Slug: `{slug}`\n\nBước tiếp:\n• /brief `{slug}`\n• /write `{slug}`",
            parse_mode="Markdown",
        )
    else:
        await send_long(update, stdout[-3000:])


# ─── Register handlers ───────────────────────────────────────────────────────

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    _pending_guided.pop(chat_id, None)
    _pending_templates.pop(chat_id, None)
    _pending_story.pop(chat_id, None)
    await update.message.reply_text("Đã huỷ.")

ptb_app.add_handler(CommandHandler("start",     cmd_start))
ptb_app.add_handler(CommandHandler("help",      cmd_start))
ptb_app.add_handler(CommandHandler("cancel",    cmd_cancel))
ptb_app.add_handler(CommandHandler("guided",    cmd_guided))
ptb_app.add_handler(CommandHandler("scan",      cmd_scan))
ptb_app.add_handler(CommandHandler("brief",     cmd_brief))
ptb_app.add_handler(CommandHandler("write",     cmd_write))
ptb_app.add_handler(CommandHandler("review",    cmd_review))
ptb_app.add_handler(CommandHandler("factcheck", cmd_factcheck))
ptb_app.add_handler(CommandHandler("template",  cmd_template))
ptb_app.add_handler(CommandHandler("generate",  cmd_generate))
ptb_app.add_handler(CommandHandler("list",      cmd_list))
ptb_app.add_handler(CommandHandler("get",       cmd_get))
ptb_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_template_choice))

# Initialize PTB
asyncio.run_coroutine_threadsafe(ptb_app.initialize(), _loop).result(timeout=10)


# ─── Flask app ───────────────────────────────────────────────────────────────

flask_app = Flask(__name__)


@flask_app.route("/")
def index():
    return "Spades Bot Running ✓", 200


@flask_app.route("/setup")
def setup_webhook():
    """Truy cập 1 lần để đăng ký webhook với Telegram."""
    future = asyncio.run_coroutine_threadsafe(
        ptb_app.bot.set_webhook(url=WEBHOOK_URL),
        _loop,
    )
    ok = future.result(timeout=15)
    return jsonify({"ok": ok, "webhook_url": WEBHOOK_URL})


@flask_app.route(f"/webhook/{WH_SECRET}", methods=["POST"])
def webhook():
    data = request.get_json(force=True, silent=True)
    if not data:
        return "bad request", 400
    update = Update.de_json(data, ptb_app.bot)
    asyncio.run_coroutine_threadsafe(ptb_app.process_update(update), _loop)
    return "ok", 200


if __name__ == "__main__":
    flask_app.run(host="0.0.0.0", port=8080, debug=False)
