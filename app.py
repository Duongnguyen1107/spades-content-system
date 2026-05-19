#!/usr/bin/env python3
"""
Spades Content Bot v2 — Webhook mode
Architecture: Strategist orchestrator → subagent writers
Deploy: CloudPanel Python Site tại diymode.work
"""

import asyncio
import os
import re
import sys
import threading
from datetime import datetime
from io import BytesIO
from pathlib import Path

import anthropic as _anthropic_lib
from openai import OpenAI as _openai_lib
from dotenv import load_dotenv
from flask import Flask, request, jsonify
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

load_dotenv()

from pipeline import run_scanner as _pipeline_run_scanner, make_slug as _pipeline_make_slug

BASE_DIR = Path(__file__).parent

_anthropic_client = _anthropic_lib.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
_openai_client    = _openai_lib(api_key=os.environ.get("OPENAI_API_KEY"))

TOKEN     = os.getenv("TELEGRAM_BOT_TOKEN")
WH_SECRET = os.getenv("WEBHOOK_SECRET", "spades2026bot")
DOMAIN    = os.getenv("WEBHOOK_DOMAIN", "diymode.work")
WEBHOOK_URL = f"https://{DOMAIN}/webhook/{WH_SECRET}"

# ─── Session management ──────────────────────────────────────────────────────

_sessions: dict[int, dict] = {}
_update_pending: set[int] = {}  # chat_ids đang chờ paste brand update

def new_session() -> dict:
    return {
        "messages":    [],      # conversation history với strategist
        "state":       "chatting",
        "brief":       None,    # brief content khi strategist output
        "writer":      None,    # "spades-story-writer" | "spades-copywriter" | "spades-advertorial"
        "slug":        None,    # slug sau khi scan story
        "story_num":   "1",
        "post_slug":   None,    # slug của bài viết vừa xuất
    }

def get_session(chat_id: int) -> dict:
    if chat_id not in _sessions:
        _sessions[chat_id] = new_session()
    return _sessions[chat_id]


# ─── Agent helpers ───────────────────────────────────────────────────────────

_BRAND_AGENTS = {"spades-strategist", "spades-story-writer", "spades-copywriter", "spades-advertorial"}

def _load_agent(name: str) -> str:
    path = BASE_DIR / "agents" / f"{name}.md"
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        parts = text.split("---", 2)
        text = parts[2].strip() if len(parts) > 2 else text

    if name in _BRAND_AGENTS:
        brand_path = BASE_DIR / "brand-context.md"
        if brand_path.exists():
            brand = brand_path.read_text(encoding="utf-8")
            text = f"{brand}\n\n---\n\n{text}"
    return text

SONNET     = "claude-sonnet-4-6"
HAIKU      = "claude-haiku-4-5-20251001"
GPT41MINI  = "gpt-4.1-mini"

def _call_agent_sync(system: str, messages: list, max_tokens: int = 1500, model: str = SONNET) -> str:
    return _anthropic_client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=messages,
    ).content[0].text

def _call_openai_sync(system: str, messages: list, max_tokens: int = 3000, model: str = GPT41MINI) -> str:
    response = _openai_client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "system", "content": system}] + messages,
    )
    return response.choices[0].message.content

def _detect_brief_type(text: str) -> str | None:
    for writer in ["spades-story-writer", "spades-copywriter", "spades-advertorial"]:
        if f"BRIEF → {writer}" in text:
            return writer
    return None

def _needs_story_scan(brief: str) -> bool:
    return "TÌM STORY:" in brief

def _extract_scan_topic(brief: str, fallback: str) -> str:
    m = re.search(r"TÌM STORY:\s*(.+?)(?:\n|$)", brief)
    return m.group(1).strip() if m else fallback


# ─── Helpers ─────────────────────────────────────────────────────────────────

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

def latest_file(pattern: str) -> Path | None:
    files = sorted(BASE_DIR.glob(pattern), key=lambda f: f.stat().st_mtime, reverse=True)
    return files[0] if files else None

