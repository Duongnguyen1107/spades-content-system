# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Mục đích hệ thống

Content marketing tự động cho **Spades Board Game Cafe** — quán poker giải trí (no cash game, #NoCashGame) tại Bình Thạnh, HCM. Hệ thống tạo nhiều dạng bài viết chất lượng cao, ít chỉnh sửa, chạy hoàn toàn qua Telegram.

### Kiến trúc đã chốt (2026-05)

```
User nhắn Telegram (topic / story từ quán / tính năng cần push)
        ↓
Strategist (orchestrator) — phỏng vấn, gợi ý angle, chọn format, tạo brief
        ↓
        ├── Story Scanner   — tìm story internet (chỉ dùng cho Story Writing có TÌM STORY:)
        ├── Story Writer    — thought leadership, bridge story ngoài đời → poker insight
        ├── Copywriter      — bài ngắn bán cảm giác hoặc chứng minh tính năng quán
        └── Advertorial     — kể chuyện người thật trong community Spades
        ↓
Fact Checker (khi có claim cụ thể — gọi qua /factcheck)
```

**Nguyên tắc thiết kế cốt lõi:** Quality gate nằm ở đầu vào — Strategist phải làm rõ đủ data trước khi viết, không chạy rồi chỉnh sửa nhiều lần sau.

**Strategist phải chủ động:** gợi ý angle thay vì hỏi thụ động. Story Writing: đề xuất 2-3 góc tâm lý cụ thể. Advertorial: phỏng vấn kéo story ra từ user.

### Các dạng bài sẽ phát triển thêm sau

Caption ảnh ngắn, event announcement, thread Facebook — chưa build, thêm bằng cách tạo writer agent mới và mở rộng Strategist.

---

## Interface chính — Telegram Bot

User nhắn plain text vào bot → Strategist xử lý toàn bộ. Không cần slash command.

**Ví dụ cách dùng:**
- *"tôi muốn viết về ego trong poker"* → Story Writing flow
- *"viết bài giới thiệu Ultra X"* → Copywriting flow
- *"có ông khách tên Hiệp đi xe từ Bảo Lộc xuống..."* → Advertorial flow

**Commands hỗ trợ:**
- `/list` — xem files gần nhất
- `/get <slug>` — lấy file theo slug
- `/factcheck <slug>` — kiểm tra facts bài viết
- `/review <slug>` — chấm điểm bài (standalone)
- `/cancel` — reset conversation

**Flow sau khi nhận bài:**
Bot hỏi: `1 — Chỉnh nhỏ | 2 — Viết lại | 3 — Bài mới`

---

## Agents — file và vai trò

| File | Vai trò | Model | Gọi bởi |
|------|---------|-------|---------|
| `agents/spades-strategist.md` | Orchestrator: nhận input → phỏng vấn → brief → gọi đúng writer | Sonnet | app.py |
| `agents/spades-story-writer.md` | Story Writing: thought leadership, bridge story → poker insight | Sonnet | Strategist |
| `agents/spades-copywriter.md` | Copywriting: bán cảm giác hoặc chứng minh tính năng | Sonnet | Strategist |
| `agents/spades-advertorial.md` | Advertorial: kể chuyện người thật trong community | Sonnet | Strategist |
| `agents/story-scanner.md` | Tìm story thật theo pattern (chỉ Story Writing có TÌM STORY:) | Haiku | app.py sau khi nhận brief |
| `agents/fact-checker.md` | Verify facts, fix inline với strikethrough | Sonnet | pipeline.py (`--step factcheck`) |
| `agents/content-reviewer.md` | Chấm 7 tiêu chí — standalone only | Sonnet | pipeline.py (`--step review`) |
| `agents/content-strategist.md` | Legacy brief writer — dùng trong pipeline.py cũ | Sonnet | pipeline.py (`--step brief`) |

Backup files: `*.backup.md` — giữ lại để so sánh, không dùng trong production.

---

## 3 Format bài viết

### Story Writing (spades-story-writer)
Thought leadership — lấy story thật từ internet, bridge sang poker insight.

**Nguyên lý cốt lõi:** Tâm lý học con người → Story minh họa → Poker là nơi nguyên lý hoạt động.

**Bridge:** Mạnh khi cùng cơ chế nhân quả. Test: *"Trong story, X→Y vì Z. Ở poker, cùng cơ chế Z khiến A→B."* Nếu điền được mạch lạc → bridge mạnh.

**Bank 8 góc tâm lý:** Survival instinct, Expectation management, Stoicism, Unpredictability, Complacency, Outcome bias, Counter-intuitive response, Ego/Identity protection.

**2 format con:** Story-Bridge (nhân vật tên thật + kỹ thuật poker độc lập) / Personal Reflection (concept/triết lý + recognition moment).

**2 nhóm người đọc:** Newbie (action bias, call loose) / Regular (ego, outcome bias, toxic với người mới).

### Copywriting (spades-copywriter)
Bài ngắn 150-400 từ về tính năng/game mode/event.

**Nguyên lý cốt lõi:** Xác định CẢM GIÁC người mua trước khi viết — không phải tính năng.

**Kiểu A — Bán cảm giác:** Cảm giác là khung, tính năng là chi tiết bên trong.

**Kiểu B — Chứng minh:** Claim → Proof → Implication. 4 kỹ thuật proof: số liệu cụ thể / người hoài nghi bị thuyết phục / quy trình cụ thể / giác quan cụ thể.

**Nhịp câu:** Dài-dài-ngắn(punch). Điểm quan trọng nhất = câu ngắn nhất.

**6 hook types:** Kết quả trước / Vào giữa trận / Số liệu đối lập / Câu hỏi tu từ / Đối lập hành động / **Thừa nhận điểm yếu** (mới).

### Advertorial (spades-advertorial)
Bài 300-600 từ kể chuyện người thật trong community.

**Quy tắc 30/70:** 70% câu chuyện con người, 30% Spades. Spades xuất hiện tối đa 2 lần: lần 1 bối cảnh, lần 2 CTA.

**Giọng hoài nghi:** Nhân vật phải thể hiện do dự/không kỳ vọng trước — làm payoff đáng tin hơn.

**Plot Twist bắt buộc:** Setup expectation sớm → lật ngược đúng lúc. Reader expects X, gets Y.

**Câu kết:** Chi tiết vật lý nhỏ cụ thể — không grand statement, không tổng kết bài học.

**Chi tiết giác quan bắt buộc tại Payoff:** mùi, vị, xúc giác, âm thanh, hoặc hành động vật lý quan sát được.

---

## Nguyên tắc chung tất cả agents

**Nguyên tắc 2 chiều** — BẮT BUỘC trong Story Writing:
- Chiều sai: `[hành động cụ thể] → [hậu quả cụ thể]`
- Chiều đúng: `[hành động cụ thể] → [cơ chế] → [kết quả]`

**Lamwork style** — giọng văn cố định tất cả agents:
- Xưng "mình", gọi "ae" — không bao giờ "bạn", "tôi"
- Đoạn 1-3 câu, xuống dòng thường xuyên
- **KHÔNG em-dash (—)** — tất cả agents đều scan và xóa trước khi output
- Không bullet, không header trong body bài
- Tiếng Anh poker giữ nguyên (fold, tilt, equity...); từ domain story dùng tiếng Việt

**Fact Checker** — fix inline, không viết lại:
- Format: `~~text cũ~~ text đúng` + `*(Fix: lý do)*`
- Quote nhân vật: KHÔNG xóa, giữ nguyên, sửa context nếu sai

---

## Shared rules & Library — @include system

### Cơ chế @include
`_load_agent()` trong `app.py` và `load_agent()` trong `pipeline.py` đều xử lý directive:
```
<!-- @include: shared/voice-rules.md -->
```
Directive được resolve trước khi truyền vào API. Đặt ở bất kỳ đâu trong body agent file.

**Khi thêm agent mới:** thêm 2 dòng `@include` vào đầu file, không cần sửa code.

### agents/shared/ — cấu trúc

```
agents/shared/
├── voice-rules.md          ← Lamwork style chung (xưng mình/ae, em-dash, tiếng Anh test)
│                             Load bởi: tất cả 3 writer agents
│
└── library/
    ├── index.md            ← Concept index nhẹ (~800 tokens) cho Strategist
    │                         Load bởi: spades-strategist.md
    ├── tam-ly.md           ← 29 bài tâm lý poker (31KB)
    ├── khai-niem.md        ← 10 bài khái niệm poker (10KB)
    ├── dinh-ly.md          ←  4 bài định lý poker (4.5KB)
    └── co-ban.md           ← 10 bài cơ bản poker (11KB) — KHÔNG load vào writer
```

**Story writer load:** `voice-rules.md` + `library/tam-ly.md` + `library/khai-niem.md` + `library/dinh-ly.md` (~15K tokens)
**Strategist load:** `library/index.md` (~4K tokens, chỉ index — không load full library)

### Format mỗi entry trong library

```
**Tên khái niệm:** [tên 2-6 từ]
**Định nghĩa:** [1-2 câu]
**Cơ chế tâm lý:** [WHY chiều sai xảy ra — đây là "Z" trong bridge test]
**Chiều sai:** [hành động → hậu quả]
**Chiều đúng:** [hành động → cơ chế → kết quả]
**Ví dụ bàn:** [tình huống cụ thể 2-3 câu]
*Nguồn: url*
```

`Cơ chế tâm lý` là field quan trọng nhất — là cơ chế Z để bridge: story ngoài đời và poker chia sẻ cùng Z.

### Cập nhật library

```powershell
# Rebuild 1 category (ví dụ khi thêm bài mới vào tam-ly)
python scripts/build_poker_library.py tam-ly

# Rebuild toàn bộ
python scripts/build_poker_library.py

# Audit kiểm tra entries bị truncated/missing
python scripts/audit_library.py
```

**Thêm bài mới:** thêm URL vào `ARTICLE_URLS` trong `scripts/build_poker_library.py`, chạy rebuild category tương ứng.

**Quy tắc no cash game:** Library không được dùng "tiền", "lợi nhuận", "$X", "tài chính" — dùng "chip", "kết quả", "thắng pot" thay thế. Script `scripts/fix_nocashgame.py` để kiểm tra và fix hàng loạt.

---

## app.py — kiến trúc mới (v2)

`app.py` là Telegram webhook bot. Kiến trúc session-based thay cho state machine cũ.

**Session per chat_id:**
```python
{
  "messages": [],       # conversation history với Strategist
  "state": "chatting",  # chatting | brief_ready | story_pick | writing | post_article
  "brief": None,        # brief content
  "writer": None,       # "spades-story-writer" | "spades-copywriter" | "spades-advertorial"
  "slug": None,         # slug sau khi scan story
}
```

**Detection brief:** Khi Strategist output có `BRIEF → spades-*` → app.py parse writer type và trigger flow tương ứng.

**Story Writing với TÌM STORY:** app.py gọi `pipeline.py --step scan` → show danh sách → user chọn số → gọi writer.

**Các dict cũ đã bỏ:** `_pending_guided`, `_pending_story`, `_pending_templates` — không còn trong codebase.

---

## pipeline.py — local dev & testing

`pipeline.py` vẫn hoạt động để dev và test local. Dùng khi cần test writer standalone mà không qua Telegram.

```bash
# Test writer với brief có sẵn
python pipeline.py --step write outputs/stories/{slug}.md

# Standalone factcheck
python pipeline.py --step factcheck outputs/posts/{slug}.md

# Legacy pipeline (vẫn hoạt động nhưng không phải primary interface)
python pipeline.py --pipeline "tilt control" --auto
python pipeline.py --pipeline "tư duy logic" --guided
```

**Em-dash post-processing:** `pipeline.py` tự động strip em-dash khỏi writer output trước khi lưu file.

---

## Scan mechanism (vẫn dùng cho Story Writing)

**Pattern Scan (primary)** — khi brief có `TÌM STORY:`:
- app.py extract pattern → gọi `pipeline.py --step scan`
- 3 queries: chiều sai / chiều đúng / broad
- Story từ bất kỳ domain nào

**Domain Scan (fallback)** — khi dùng `--step scan` trực tiếp:
- 9 domains: `Bóng đá | Esports/Gaming | MMA/Boxing | Đầu tư/Forex/Crypto | Kinh doanh thương hiệu lớn | Triết học/Stoicism | Hàng không vũ trụ | Lịch sử thế giới | Phim/Series triết học`

---

## Cấu trúc thư mục

```
d:\Poker Cafe\spades-content-system\   ← git root, cũng là thư mục dev duy nhất
├── pipeline.py           ← local dev & testing
├── app.py                ← production bot (Flask webhook, Telegram)
├── CLAUDE.md             ← context cho Claude Code (file này)
├── PLAN.md               ← roadmap build và quality checklist
├── README.md
├── requirements.txt      ← bao gồm beautifulsoup4 (dùng cho library scripts)
├── .env               ← ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN
│
├── agents/
│   ├── spades-strategist.md      ← orchestrator chính
│   ├── spades-story-writer.md    ← writer thought leadership
│   ├── spades-copywriter.md      ← writer copywriting
│   ├── spades-advertorial.md     ← writer advertorial
│   ├── story-scanner.md          ← tìm story internet
│   ├── fact-checker.md           ← verify facts
│   ├── content-reviewer.md       ← standalone reviewer
│   ├── content-strategist.md     ← legacy brief writer
│   ├── *.backup.md               ← backup trước khi nâng cấp
│   └── shared/                   ← shared rules & library (@include system)
│       ├── voice-rules.md        ← lamwork style chung
│       └── library/
│           ├── index.md          ← concept index cho Strategist
│           ├── tam-ly.md         ← 29 bài tâm lý
│           ├── khai-niem.md      ← 10 bài khái niệm
│           ├── dinh-ly.md        ←  4 bài định lý
│           └── co-ban.md         ← 10 bài cơ bản (không load vào writer)
│
├── scripts/
│   ├── build_poker_library.py    ← scrape + build library từ wikipoker.net
│   ├── audit_library.py          ← kiểm tra entries bị truncated/missing
│   └── fix_nocashgame.py         ← remove money references khỏi library
│
├── archive/           ← deprecated code
│
└── outputs/
    ├── stories/       ← {slug}.md (scanner) + {slug}_brief.md
    ├── posts/         ← {slug}.md (writer output)
    ├── checks/        ← {slug}_review.md và {slug}_check.md
    └── content_log.json
```

---

## Deploy lên GitHub và VPS

**Một nguồn duy nhất:** Dev ở git root → push GitHub → VPS pull về. Không copy thủ công.

**GitHub repo:** `https://github.com/Duongnguyen1107/spades-content-system`
**VPS:** Hostinger, IP `82.180.163.187`, CloudPanel, domain `diymode.work`
**Production file:** `app.py` tại `/home/diymode/htdocs/diymode.work/`

**Push lên GitHub:**
```powershell
git add agents/ app.py pipeline.py CLAUDE.md PLAN.md
git commit -m "mô tả thay đổi"
git push origin master:main
```

**Deploy lên VPS:**
```bash
ssh root@82.180.163.187
cd /home/diymode/htdocs/diymode.work && git pull origin main && kill $(cat /home/diymode/gunicorn.pid) && sleep 2 && su -s /bin/bash diymode -c "cd /home/diymode/htdocs/diymode.work && /home/diymode/venv/bin/gunicorn app:flask_app --bind 0.0.0.0:8081 --workers 1 --timeout 300 --daemon --pid /home/diymode/gunicorn.pid --access-logfile /home/diymode/logs/access.log --error-logfile /home/diymode/logs/error.log"
```

**Verify:**
```bash
ps aux | grep gunicorn | grep -v grep
```

Nếu git pull lỗi: `git pull origin main --rebase`

---

## Khi chỉnh sửa agents

- **Thêm rule mới vào writer** → thêm vào cả writer file VÀ checklist verify của agent đó
- **Thêm format mới** → tạo writer agent mới + mở rộng spades-strategist.md (thêm schema brief và detection logic)
- **Thêm/bỏ domain scan** → sửa `_SCAN_DOMAINS` và `_ROUTER_PROMPT` trong `pipeline.py`
- **Backup trước khi sửa agent quan trọng** → `cp agents/X.md agents/X.backup.md`
- **Đổi voice rule** → chỉ sửa `agents/shared/voice-rules.md` — tất cả writer agents tự nhận
- **Thêm agent mới** → tạo file `.md` mới + thêm `<!-- @include: shared/voice-rules.md -->` vào đầu
- **Thêm bài mới vào library** → thêm URL vào `ARTICLE_URLS` trong `scripts/build_poker_library.py` → chạy `python scripts/build_poker_library.py [category]`
- **Kiểm tra library** → chạy `python scripts/audit_library.py` (expect: "OK: 53 entries | Issues: 0")
