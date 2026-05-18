# Spades Content System 🂡

Hệ thống tạo content tự động cho Spades Board Game Cafe.
3 agents, chạy từ terminal hoặc VSCode.

---

## Cấu trúc

```
spades-agent/
├── scan.py              # Runner: Story Scanner
├── write.py             # Runner: Content Writer
├── pipeline.py             # Runner: Fact Checker + Full Pipeline
├── agent.py             # Runner: Single content agent (simple mode)
├── config.py            # System prompt + settings cho agent.py
├── requirements.txt
│
├── agents/
│   ├── story-scanner.md    # Agent tìm story thật từ internet
│   ├── content-writer.md   # Agent viết bài lamwork style
│   └── fact-checker.md     # Agent verify facts trước khi đăng
│
└── outputs/             # Tự tạo khi chạy
    ├── stories/         # Story thô từ scanner
    ├── posts/           # Bài viết hoàn chỉnh
    └── checks/          # Fact check report
```

---

## Setup (một lần duy nhất)

**1. Cài dependencies**
```bash
pip install -r requirements.txt
```

**2. Set API key**

Mac/Linux:
```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

Windows (PowerShell):
```powershell
$env:ANTHROPIC_API_KEY="sk-ant-..."
```

---

## Cách dùng

### Full Pipeline — 1 lệnh từ idea đến bài verified

```bash
python pipeline.py --pipeline "tilt control"
python pipeline.py --pipeline "variance"
python pipeline.py --pipeline "Ronaldo comeback"
python pipeline.py --pipeline "lãi suất Fed"
```

Chạy tuần tự: Story Scanner → Content Writer → Fact Checker
Output lưu vào 3 thư mục: outputs/stories/, outputs/posts/, outputs/checks/

---

### Từng bước riêng lẻ

**Bước 1 — Tìm story:**
```bash
python scan.py "tilt control"         # on-demand: tìm story về topic
python scan.py                         # daily scan: tin mới 24h
```

**Bước 2 — Viết bài:**
```bash
# Từ story file của scanner
python write.py outputs/stories/tilt_control_xxx.md

# Từ topic trực tiếp
python write.py --topic "variance" --angle "tâm lý" --goal "giúp newbie"

# Pipeline scan + write luôn
python write.py --pipeline "tilt control"
```

**Bước 3 — Verify facts:**
```bash
python pipeline.py outputs/posts/tilt_control_xxx.md
```

---

### Simple Mode — Viết bài nhanh không cần story scanner

```bash
python agent.py
python agent.py "variance" "tâm lý" "" "~500 từ" "giúp newbie"
```

---

## Điều chỉnh agents

Mở file trong agents/ để tune:

| File | Chỉnh gì |
|------|---------|
| agents/story-scanner.md  | Domain ưu tiên, bridge criteria, output format |
| agents/content-writer.md | Lamwork style rules, CTA, cấu trúc bài |
| agents/fact-checker.md   | Poker math reference, verify criteria |
| config.py                | Model, max_tokens, system prompt simple mode |

---

## Flow tổng quát

```
idea / topic
    ↓
story-scanner  →  tìm story thật từ internet (web search)
    ↓                story thô + bridge point
content-writer →  viết bài lamwork style
    ↓                bài viết + checklist verify
fact-checker   →  verify số liệu poker + story facts
    ↓                report + bản đã fix nếu cần
đăng bài ✓
```