async def send_long(update: Update, text: str):
    for i in range(0, len(text), 4000):
        await update.message.reply_text(text[i:i + 4000])

async def send_file(update: Update, content: str, filename: str):
    txt_name = Path(filename).stem + ".txt"
    bio = BytesIO(content.encode("utf-8"))
    bio.name = txt_name
    await update.message.reply_document(bio, filename=txt_name)

def parse_story_titles(content: str) -> list[tuple[int, str, str]]:
    sep = "=" * 60
    if sep in content:
        content = content.split(sep)[-1]
    results, seen_idx = [], set()
    for block in re.split(r'(?=STORY\s*#\d+)', content):
        m_idx = re.search(r'STORY\s*#(\d+)', block)
        if not m_idx:
            continue
        idx = int(m_idx.group(1))
        if idx in seen_idx:
            continue
        seen_idx.add(idx)
        m_title = re.search(r'Tiêu đề\s*[:：]\s*([^\n]+)', block)
        m_domain = re.search(r'DOMAIN\s*[:：]\s*([^\n]+)', block)
        title = m_title.group(1).strip()[:80] if m_title else f"Story #{idx}"
        domain = m_domain.group(1).strip()[:40] if m_domain else ""
        results.append((idx, title, domain))
    return results

def _strip_checklist(text: str) -> str:
    """Chỉ giữ PHẦN 1 — bài viết, bỏ checklist verify."""
    for marker in ["PHẦN 2", "CHECKLIST VERIFY", "---\nPHẦN 2"]:
        if marker in text:
            return text.split(marker)[0].strip()
    return text

def _extract_story_block(content: str, story_num: str) -> str:
    """Trích story block theo số từ scanner output. Fallback toàn bộ nếu không tìm được."""
    blocks = re.split(r'(?=STORY\s*#\d+)', content)
    for block in blocks:
        if re.match(rf'\s*STORY\s*#{re.escape(story_num)}\b', block):
            return block.strip()
    return content


# ─── Writer subagent ─────────────────────────────────────────────────────────

async def _run_writer(update: Update, session: dict):
    """Gọi writer agent phù hợp, lưu file, gửi kết quả."""
    writer = session["writer"]
    brief  = session["brief"]

    await update.message.reply_text("Đang viết bài...")

    # Với Story Writer: truyền thêm story content nếu có
    user_content = f"Brief:\n\n{brief}"
    if writer == "spades-story-writer" and session.get("slug"):
        slug = session["slug"]
        story_file = latest_file(f"outputs/stories/{slug}*.md")
        if story_file:
            story_text = story_file.read_text(encoding="utf-8")
            story_num = session.get("story_num", "1")
            selected_story = _extract_story_block(story_text, story_num)
            user_content = f"Story:\n\n{selected_story}\n\nBrief:\n\n{brief}"

    writer_system = _load_agent(writer)
    loop = asyncio.get_event_loop()

    if writer == "spades-story-writer":
        result = await loop.run_in_executor(
            None,
            lambda: _call_openai_sync(writer_system, [{"role": "user", "content": user_content}], max_tokens=3000)
        )
    else:
        result = await loop.run_in_executor(
            None,
            lambda: _call_agent_sync(writer_system, [{"role": "user", "content": user_content}], max_tokens=3000)
        )

    # Xóa em-dash
    result = result.replace(" — ", ", ").replace("— ", ", ").replace(" —", ",")

    # Lưu file
    first_msg = session["messages"][0]["content"][:30] if session["messages"] else "post"
    safe = "".join(c if c.isalnum() or c in "_-" else "_" for c in first_msg).strip("_")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    post_path = BASE_DIR / "outputs" / "posts" / f"{safe}_{timestamp}.md"
    post_path.parent.mkdir(parents=True, exist_ok=True)
    post_path.write_text(result, encoding="utf-8")
    session["post_slug"] = post_path.stem

    # Gửi file đầy đủ + bài viết sạch
    await send_file(update, result, post_path.name)
    article = _strip_checklist(result)
    await send_long(update, article)

    session["state"] = "post_article"

    # Auto fact-check sau Story Writing vì luôn có claim cụ thể
    if writer == "spades-story-writer":
        await update.message.reply_text("Đang tự động fact-check bài...")
        stdout, stderr, code = await run_cmd(
            [sys.executable, "pipeline.py", "--step", "factcheck", post_path.stem]
        )
        if code == 0:
            check = latest_file(f"outputs/checks/{post_path.stem}*_check.md")
            if check:
                await send_file(update, check.read_text(encoding="utf-8"), check.name)
                await send_long(update, check.read_text(encoding="utf-8")[:3000])
        else:
            await update.message.reply_text(f"Fact-check lỗi:\n{stderr[-1000:] or stdout[-1000:]}")

    await update.message.reply_text(
        "Bài xong ✓\n\n"
        "1 — Chỉnh nhỏ (nói chỗ cần sửa)\n"
        "2 — Viết lại (giữ brief, chạy lại)\n"
        "3 — Bài mới\n"
        "4 — Fact check\n"
        "5 — Review bài"
    )


