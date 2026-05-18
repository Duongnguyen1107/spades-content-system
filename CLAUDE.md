# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Mục đích hệ thống

Content pipeline tự động cho **Spades Board Game Cafe** — quán poker giải trí (no cash game, #NoCashGame) tại Bình Thạnh, HCM. Hệ thống tìm story thật từ internet, bridge sang poker concept, viết bài theo lamwork style, review và fact-check trước khi đăng.

---

## Chạy pipeline

Tất cả lệnh chạy từ thư mục `spades-content-system/` (chứa `pipeline.py`):

```bash
# Full pipeline tự động (scan → brief → write → review → factcheck)
python pipeline.py --pipeline "tilt control" --auto

# Full pipeline có Anchor Chat — hỏi 2-3 câu để làm rõ góc viết trước khi scan
python pipeline.py --pipeline "tư duy logic" --guided

# Từng bước riêng lẻ
python pipeline.py --step scan --topic "variance"
python pipeline.py --step brief
python pipeline.py --step write
python pipeline.py --step review
python pipeline.py --step factcheck

# Chạy trên file cụ thể
python pipeline.py --step factcheck "outputs/posts/bluff_jobs_20260427_0027.md"
```

Slash commands (dùng trong Claude Code):
- `/scan [topic]` — tìm story
- `/brief [slug]` — tạo marketing brief
- `/write [slug]` — viết bài
- `/review [slug]` — chấm điểm bài viết
- `/factcheck [slug]` — verify facts
- `/generate [topic]` — full pipeline `--auto`
- `/guided [topic]` — full pipeline với Anchor Chat (hỏi góc viết trước khi scan)

---

## Kiến trúc pipeline

```
/scan → Story Scanner (Haiku, web_search)
  ↓  Router chọn 2 domain → 2 mini-scans song song → Evaluator chọn top 2
  outputs/stories/{slug}.md

/brief → Content Strategist (Sonnet)
  ↓  Đọc story → xuất brief: FORMAT, AUDIENCE, GOAL, ANGLE,
     POKER INSIGHT, 2 CHIỀU POKER, CTA, NOTE
  outputs/stories/{slug}_brief.md   ← co-locate với story file (intentional)

/write → Content Writer (Sonnet)
  ↓  Đọc story + brief → viết bài lamwork style + checklist
  outputs/posts/{slug}.md

/review → Content Reviewer (Sonnet)
  ↓  Chấm 7 tiêu chí (35 điểm) + reader simulation (Khoa persona)
  outputs/checks/{slug}_review.md

/factcheck → Fact Checker (Sonnet, web_search)
  ↓  Verify claims → fix inline với strikethrough → cập nhật post file
  outputs/checks/{slug}_check.md
```

`pipeline.py` là orchestrator duy nhất — tất cả pipeline chạy qua đây.
`archive/` chứa scripts cũ (scan.py, write.py, agent.py...) — không dùng nữa, giữ lại để tham khảo.

---

## Agents — file và vai trò

| File | Vai trò | Model |
|------|---------|-------|
| `agents/story-scanner.md` | Tìm story thật, đánh giá 6 tiêu chí, output bridge point + 2 chiều tiềm năng | Haiku (mini-scan) + Sonnet (evaluate) |
| `agents/content-strategist.md` | Đọc story → marketing brief. Quyết định FORMAT, AUDIENCE, 2 CHIỀU POKER | Sonnet |
| `agents/content-writer.md` | Viết bài theo lamwork style. Có 2 format: Story-Bridge và Personal Reflection | Sonnet |
| `agents/content-reviewer.md` | Chấm điểm 7 tiêu chí + Khoa persona simulation | Sonnet |
| `agents/fact-checker.md` | Verify facts, fix inline với strikethrough, giữ nguyên quote gốc | Sonnet |

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

## Domain scan

9 domains hiện tại:
`Bóng đá | Esports / Gaming | MMA / Boxing | Đầu tư / Forex / Crypto | Kinh doanh thương hiệu lớn | Triết học / Stoicism | Hàng không vũ trụ | Lịch sử thế giới | Phim / Series triết học`

Thay đổi so với trước: bỏ "Lịch sử Việt Nam" (gộp vào Lịch sử thế giới), đổi "Kinh doanh / Startup" thành thương hiệu toàn cầu lớn, thêm Triết học / Stoicism và Hàng không vũ trụ, refine Phim / Series sang phim có chiều sâu triết học.

Router có **recency tracking** — đọc 4 story files gần nhất, tránh lặp domain. Hints của mỗi domain mô tả **pattern cần tìm** (không phải danh sách tên cứng) để scanner tìm story mới, không lặp cùng nhân vật.

---

## Cấu trúc thư mục

```
spades-content-system/
├── pipeline.py           ← orchestrator duy nhất
├── CLAUDE.md          ← context cho Claude Code (file này)
├── README.md
├── requirements.txt
├── .env               ← ANTHROPIC_API_KEY
│
├── agents/            ← system prompts của 5 agents
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

## Khi chỉnh sửa agents

- **Thêm rule mới** → thêm vào cả Writer (để agent tuân theo) và Reviewer (để kiểm tra được)
- **Thêm format mới** → cập nhật Strategist (FORMAT field), Writer (rules theo format), Reviewer (tiêu chí theo format)
- **Thêm/bỏ domain** → sửa `_SCAN_DOMAINS` và `_ROUTER_PROMPT` trong `pipeline.py`
- `config.py` chỉ dùng cho `agent.py` (simple mode cũ) — không ảnh hưởng pipeline chính
