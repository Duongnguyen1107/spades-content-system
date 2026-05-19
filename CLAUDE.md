# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Mục đích hệ thống

Content pipeline tự động cho **Spades Board Game Cafe** — quán poker giải trí (no cash game, #NoCashGame) tại Bình Thạnh, HCM. Hệ thống tìm story thật từ internet, bridge sang poker concept, viết bài theo lamwork style, review và fact-check trước khi đăng.

---

## Chạy pipeline

Tất cả lệnh chạy từ thư mục `spades-content-system/` (chứa `pipeline.py`):

```bash
# Full pipeline có Strategy Builder — hỏi 2-3 câu để làm rõ góc viết trước khi scan
python pipeline.py --pipeline "tư duy logic" --guided

# Full pipeline tự động không hỏi (dùng khi topic đã rõ)
python pipeline.py --pipeline "tilt control" --auto

# Từng bước riêng lẻ
python pipeline.py --step scan --topic "variance"
python pipeline.py --step brief
python pipeline.py --step write
python pipeline.py --step review      # standalone, không chạy trong --pipeline nữa
python pipeline.py --step factcheck

# Chạy trên file cụ thể
python pipeline.py --step factcheck "outputs/posts/bluff_jobs_20260427_0027.md"
```

Slash commands (dùng trong Claude Code):
- `/scan [topic]` — tìm story
- `/brief [slug]` — tạo marketing brief
- `/write [slug]` — viết bài
- `/review [slug]` — chấm điểm bài viết (standalone)
- `/factcheck [slug]` — verify facts
- `/generate [topic]` — full pipeline `--auto`
- `/guided [topic]` — full pipeline với Strategy Builder

---

## Kiến trúc pipeline (hiện tại)

```
--guided "topic"
        ↓
Strategy Builder (Sonnet) — hội thoại 2-3 lượt
  ↓  Output <STRATEGY>: CONCEPT, CHIỀU SAI/ĐÚNG, POKER MOMENT,
     FORMAT, AUDIENCE, HOOK TYPE, DOMAIN, STORY PATTERN,
     EMOTIONAL ARC, SHARE TRIGGER
        ↓
Pattern Scan (Haiku + web_search) — KHÔNG giới hạn domain
  ↓  Search bằng STORY PATTERN trực tiếp — 3 queries: chiều sai / chiều đúng / broad
  ↓  Fallback sang Domain Scan nếu không có STORY PATTERN
  outputs/stories/{slug}.md

        ↓  user chọn story
Content Strategist (Sonnet) — nhận STRATEGY làm pre-seed
  ↓  Viết brief bám sát intent của user, không drift
  outputs/stories/{slug}_brief.md

        ↓  user confirm brief
Content Writer (Sonnet)
  ↓  Đọc story + brief → viết bài lamwork style
  outputs/posts/{slug}.md

        ↓
Fact Checker (Sonnet, web_search)
  ↓  Verify claims → fix inline
  outputs/checks/{slug}_check.md
```

**Reviewer đã bỏ khỏi `--pipeline`** — Strategy Builder đảm nhận vai trò định hướng chất lượng từ đầu. Dùng `--step review` để review standalone khi cần.

`pipeline.py` là orchestrator duy nhất — tất cả pipeline chạy qua đây.

---

## Agents — file và vai trò

| File | Vai trò | Model | Trong pipeline? |
|------|---------|-------|-----------------|
| `agents/story-scanner.md` | Tìm story thật theo STORY PATTERN, output bridge point + 2 chiều | Haiku | ✅ |
| `agents/content-strategist.md` | Đọc story + STRATEGY pre-seed → marketing brief | Sonnet | ✅ |
| `agents/content-writer.md` | Viết bài lamwork style. 2 format: Story-Bridge / Personal Reflection | Sonnet | ✅ |
| `agents/fact-checker.md` | Verify facts, fix inline với strikethrough | Sonnet | ✅ |
| `agents/content-reviewer.md` | Chấm 7 tiêu chí + Khoa simulation — standalone only | Sonnet | ❌ (dùng `--step review`) |

---

## 2 Format bài viết

**Story-Bridge** — sự kiện ngoài đời thật → poker insight kỹ thuật
- Bridge: im lặng, reader tự thấy liên kết
- Poker section: mechanism kỹ thuật + 2 chiều cụ thể
- CTA: conversion (ngày/giờ/link)

**Personal Reflection** — concept/triết lý → cảm xúc cá nhân trong poker
- Bridge: câu hỏi tu từ OK
- Poker section: recognition moment + brand voice owner
- CTA: community (mềm, không push)

---

## Nguyên tắc cốt lõi đã confirmed

**Nguyên tắc 2 chiều** — BẮT BUỘC trong cả story lẫn poker section:
- Chiều sai: `[hành động cụ thể] → [hậu quả cụ thể]`
- Chiều đúng: `[hành động cụ thể] → [cơ chế] → [kết quả]`

**Lamwork style** — giọng văn cố định:
- Xưng "mình", gọi "ae" — không bao giờ "bạn", "tôi"
- Đoạn 1-3 câu, xuống dòng thường xuyên
- Không em-dash, không bullet, không header trong body
- Tiếng Anh poker giữ nguyên (fold, equity, tilt...); từ domain story dùng tiếng Việt (đánh đầu, không phải header; tổng tỷ số, không phải aggregate)

**Fact checker** — fix inline, không viết lại:
- Format sửa: `~~text cũ~~ text đúng` + `*(Fix: lý do)*`
- Quote của nhân vật: KHÔNG xóa, giữ nguyên trong ngoặc kép, sửa context sai nếu có, chú thích nguyên văn gốc phía dưới

---

## Scan mechanism

**Pattern Scan (primary)** — khi có STORY PATTERN từ Strategy Builder:
- Search trực tiếp bằng pattern, không giới hạn domain
- 3 queries: (1) chiều sai — ai làm sai → hậu quả, (2) chiều đúng — ai làm đúng → thành công, (3) broad — story nổi tiếng nhất khớp pattern
- Story có thể đến từ bất kỳ domain nào

**Domain Scan (fallback)** — khi không có STORY PATTERN (dùng `/scan` standalone hoặc `--auto`):
- Router chọn 2 domain từ 9 domain có sẵn dựa trên topic
- 9 domains: `Bóng đá | Esports/Gaming | MMA/Boxing | Đầu tư/Forex/Crypto | Kinh doanh thương hiệu lớn | Triết học/Stoicism | Hàng không vũ trụ | Lịch sử thế giới | Phim/Series triết học`
- Router có recency tracking — tránh lặp domain gần đây

---

## Cấu trúc thư mục

```
d:\Poker Cafe\spades-content-system\   ← git root, cũng là thư mục dev duy nhất
├── pipeline.py           ← orchestrator duy nhất
├── app.py                ← production bot (Flask webhook cho VPS)
├── CLAUDE.md             ← context cho Claude Code (file này)
├── README.md
├── requirements.txt
├── .env               ← ANTHROPIC_API_KEY
│
├── agents/            ← system prompts của 8 agents
├── scripts/           ← utilities: set_env.ps1, telegram_bot.py
├── archive/           ← deprecated: scan.py, write.py, agent.py, config.py
│
└── outputs/
    ├── stories/       ← {slug}.md (scanner) + {slug}_brief.md (strategist, co-locate)
    │                     _domains_*.txt (scanner cache, tự xóa được)
    ├── posts/         ← {slug}.md (writer, được factcheck cập nhật inline)
    ├── checks/        ← {slug}_review.md và {slug}_check.md
    └── content_log.json  ← lịch sử 5 bài gần nhất (Strategist đọc để tránh lặp angle)
```

Brief file (`{slug}_brief.md`) co-locate với story file cùng folder là intentional — `brief_path_for()` trong pipeline.py link chúng theo slug.

---

## Deploy lên GitHub và VPS

**Một nguồn duy nhất:** Dev ở git root → push GitHub → VPS pull về. Không copy thủ công.

**GitHub repo:** `https://github.com/Duongnguyen1107/spades-content-system`
**VPS:** Hostinger, IP `82.180.163.187`, CloudPanel, domain `diymode.work`
**Production file:** `app.py` (Flask webhook) tại `/home/diymode/htdocs/diymode.work/`

### Quy trình push và deploy

**Bước 1 — Push từ git root:**
```powershell
# Từ thư mục d:\Poker Cafe\spades-content-system\
git add pipeline.py app.py CLAUDE.md agents/   # thêm file nào đã sửa
git commit -m "mô tả thay đổi"
git push origin master:main
```

**Bước 2 — SSH vào VPS:**
```powershell
ssh root@82.180.163.187
# Nhập password (lấy từ hPanel Hostinger nếu quên)
```

**Bước 3 — Pull và restart bot:**
```bash
cd /home/diymode/htdocs/diymode.work && git pull origin main && kill $(cat /home/diymode/gunicorn.pid) && sleep 2 && su -s /bin/bash diymode -c "cd /home/diymode/htdocs/diymode.work && /home/diymode/venv/bin/gunicorn app:flask_app --bind 0.0.0.0:8081 --workers 1 --timeout 300 --daemon --pid /home/diymode/gunicorn.pid --access-logfile /home/diymode/logs/access.log --error-logfile /home/diymode/logs/error.log"
```

**Verify bot đang chạy:**
```bash
ps aux | grep gunicorn | grep -v grep
```

### Lưu ý quan trọng
- `app.py` là production bot (Flask webhook), khác với `scripts/telegram_bot.py` (polling, không dùng trên VPS).
- Nếu git pull bị lỗi divergent branches: `git pull origin main --rebase`

---

## Khi chỉnh sửa agents

- **Thêm rule mới** → thêm vào cả Writer (để agent tuân theo) và Reviewer (để kiểm tra được)
- **Thêm format mới** → cập nhật Strategist (FORMAT field), Writer (rules theo format), Reviewer (tiêu chí theo format)
- **Thêm/bỏ domain** → sửa `_SCAN_DOMAINS` và `_ROUTER_PROMPT` trong `pipeline.py`
- `config.py` chỉ dùng cho `agent.py` (simple mode cũ) — không ảnh hưởng pipeline chính