# ─── Main content flow ────────────────────────────────────────────────────────

async def handle_content_message(update: Update, text: str):
    chat_id = update.message.chat_id
    session = get_session(chat_id)
    state   = session["state"]
    loop    = asyncio.get_event_loop()

    strategist_system = _load_agent("spades-strategist")

    # ── CHATTING: trả lời với strategist ──────────────────────────────────────
    if state == "chatting":
        session["messages"].append({"role": "user", "content": text})

        response = await loop.run_in_executor(
            None,
            lambda: _call_agent_sync(strategist_system, session["messages"], max_tokens=2500)
        )
        session["messages"].append({"role": "assistant", "content": response})

        writer = _detect_brief_type(response)
        if writer:
            session["brief"]  = response
            session["writer"] = writer
            session["state"]  = "brief_ready"
            await send_long(update, response)
            await update.message.reply_text("Brief xong ✓\nReply *Y* để viết bài, hoặc tiếp tục chỉnh.", parse_mode="Markdown")
        else:
            await send_long(update, response)

    # ── BRIEF_READY: user confirm hoặc chỉnh ─────────────────────────────────
    elif state == "brief_ready":
        if text.lower() in ("y", "yes", "ok", "viết", "oke", "được", "go"):
            writer = session["writer"]
            brief  = session["brief"]

            if writer == "spades-story-writer" and _needs_story_scan(brief):
                # Cần tìm story trước
                first_msg = session["messages"][0]["content"] if session["messages"] else text
                topic = _extract_scan_topic(brief, first_msg)

                await update.message.reply_text(f"Đang tìm story cho: *{topic[:60]}*...", parse_mode="Markdown")
                try:
                    story_text = await loop.run_in_executor(
                        None, lambda: _pipeline_run_scanner(topic)
                    )
                except Exception as e:
                    await update.message.reply_text(f"Scan lỗi: {e}")
                    return

                slug = _pipeline_make_slug(topic)
                story_path = BASE_DIR / "outputs" / "stories" / f"{slug}.md"
                story_path.parent.mkdir(parents=True, exist_ok=True)
                story_path.write_text(story_text, encoding="utf-8")

                session["slug"]  = slug
                session["state"] = "story_pick"

                if story_path.exists():
                    await send_file(update, story_path.read_text(encoding="utf-8"), story_path.name)
                    stories = parse_story_titles(story_path.read_text(encoding="utf-8"))
                    if stories:
                        lines = ["*Chọn story để viết bài:*\n"]
                        for idx, title, domain in stories:
                            lines.append(f"*{idx}.* [{domain}] {title}")
                        lines.append(f"\nReply số (*1*–*{len(stories)}*) để chọn.")
                        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
                    else:
                        await update.message.reply_text("Reply *1* để dùng story đầu tiên.", parse_mode="Markdown")
            else:
                # Viết thẳng (Copywriting, Advertorial, hoặc Story Writer đã có story)
                await _run_writer(update, session)
        else:
            # User muốn chỉnh brief — quay về chatting
            session["state"] = "chatting"
            session["messages"].append({"role": "user", "content": text})
            response = await loop.run_in_executor(
                None,
                lambda: _call_agent_sync(strategist_system, session["messages"], max_tokens=2500)
            )
            session["messages"].append({"role": "assistant", "content": response})

            writer = _detect_brief_type(response)
            if writer:
                session["brief"]  = response
                session["writer"] = writer
                session["state"]  = "brief_ready"
                await send_long(update, response)
                await update.message.reply_text("Brief updated ✓\nReply *Y* để viết bài.", parse_mode="Markdown")
            else:
                await send_long(update, response)

    # ── STORY_PICK: user chọn story số mấy ───────────────────────────────────
    elif state == "story_pick":
        if text.strip().isdigit():
            session["story_num"] = text.strip()
            await _run_writer(update, session)
        else:
            await update.message.reply_text("Reply số để chọn story (ví dụ: *1*, *2*).", parse_mode="Markdown")

    # ── POST_ARTICLE: sau khi nhận bài ───────────────────────────────────────
    elif state == "post_article":
        t = text.strip()
        if t == "3" or "bài mới" in t.lower():
            _sessions[chat_id] = new_session()
            await update.message.reply_text("Bắt đầu bài mới — ae muốn viết gì?")

        elif t == "2" or "viết lại" in t.lower():
            await _run_writer(update, session)

        elif t == "1" or "chỉnh" in t.lower():
            session["state"] = "chatting"
            session["messages"].append({"role": "user", "content": f"[Feedback bài vừa viết] {text}"})
            response = await loop.run_in_executor(
                None,
                lambda: _call_agent_sync(strategist_system, session["messages"], max_tokens=2500)
            )
            session["messages"].append({"role": "assistant", "content": response})
            writer = _detect_brief_type(response)
            if writer:
                session["brief"]  = response
                session["writer"] = writer
                session["state"]  = "brief_ready"
                await send_long(update, response)
                await update.message.reply_text("Brief updated ✓\nReply *Y* để viết lại.", parse_mode="Markdown")
            else:
                await send_long(update, response)

        elif t == "4" or "fact check" in t.lower() or "factcheck" in t.lower():
            slug = session.get("post_slug")
            if not slug:
                await update.message.reply_text("Không tìm thấy slug bài vừa viết.")
                return
            await update.message.reply_text(f"Đang fact-check: *{slug}*...", parse_mode="Markdown")
            stdout, stderr, code = await run_cmd([sys.executable, "pipeline.py", "--step", "factcheck", slug])
            if code != 0:
                await update.message.reply_text(f"Lỗi:\n{stderr[-2000:] or stdout[-2000:]}")
                return
            check = latest_file(f"outputs/checks/{slug}*_check.md")
            if check:
                await send_file(update, check.read_text(encoding="utf-8"), check.name)
                await send_long(update, check.read_text(encoding="utf-8")[:3000])
            else:
                await send_long(update, stdout[-3000:])

        elif t == "5" or "review" in t.lower():
            slug = session.get("post_slug")
            if not slug:
                await update.message.reply_text("Không tìm thấy slug bài vừa viết.")
                return
            await update.message.reply_text(f"Đang review: *{slug}*...", parse_mode="Markdown")
            stdout, stderr, code = await run_cmd([sys.executable, "pipeline.py", "--step", "review", slug])
            if code != 0:
                await update.message.reply_text(f"Lỗi:\n{stderr[-2000:] or stdout[-2000:]}")
                return
            rev = latest_file(f"outputs/checks/{slug}*_review.md")
            if rev:
                await send_file(update, rev.read_text(encoding="utf-8"), rev.name)
                await send_long(update, rev.read_text(encoding="utf-8")[:3000])
            else:
                await send_long(update, stdout[-3000:])

        else:
            # Tin nhắn tự do sau bài — coi như bắt đầu bài mới
            _sessions[chat_id] = new_session()
            await handle_content_message(update, text)


