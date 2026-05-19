# Build Plan — Spades Content System v2

Mục tiêu: content marketing cho Spades qua nhiều dạng bài viết, chạy hoàn toàn qua Telegram, chất lượng cao, ít chỉnh sửa.

**Nguyên tắc xuyên suốt:** Quality gate ở đầu vào. Làm rõ data trước, viết sau. Không chạy pipeline rẻ rồi chỉnh nhiều lần.

---

## Kiến trúc đã chốt

```
User nhắn Telegram
        ↓
Strategist (orchestrator) — giữ context hội thoại, gợi ý angle, phỏng vấn, tạo brief
        ↓
        ├── Story Scanner (subagent)  — tìm story internet khi cần Story Writing
        ├── Story Writer  (subagent)  — thought leadership, bridge story → poker insight
        ├── Copywriter    (subagent)  — bài ngắn bán cảm giác hoặc tính năng quán
        └── Advertorial   (subagent)  — kể chuyện người thật trong community Spades
        ↓
Fact Checker (subagent, chỉ chạy khi bài có claim cụ thể)
        ↓
User nhận bài — có thể chỉnh nhỏ / viết lại / bắt đầu bài mới
```

---

## Thứ tự build

### Bước 1 — Nâng cấp Story Writer
**Trạng thái:** ✅ Hoàn thành (2026-05-19)
**Đã làm:**
- Thêm nguyên lý cốt lõi: tâm lý học → story → poker
- Rewrite bridge section: test cơ chế nhân quả, bỏ motif nhàm "poker cũng vậy"
- Topic phải xuất hiện trong bài như concept được đặt tên
- Bank 8 góc tâm lý với biểu hiện cụ thể ở bàn poker
- Thêm hai nhóm người đọc (newbie vs regular) với blind spot khác nhau
- Fix brand voice: chỉ dùng khi brief có góc nhìn thật của owner
- Tách rõ "không bịa story facts" vs "poker domain knowledge là writer tự viết"
- Post-processing tự động xóa em-dash trong pipeline.py
- Đổi tên content-writer.md → spades-story-writer.md

**Kiểm tra chất lượng:**
- [x] Test bài "game sinh tồn" — hầm mỏ Chile 2010 — owner duyệt OK

---

### Bước 2 — Test Copywriter và Advertorial standalone
**Trạng thái:** ✅ Hoàn thành (2026-05-19)
**Đã test:**
- Ultra X → spades-copywriter → bài bán cảm giác, không quảng cáo lộ liễu, owner duyệt OK
- Thành Mini → spades-advertorial → kể chuyện tự nhiên, [THIẾU DATA] đúng chỗ, câu kết đúng tone

---

### Bước 3 — Build Strategist mới
**Trạng thái:** ✅ Hoàn thành (2026-05-19)
**Việc cần làm:**
- Nhận dạng 3 loại input: topic muốn viết / story từ quán / tính năng cần push
- Bank angle tâm lý cho Story Writing — gợi ý chủ động thay vì hỏi chung chung
- Quy trình phỏng vấn riêng cho từng format
- Checklist data tối thiểu cho từng format — chỉ dừng hỏi khi đủ
- Xử lý 3 trạng thái sau khi user nhận bài: chỉnh nhỏ / viết lại / bài mới
- Phân biệt "đang trong session tạo content" và "tin nhắn không liên quan"

**Kiểm tra chất lượng trước khi qua bước tiếp:**
- [ ] Strategist gợi ý được angle cụ thể, không hỏi chung chung
- [ ] Biết dừng hỏi đúng lúc — không hỏi quá nhiều, không hỏi quá ít
- [ ] Brief đầu ra đủ để writer viết mà không cần thêm thông tin
- [ ] Xử lý đúng khi user nhắn tin không liên quan đến content

---

### Bước 4 — Kết nối Strategist với các subagents
**Trạng thái:** Chưa làm
**Việc cần làm:**
- Strategist gọi Story Scanner khi cần tìm story (không tự search)
- Strategist gọi đúng writer dựa trên format đã chọn
- Strategist gọi Fact Checker khi bài có claim cụ thể (số liệu, tên người, sự kiện)
- Xử lý flow chỉnh sửa sau khi nhận bài

**Kiểm tra chất lượng trước khi qua bước tiếp:**
- [ ] Chạy end-to-end 1 bài mỗi format (3 format)
- [ ] Strategist route đúng writer cho đúng loại content
- [ ] Fact Checker chỉ chạy khi cần, không chạy thừa

---

### Bước 4+5 — Kết nối subagents + Tích hợp Telegram
**Trạng thái:** ✅ Hoàn thành (2026-05-19)
**Đã làm:**
- Viết lại app.py hoàn toàn — bỏ state machine cũ (_pending_guided, _pending_story, _pending_templates)
- Session-based: mỗi chat_id có messages history, state, brief, writer
- Strategist orchestrates toàn bộ: nhận text → hỏi → ra brief → gọi đúng writer
- Story Writing với "TÌM STORY:": auto-scan → show list → user chọn → viết
- Post-article flow: chỉnh nhỏ / viết lại / bài mới
- Em-dash auto-strip trong writer output
- Giữ utility commands: /list, /get, /factcheck, /review, /cancel

**Kiểm tra chất lượng — cần test thật trên Telegram:**
- [ ] Chạy 1 bài mỗi format (Story Writing / Copywriting / Advertorial)
- [ ] Bot không bị confuse khi user nhắn linh tinh
- [ ] Flow chỉnh sửa hoạt động đúng

---

## Các dạng bài sẽ phát triển thêm sau

Chưa build. Khi cần thì tạo writer agent mới và mở rộng Strategist:
- Caption ảnh ngắn
- Event announcement
- Thread Facebook

---

## Những thứ giữ nguyên, không đụng vào

- Story Scanner — giữ nguyên logic tìm story
- Fact Checker — giữ nguyên
- Hạ tầng Telegram (webhook, Flask, VPS Hostinger)
- Lamwork style — giọng văn cố định, không thay đổi