# ─── Command handlers ─────────────────────────────────────────────────────────

async def _process_brand_update(update: Update, raw: str):
    brand_path = BASE_DIR / "brand-context.md"
    current = brand_path.read_text(encoding="utf-8") if brand_path.exists() else ""

    system = (
        "Bạn là brand manager cho Spades Board Game Cafe.\n"
        "Nhiệm vụ: nhận thông tin mới từ user → cập nhật vào đúng section của brand-context.md → output toàn bộ file đã cập nhật.\n\n"
        "Sections hiện có: THÔNG TIN QUÁN / GAME MODES / KHUYẾN MÃI & MARKETING / SỰ KIỆN SẮP TỚI\n"
        "Nếu thông tin mới không thuộc section nào → tạo section mới phù hợp.\n"
        "Giữ nguyên thông tin cũ không liên quan đến update.\n"
        "Chỉ output nội dung file markdown, không giải thích thêm."
    )
    user_msg = f"Brand context hiện tại:\n\n{current}\n\nThông tin mới cần cập nhật:\n\n{raw}"

    await update.message.reply_text("Đang cập nhật brand context...")
    loop = asyncio.get_event_loop()
    updated = await loop.run_in_executor(
        None,
        lambda: _call_agent_sync(system, [{"role": "user", "content": user_msg}], max_tokens=2000, model=HAIKU)
    )
    brand_path.write_text(updated, encoding="utf-8")
    await update.message.reply_text(
        "Brand context đã cập nhật ✓\n"
        "Có hiệu lực ngay từ bài viết tiếp theo.\n\n"
        f"Xem lại: /showbrand"
    )

async def cmd_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    if context.args:
        raw = " ".join(context.args)
        await _process_brand_update(update, raw)
    else:
        _update_pending.add(chat_id)
        await update.message.reply_text("Paste thông tin brand mới vào đây:")

async def cmd_showbrand(update: Update, context: ContextTypes.DEFAULT_TYPE):
    brand_path = BASE_DIR / "brand-context.md"
    if brand_path.exists():
        await send_long(update, brand_path.read_text(encoding="utf-8"))
    else:
        await update.message.reply_text("Chưa có brand-context.md.")

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "*Spades Content Bot* 🃏\n\n"
        "Nhắn thẳng topic, story từ quán, hoặc tính năng muốn push — mình lo phần còn lại.\n\n"
        "*Ví dụ:*\n"
        "• _tôi muốn viết về ego trong poker_\n"
        "• _viết bài giới thiệu Ultra X_\n"
        "• _có ông khách tên Hiệp đi xe từ Bảo Lộc xuống..._\n\n"
        "*Tiện ích:*\n"
        "/list — xem files gần nhất\n"
        "/get `<slug>` — lấy file\n"
        "/factcheck `<slug>` — kiểm tra facts\n"
        "/cancel — reset conversation\n\n"
        "*Brand:*\n"
        "/update — cập nhật thông tin quán (game mode, KM, sự kiện...)\n"
        "/showbrand — xem brand context hiện tại",
        parse_mode="Markdown",
    )

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    _sessions.pop(chat_id, None)
    await update.message.reply_text("Đã reset. Ae muốn viết gì mới?")

async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    posts = sorted(BASE_DIR.glob("outputs/posts/*.md"), key=lambda f: f.stat().st_mtime, reverse=True)[:10]
    stories = [f for f in sorted(BASE_DIR.glob("outputs/stories/*.md"), key=lambda f: f.stat().st_mtime, reverse=True)
               if "_brief" not in f.stem][:5]

    lines = []
    if posts:
        lines.append("*Posts gần nhất:*")
        lines += [f"• `{f.stem}`" for f in posts]
    if stories:
        lines.append("\n*Stories:*")
        lines += [f"• `{f.stem}`" for f in stories]
    if not lines:
        lines = ["Chưa có file nào."]

    lines.append("\nDùng /get `<slug>` để lấy file")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def cmd_get(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /get <slug>")
        return
    slug = context.args[0]
    for pattern in [f"outputs/posts/{slug}*.md", f"outputs/stories/{slug}*.md", f"outputs/checks/{slug}*.md"]:
        f = latest_file(pattern)
        if f:
            await send_file(update, f.read_text(encoding="utf-8"), f.name)
            return
    await update.message.reply_text(f"Không tìm thấy file cho: `{slug}`", parse_mode="Markdown")

async def cmd_factcheck(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /factcheck <slug>")
        return
    slug = context.args[0]
    post = latest_file(f"outputs/posts/{slug}*.md")
    if not post:
        await update.message.reply_text(f"Không tìm thấy post: `{slug}`", parse_mode="Markdown")
        return
    await update.message.reply_text(f"Đang fact-check: *{slug}*...", parse_mode="Markdown")
    stdout, stderr, code = await run_cmd([sys.executable, "pipeline.py", "--step", "factcheck", slug])
    if code != 0:
        await update.message.reply_text(f"Lỗi:\n{stderr[-2000:] or stdout[-2000:]}")
        return
    check = latest_file(f"outputs/checks/{slug}*_check.md")
    if check:
        await send_file(update, check.read_text(encoding="utf-8"), check.name)
    else:
        await send_long(update, stdout[-3000:])

async def cmd_review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /review <slug>")
        return
    slug = context.args[0]
    await update.message.reply_text(f"Đang review: *{slug}*...", parse_mode="Markdown")
    stdout, stderr, code = await run_cmd([sys.executable, "pipeline.py", "--step", "review", slug])
    if code != 0:
        await update.message.reply_text(f"Lỗi:\n{stderr[-2000:] or stdout[-2000:]}")
        return
    review = latest_file(f"outputs/checks/{slug}*_review.md")
    if review:
        await send_file(update, review.read_text(encoding="utf-8"), review.name)
    else:
        await send_long(update, stdout[-3000:])

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text:
        return
    chat_id = update.message.chat_id
    if chat_id in _update_pending:
        _update_pending.discard(chat_id)
        await _process_brand_update(update, text)
        return
    await handle_content_message(update, text)


# ─── Register handlers ────────────────────────────────────────────────────────

_loop = asyncio.new_event_loop()
threading.Thread(target=_loop.run_forever, daemon=True).start()

ptb_app = Application.builder().token(TOKEN).build()

ptb_app.add_handler(CommandHandler("start",     cmd_start))
ptb_app.add_handler(CommandHandler("help",      cmd_start))
ptb_app.add_handler(CommandHandler("cancel",    cmd_cancel))
ptb_app.add_handler(CommandHandler("list",      cmd_list))
ptb_app.add_handler(CommandHandler("get",       cmd_get))
ptb_app.add_handler(CommandHandler("factcheck", cmd_factcheck))
ptb_app.add_handler(CommandHandler("review",    cmd_review))
ptb_app.add_handler(CommandHandler("update",    cmd_update))
ptb_app.add_handler(CommandHandler("showbrand", cmd_showbrand))
ptb_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

asyncio.run_coroutine_threadsafe(ptb_app.initialize(), _loop).result(timeout=10)


# ─── Flask app ────────────────────────────────────────────────────────────────

flask_app = Flask(__name__)

@flask_app.route("/")
def index():
    return "Spades Bot v2 Running ✓", 200

@flask_app.route("/setup")
def setup_webhook():
    future = asyncio.run_coroutine_threadsafe(
        ptb_app.bot.set_webhook(url=WEBHOOK_URL), _loop
    )
    ok = future.result(timeout=15)
    return {"ok": ok, "webhook_url": WEBHOOK_URL}

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
