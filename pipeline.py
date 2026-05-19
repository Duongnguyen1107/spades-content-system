#!/usr/bin/env python3
"""
Spades Content Pipeline
Orchestrator cho toàn bộ quy trình tạo content: scan → brief → write → review → factcheck.

Cách chạy:
  python pipeline.py --pipeline "tilt control" --auto   # full pipeline tự động
  python pipeline.py --step scan --topic "variance"     # từng bước riêng lẻ
  python pipeline.py --step factcheck "outputs/posts/bai_viet.md"
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

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

from dotenv import load_dotenv
load_dotenv()

CHECKER_AGENT     = Path(__file__).parent / "agents" / "fact-checker.md"
SCANNER_AGENT     = Path(__file__).parent / "agents" / "story-scanner.md"
WRITER_AGENT      = Path(__file__).parent / "agents" / "spades-story-writer.md"
STRATEGIST_AGENT  = Path(__file__).parent / "agents" / "spades-strategist.md"
REVIEWER_AGENT    = Path(__file__).parent / "agents" / "content-reviewer.md"
MODEL          = "claude-sonnet-4-6"
SCANNER_MODEL  = "claude-haiku-4-5-20251001"  # dùng cho scanner để giảm chi phí

# Pricing (USD per million tokens)
PRICE_INPUT         = 3.00   # Sonnet
PRICE_OUTPUT        = 15.00
PRICE_INPUT_HAIKU   = 0.80   # Haiku
PRICE_OUTPUT_HAIKU  = 4.00
PRICE_WEB_SEARCH      = 0.01   # Per search query (web_search_20250305 tool)
PRICE_DEEPSEEK_INPUT  = 0.14   # DeepSeek V3, per million tokens
PRICE_DEEPSEEK_OUTPUT = 0.28

_client        = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
_session_usage: list[dict] = []
_session_lock  = threading.Lock()

# DeepSeek client — dùng cho bước scan nếu có DEEPSEEK_API_KEY
_deepseek_client = None
try:
    if os.environ.get("DEEPSEEK_API_KEY"):
        from openai import OpenAI as _OpenAI
        _deepseek_client = _OpenAI(
            api_key=os.environ.get("DEEPSEEK_API_KEY"),
            base_url="https://api.deepseek.com",
        )
except ImportError:
    pass

DEEPSEEK_MODEL = "deepseek-chat"

# 8 domain — router chọn 2 phù hợp nhất với topic
# Ưu tiên domain quen thuộc với Nam 23-40 HCM
_SCAN_DOMAINS = [
    ("Bóng đá",
     "Tìm story có: đội/cầu thủ thua vì chủ quan hoặc sai chiến thuật (chiều sai) "
     "vs. đội/cầu thủ thắng vì kỷ luật hoặc adapt đúng lúc (chiều đúng). "
     "Ưu tiên: Ngoại hạng Anh, Champions League, World Cup — trận đấu có khoảnh khắc quyết định rõ, tên quen với Nam 23-40 HCM. "
     "Tìm rộng — không giới hạn năm. "
     "Ví dụ pattern (KHÔNG bắt buộc dùng lại): Gerrard slip 2014, Man United 1999 CL final, "
     "Liverpool 4-0 Barca, Zidane headbutt WC 2006, Leicester 5000:1."),

    ("Esports / Gaming",
     "Tìm story có: team/player thua vì mental breakdown hoặc sai draft/strategy (chiều sai) "
     "vs. team/player thắng nhờ chuẩn bị kỹ, đọc được đối thủ, hoặc comeback từ deficit (chiều đúng). "
     "Ưu tiên: tên/giải đấu quen với game thủ Việt Nam 23-40. "
     "Tìm rộng — LMHT, Dota, Valorant, PUBG, CS:GO, bất kỳ esport nào có story đủ mạnh. "
     "Ví dụ pattern (KHÔNG bắt buộc dùng lại): Faker clutch play, "
     "team comeback từ 0-2, upset tại SEA Games, VCS underdog."),

    ("MMA / Boxing",
     "Tìm story có: võ sĩ thua vì game plan sai hoặc mất bình tĩnh (chiều sai) "
     "vs. võ sĩ thắng vì adapt được trong trận hoặc chuẩn bị kỹ hơn (chiều đúng). "
     "Ưu tiên: trận đấu có turning point rõ, tên quen với Nam 23-40 HCM. "
     "Tìm rộng — UFC, ONE Championship, boxing lịch sử, Muay Thai, bất kỳ võ thuật nào. "
     "Ví dụ pattern (KHÔNG bắt buộc dùng lại): tilt dẫn đến thua, "
     "underdog thắng nhờ game plan, corner quyết định đúng lúc."),

    ("Đầu tư / Forex / Crypto",
     "Tìm story có: trader/investor thua vì FOMO, overleverage, hoặc không cut loss (chiều sai) "
     "vs. trader/investor thắng nhờ kỷ luật, đọc được thị trường, hoặc giữ vững khi áp lực (chiều đúng). "
     "Ưu tiên: sự kiện thị trường lớn hoặc tên investor quen với người Việt 23-40. "
     "Tìm rộng — cổ phiếu, crypto, forex, bất kỳ thị trường tài chính nào. "
     "Ví dụ pattern (KHÔNG bắt buộc dùng lại): Soros Black Wednesday, "
     "crypto crash 2022, nhà đầu tư VN thắng/thua trong biến động lớn."),

    ("Kinh doanh thương hiệu lớn",
     "Tìm story có: công ty/CEO thất bại vì sai quyết định hoặc overconfident (chiều sai) "
     "vs. công ty/CEO thành công nhờ kiên nhẫn, pivot đúng lúc, hoặc nhìn xa hơn đối thủ (chiều đúng). "
     "CHỈ dùng thương hiệu toàn cầu mà ai cũng biết tên — Apple, Tesla, Amazon, Netflix, Nike, "
     "Airbnb, Google, Microsoft, Starbucks, Disney. KHÔNG dùng startup ít tên tuổi. "
     "Ví dụ pattern (KHÔNG bắt buộc dùng lại): Jobs bị đuổi rồi quay lại cứu Apple, "
     "Blockbuster từ chối mua Netflix, Nike ký hợp đồng Jordan khi không ai muốn."),

    ("Triết học / Stoicism",
     "Tìm story có: nhân vật thật áp dụng hoặc KHÔNG áp dụng được triết lý stoicism / kỷ luật nội tâm "
     "trong khoảnh khắc áp lực cao (chiều sai: cảm xúc override lý trí → hậu quả / "
     "chiều đúng: giữ được bình tĩnh → hành động đúng). "
     "Ưu tiên: Marcus Aurelius, Seneca, Epictetus — hoặc người hiện đại áp dụng stoicism rõ ràng. "
     "Bridge tự nhiên sang tilt control và emotional discipline ở bàn poker. "
     "Ví dụ pattern (KHÔNG bắt buộc dùng lại): Marcus Aurelius viết Meditations khi đang chỉ huy chiến tranh, "
     "Seneca đối mặt với lệnh tử hình của Nero, James Stockdale dùng stoicism sống sót POW Vietnam."),

    ("Hàng không vũ trụ",
     "Tìm story có: phi hành gia/kỹ sư/đội mission ra quyết định sai dưới áp lực cực cao (chiều sai) "
     "vs. ra quyết định đúng nhờ kỷ luật, tính toán rủi ro rõ ràng, và không panic (chiều đúng). "
     "Ưu tiên: NASA, SpaceX, ESA — sự kiện mà người Việt 23-40 có thể Google thêm. "
     "Bridge sang poker: mỗi quyết định có hậu quả không thể undo, tính toán xác suất dưới áp lực. "
     "Ví dụ pattern (KHÔNG bắt buộc dùng lại): Apollo 13 improvised CO2 fix, "
     "Challenger disaster khi kỹ sư phản đối nhưng bị bỏ qua, SpaceX landing lần đầu thất bại → học → thành công."),

    ("Lịch sử thế giới",
     "Tìm story có: bên thua vì chủ quan, sai chiến lược, hoặc không đọc được tình huống (chiều sai) "
     "vs. bên thắng nhờ kỷ luật, timing, hoặc chiến lược bất ngờ (chiều đúng). "
     "Ưu tiên: sự kiện/nhân vật lịch sử thế giới HOẶC Việt Nam mà người 23-40 đều biết tên. "
     "Tìm rộng — không giới hạn thời kỳ hay quốc gia. "
     "Ví dụ pattern (KHÔNG bắt buộc dùng lại): Napoleon Waterloo (overconfidence), "
     "Hannibal Cannae (chiến thuật bao vây), Võ Nguyên Giáp Điện Biên Phủ, "
     "Trần Hưng Đạo vs quân Nguyên Mông, Churchill refusing to negotiate 1940."),

    ("Phim / Series triết học",
     "Tìm sự kiện ĐỜI THẬT (có thể verify) đằng sau phim/series có chiều sâu triết học — "
     "HOẶC sự kiện thật được phim khắc họa. KHÔNG dùng plot hư cấu. "
     "Tìm story có: nhân vật thật làm sai gì (chiều sai) vs. làm đúng gì (chiều đúng). "
     "Ưu tiên: phim có tư tưởng về kỷ luật, sự hy sinh, kiểm soát cảm xúc, đối mặt áp lực — "
     "The Dark Knight, Gladiator, Whiplash, Interstellar, Shawshank Redemption, Any Given Sunday. "
     "Ví dụ pattern (KHÔNG bắt buộc dùng lại): Billy Beane thật trong Moneyball, "
     "Michael Burry thật trong Big Short, sự kiện thật đằng sau Whiplash hoặc Gladiator."),
]
_SCAN_DOMAIN_MAP = {name: hints for name, hints in _SCAN_DOMAINS}

# Template: sinh 3 angle khác nhau từ topic thô
_TEMPLATE_PROMPT = """Bạn là prompt engineer cho hệ thống content Spades Board Game Cafe — quán poker giải trí tại HCM.

Nhận topic poker thô, tạo ra ĐÚNG 3 angle khác nhau để search story minh họa.

Mỗi angle theo format này (giữ nguyên label, không thêm gì khác):
CONCEPT     [1 câu mechanism — nhân → quả cụ thể, không phải khái niệm chung]
CHIỀU SAI   [hành động cụ thể người chơi hay làm] → [hậu quả cụ thể ở bàn poker]
CHIỀU ĐÚNG  [hành động cụ thể thay thế] → [kết quả cụ thể]

Yêu cầu bắt buộc:
- 3 angle phải KHÁC NHAU về cơ chế — không chỉ khác từ ngữ
- Mỗi angle nên bridge sang situation poker khác nhau (VD: bad beat, bubble pressure, chip lead, bluff caught, losing streak...)
- CONCEPT phải có "X → Y" rõ ràng: nguyên nhân → hệ quả
- CHIỀU SAI và ĐÚNG đủ cụ thể để Scanner tìm được story thật minh họa
- Không giải thích, không header — chỉ 3 block theo format, cách nhau bằng dòng trống

Ví dụ tốt (topic: tilt):
CONCEPT     thua tay lớn kích hoạt bực bội → suy nghĩ thu hẹp → chỉ thấy cơ hội gỡ → vào tay tiếp với bộ bài không đủ mạnh → thua thêm
CHIỀU SAI   vừa bị lật ngược trên lá bài cuối → vào ngay tay tiếp → gọi raise lớn với bài chưa mạnh vì muốn "gỡ" → mất thêm chip
CHIỀU ĐÚNG  nhận ra mình đang bực → ngồi bỏ qua 2-3 tay → hít thở, quan sát → quay lại khi đầu đã bình thường

CONCEPT     thắng liền tay tạo cảm giác bất bại → đánh giá thấp đối thủ → bluff sai chỗ → bị gọi và mất
CHIỀU SAI   thắng 3 tay liên tiếp → raise lớn với bài trung bình → đối thủ gọi → tiếp tục cược mạnh đến cuối → đối thủ lật bài mạnh hơn → mất cả pot
CHIỀU ĐÚNG  nhận ra mình đang thắng nhiều → tự hỏi "đối thủ đang nhìn mình ra sao?" → đánh vừa phải, không lộ hết pattern → đối thủ không đọc được

CONCEPT     thua dài sinh ra sợ mất thêm → cơ hội tốt cũng không dám vào → chip tự cạn dần vì mù và ante
CHIỀU SAI   thua 3 ván liên tiếp → sợ mất thêm → fold kể cả khi bài tốt vì "chưa chắc" → chip cứ hao dần → cuối cùng bị loại mà không có tay nào đáng nhớ
CHIỀU ĐÚNG  nhận ra chip đang nguy hiểm → xác định bộ bài đủ mạnh để vào → chơi quyết đoán đúng lúc → hoặc double up hoặc thua trong thế chủ động"""

# Router: chọn đúng 2 domain phù hợp nhất với topic
_ROUTER_PROMPT = """Nhận topic poker, chọn ĐÚNG 2 domain có khả năng cao tìm được story thật, bridge tự nhiên, quen thuộc với Nam 23-40 tuổi tại HCM.

Domains có sẵn:
Bóng đá | Esports / Gaming | MMA / Boxing | Đầu tư / Forex / Crypto | Kinh doanh thương hiệu lớn | Triết học / Stoicism | Hàng không vũ trụ | Lịch sử thế giới | Phim / Series triết học

Mapping theo cơ chế/cảm xúc cốt lõi của topic:
- Survival / tồn tại / short stack → MMA / Boxing (underdog fighter), Lịch sử thế giới (quân ít đánh nhiều), Esports / Gaming (team comeback từ deficit), Hàng không vũ trụ (mission critical)
- Tilt / cảm xúc phá quyết định → MMA / Boxing (McGregor tilt), Triết học / Stoicism (mất kiểm soát nội tâm), Bóng đá (penalty miss, headbutt)
- Bluff / table image / deception → Esports / Gaming (mind games, draft bait), Kinh doanh thương hiệu lớn (Jobs demo iPhone), Phim / Series triết học
- Resource / tiền / bankroll → Đầu tư / Forex / Crypto, Kinh doanh thương hiệu lớn
- Variance / may mắn vs kỹ năng → Đầu tư / Forex / Crypto (Soros, Buffett), Bóng đá (underdog cup run), Esports / Gaming (upset match)
- Strategy / đọc đối thủ / game theory → Esports / Gaming (draft, map control), MMA / Boxing (game plan), Lịch sử thế giới (Hannibal, Napoleon)
- Long-term / discipline / kiên nhẫn → Kinh doanh thương hiệu lớn, Lịch sử thế giới, Đầu tư / Forex / Crypto, Triết học / Stoicism
- Data / xác suất / EV → Kinh doanh thương hiệu lớn (Moneyball angle), Đầu tư / Forex / Crypto, Hàng không vũ trụ
- Overconfidence / chip lead → Bóng đá (big team loses), Lịch sử thế giới (quân xâm lược chủ quan), Kinh doanh thương hiệu lớn (Blockbuster)
- Cảm xúc / tâm lý / stoicism → Triết học / Stoicism, MMA / Boxing, Phim / Series triết học
- Áp lực / quyết định không thể undo → Hàng không vũ trụ (Apollo 13, Challenger), MMA / Boxing, Lịch sử thế giới

Phân bổ domain (tham khảo để đảm bảo đa dạng):
- Bóng đá: mạnh với Tilt, Variance, Overconfidence — ưu tiên EPL/CL/World Cup
- Esports / Gaming: mạnh với Bluff, Strategy, Survival — underused, ưu tiên khi phù hợp
- MMA / Boxing: đa năng, dùng được cho hầu hết topic
- Đầu tư / Forex / Crypto: mạnh với Variance, Resource, Long-term
- Kinh doanh thương hiệu lớn: mạnh với Resource, Data, Long-term, Bluff
- Triết học / Stoicism: mạnh nhất với Tilt, Cảm xúc, Discipline — underused, ưu tiên khi phù hợp
- Hàng không vũ trụ: mạnh với áp lực cực cao, quyết định không thể undo — underused, ưu tiên khi phù hợp
- Lịch sử thế giới: mạnh với Strategy, Survival, Overconfidence — underused, ưu tiên khi phù hợp
- Phim / Series triết học: mạnh với Tilt, Bluff, Cảm xúc, Long-term

Nếu domain gần đây đã dùng → chọn domain khác cùng nhóm phù hợp.
Output JSON duy nhất, không giải thích: {"domains": ["Domain A", "Domain B"]}"""

# Prompt nhẹ chỉ để evaluate — không cần search instructions
_EVALUATE_PROMPT = """Bạn là story editor cho Spades Board Game Cafe — quán poker giải trí tại HCM.
Target audience: Nam 23-40 tuổi, HCM — quan tâm thể thao, kinh doanh, đầu tư, phát triển bản thân.

NHIỆM VỤ: Đánh giá và xếp hạng các story candidates BÊN DƯỚI. Chỉ làm việc với story đã có — KHÔNG tự thêm story mới, KHÔNG bịa story, KHÔNG search.

Chọn top 2 tốt nhất. Nếu chỉ có 1 story → output 1. Không ép đủ 2 nếu không có đủ input.

Đánh số lại STORY #1 / #2 theo thứ tự ưu tiên, giữ nguyên toàn bộ nội dung gốc (không cắt bớt), thêm SUMMARY cuối.

TIÊU CHÍ XẾP HẠNG (theo thứ tự ưu tiên):

1. CÓ 2 CHIỀU TỰ NHIÊN — story phải có sẵn CẢ HAI: chiều sai (ai đó làm gì → hậu quả cụ thể) VÀ chiều đúng (ai đó làm gì khác → cơ chế → kết quả khác). Story chỉ có 1 chiều (chỉ thành công hoặc chỉ thất bại) rank thấp hơn dù bridge mạnh — vì Writer sẽ phải tự bịa ra chiều còn lại.
   VD có 2 chiều: Hamed chuẩn bị casual → thua / Barrera chuẩn bị kỹ từng detail → thắng.
   VD chỉ 1 chiều: "Barrera thắng bằng cách thích nghi tốt" — không thấy Hamed làm sai gì cụ thể.

2. BRIDGE RÕ KHÔNG CẦN GIẢI THÍCH — người đọc tự thấy liên quan mà không cần dẫn dắt. Nếu phải viết "giống như trong poker..." thì bridge yếu.

3. KHOẢNH KHẮC QUYẾT ĐỊNH CỤ THỂ — story phải có 1 moment rõ ràng: ai, quyết định gì, dưới áp lực nào.

4. CON SỐ / NHÂN VẬT THẬT — tên người thật + số liệu cụ thể = credibility.

5. RELEVANCE với Nam 23-40 HCM — thể thao lớn, founder nổi tiếng, tên tuổi quen thuộc sẽ có hook mạnh hơn.

Bridge sang poker hoạt động khi story có: ra quyết định dưới áp lực, risk/reward, đọc người/tình huống, variance, discipline/tilt, bluff/table image, bankroll management, long-term vs short-term.
Bridge BỎ khi phải giải thích dài mới thấy liên quan.

NHẮC LẠI: Chỉ chọn và xếp hạng từ input. Tuyệt đối không tự viết thêm story."""

_ANCHOR_TEMPLATE_SYSTEM = """Bạn là senior content strategist cho Spades Board Game Cafe — quán poker giải trí tại HCM.

Nhiệm vụ: hỏi 2-3 lượt để hiểu đúng ý tưởng của người dùng, rồi synthesize thành bản STRATEGY đầy đủ. Bản này là input duy nhất cho toàn bộ pipeline sau — quyết định domain tìm story, góc viết, format bài, emotional arc, và brief.

---

## CÁCH DẪN CHUYỆN

Lượt 1: Nhận topic thô → hỏi 1 câu về KHÍA CẠNH cụ thể. Đưa 3-4 options khác nhau về cơ chế + [0] nhập tay.
Lượt 2: Nhận trả lời → hỏi 1 câu về KHOẢNH KHẮC POKER cụ thể (khi nào ở bàn player gặp điều này?). Đưa 2-3 options.
Lượt 3 (nếu cần): Hỏi về GÓC KẾT NỐI — người đọc sẽ nhận ra mình ở chỗ nào.

Nguyên tắc hỏi:
- 1 câu mỗi lượt, ngôn ngữ đời thường, không jargon poker
- Options phải khác nhau về cơ chế — không chỉ khác từ ngữ
- Đừng giải thích dài — hỏi xong để người dùng trả lời
- Nếu người dùng trả lời bằng số (1/2/3) → hiểu là chọn option đó

---

## KHI ĐỦ RÕ → OUTPUT STRATEGY

Sau 2-3 lượt, synthesize toàn bộ thành bản STRATEGY. Output ĐÚNG format dưới đây, không thêm gì khác ngoài tag:

<STRATEGY>
CONCEPT      : [cơ chế cốt lõi — "X xảy ra → Y" cụ thể, không phải định nghĩa]
CHIỀU SAI    : [hành động cụ thể người chơi hay làm] → [hậu quả cụ thể ở bàn poker]
CHIỀU ĐÚNG   : [hành động cụ thể thay thế] → [lý do cơ học nó work] → [kết quả]
POKER MOMENT : [khoảnh khắc cụ thể ở bàn — ai, đang làm gì, board/stack như thế nào]
FORMAT       : [Story-Bridge / Personal Reflection]
AUDIENCE     : [Tier 1 — Chưa chơi / Tier 2 — Mới chơi / Tier 3 — Thường xuyên / Broad]
HOOK TYPE    : [Kết quả trước / Vào giữa trận / Số liệu đối lập / Câu hỏi tu từ / Đối lập hành động]
DOMAIN       : [domain tốt nhất để tìm story — 1 trong: Bóng đá / Esports Gaming / MMA Boxing / Đầu tư Forex Crypto / Kinh doanh thương hiệu lớn / Triết học Stoicism / Hàng không vũ trụ / Lịch sử thế giới / Phim Series triết học]
STORY PATTERN: [tìm story về ai + làm gì + hậu quả gì — đủ cụ thể để Tavily search được. VD: "tướng quân tự tin tấn công khi đã cầm chắc ưu thế → địch dùng bẫy → thua sạch"]
EMOTIONAL ARC: [cảm xúc ở hook — lý do] → [cảm xúc ở bridge — lý do] → [cảm xúc sau CTA — lý do]
SHARE TRIGGER: [Identity signal / Recognition gift / Conversation starter] — [1 câu: element nào trong bài kích hoạt trigger này]
</STRATEGY>

---

## TIÊU CHUẨN CHẤT LƯỢNG — tự check trước khi output

CONCEPT tốt: có X → Y rõ ràng, đọc một mình vẫn hiểu cơ chế. Không phải "phải kiên nhẫn" hay "cần bình tĩnh".
CHIỀU SAI tốt: đọc xong tự nhận ra mình — cụ thể đến mức reader nghĩ "ừ mình hay làm vậy". Không phải "chơi tệ".
CHIỀU ĐÚNG tốt: có bước làm gì, không phải "chơi tốt hơn". Lý do cơ học ≠ lý do cảm xúc.
POKER MOMENT tốt: có board/stack/tình huống cụ thể, không phải "khi chơi poker".
STORY PATTERN tốt: Google query được ngay — có nhân vật type + hành động + hậu quả.
EMOTIONAL ARC tốt: 3 checkpoint khác nhau, mỗi cái có lý do trong ngoặc. Không phải "tò mò → học được → muốn ghé".
DOMAIN: chọn domain có story MIRROR đúng cơ chế trong CONCEPT — không phải domain quen thuộc nhất.

---

## VÍ DỤ STRATEGY TỐT

Topic người dùng: "tư duy logic"
Sau 2 lượt → user muốn viết về "tiếp tục làm theo cách cũ dù tình huống đã thay đổi vì đã đầu tư quá nhiều vào nó"

<STRATEGY>
CONCEPT      : đã bỏ nhiều công sức vào một hướng → não tự biện minh để tiếp tục → không nhìn thấy tín hiệu "hướng này không còn hiệu quả" → mất thêm
CHIỀU SAI    : tiếp tục bluff thêm 2 street vì đã cược nhiều ở pre/flop → đối thủ vẫn call → mất toàn bộ stack cho một line không có value
CHIỀU ĐÚNG   : nhận ra bluff đã bị read từ turn → dừng lại, check/fold river → bảo toàn stack cho tay tốt hơn ở sau
POKER MOMENT : river, đã cược 3 street, đối thủ call hết, board paired, stack còn 40BB — tiếp tục shove hay check?
FORMAT       : Story-Bridge
AUDIENCE     : Tier 2 — Mới chơi
HOOK TYPE    : Đối lập hành động
DOMAIN       : Kinh doanh thương hiệu lớn
STORY PATTERN: công ty/CEO tiếp tục đầu tư vào sản phẩm thất bại vì đã bỏ quá nhiều → đối thủ pivot nhanh → công ty lớn thua → CEO kia thành công
EMOTIONAL ARC: Tò mò (công ty lớn thế sao lại sai?) → Nhận ra (mình cũng làm vậy ở bàn mà không biết) → Muốn thử lại (lần sau ngồi bàn sẽ tự hỏi "mình đang tiếp tục vì đúng hay vì lỡ rồi?")
SHARE TRIGGER: Identity signal — CHIỀU SAI phải đủ cụ thể: "tiếp tục cược vì đã cược nhiều rồi" là hành động ai cũng từng làm nhưng chưa có tên gọi — đặt tên cho nó trong bài để reader share vì "đây là mình"
</STRATEGY>"""


def _calc_cost(input_tokens: int, output_tokens: int, search_calls: int = 0,
               haiku: bool = False, deepseek: bool = False) -> float:
    if deepseek:
        p_in, p_out = PRICE_DEEPSEEK_INPUT, PRICE_DEEPSEEK_OUTPUT
    elif haiku:
        p_in, p_out = PRICE_INPUT_HAIKU, PRICE_OUTPUT_HAIKU
    else:
        p_in, p_out = PRICE_INPUT, PRICE_OUTPUT
    return (input_tokens * p_in + output_tokens * p_out) / 1_000_000 + search_calls * PRICE_WEB_SEARCH


def _tavily_search(query: str, max_results: int = 5) -> dict:
    """Tìm kiếm qua Tavily API. Trả về dict với 'answer' và 'results'."""
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        return {}
    try:
        import httpx
        r = httpx.post(
            "https://api.tavily.com/search",
            json={
                "api_key": api_key,
                "query": query,
                "max_results": max_results,
                "search_depth": "advanced",
                "include_answer": True,
            },
            timeout=30,
        )
        return r.json() if r.status_code == 200 else {}
    except Exception:
        return {}


def _format_tavily(data: dict) -> str:
    """Format Tavily results thành text để đưa vào prompt."""
    parts = []
    if data.get("answer"):
        parts.append(f"[Tóm tắt] {data['answer']}")
    for r in data.get("results", []):
        parts.append(
            f"Tiêu đề: {r.get('title', '')}\n"
            f"URL: {r.get('url', '')}\n"
            f"Nội dung: {r.get('content', '')[:600]}"
        )
    return "\n\n---\n\n".join(parts) if parts else "Không có kết quả."


def _generate_search_queries(query: str, domain: str, hints: str, used_stories: list[str]) -> list[str]:
    """Dùng Claude Haiku sinh 3 Tavily search queries thông minh."""
    avoid_note = ""
    if used_stories:
        avoid_note = f"\nCác story đã dùng rồi (KHÔNG tìm lại): {', '.join(s[:50] for s in used_stories[:5])}"

    # Trích STORY PATTERN nếu có — đây là primary search instruction
    story_pattern = ""
    concept_note = ""
    m_pattern = re.search(r'STORY PATTERN cần tìm:\s*([^\n]+)', query)
    m_concept = re.search(r'CONCEPT cần bridge:\s*([^\n]+)', query)
    if m_pattern:
        story_pattern = m_pattern.group(1).strip()
    if m_concept:
        concept_note = m_concept.group(1).strip()

    # Topic thuần — bỏ phần STRATEGY block
    base_topic = query.split("\n\nSTORY PATTERN")[0].split("\n\nANCHOR:")[0].strip()

    if story_pattern:
        primary = (
            f"Story pattern cần tìm (QUAN TRỌNG NHẤT — bám sát khi tạo query):\n{story_pattern}"
        )
        secondary = f"\nConcept poker cần bridge: {concept_note}" if concept_note else ""
        topic_line = f"Topic gốc: {base_topic}"
    else:
        primary = f"Topic cần bridge sang poker: {base_topic}"
        secondary = ""
        topic_line = ""

    prompt = (
        f"Sinh đúng 3 search queries cho Tavily để tìm story thật trong domain \"{domain}\".\n\n"
        f"{primary}{secondary}\n"
        f"{topic_line}\n"
        f"Domain hints: {hints}{avoid_note}\n\n"
        f"Yêu cầu:\n"
        f"- Query 1: tìm đúng story pattern trên — nhân vật/sự kiện khớp với pattern\n"
        f"- Query 2: tìm chiều ngược — ai làm đúng, thành công vì sao\n"
        f"- Query 3: tìm góc khác hoặc nhân vật khác cùng pattern\n"
        f"- Mỗi query: nhân vật thật, sự kiện thật, 8-12 từ tiếng Anh\n"
        f"- Ưu tiên 2020-2025, tên quen với người Việt Nam 23-40 tuổi\n\n"
        f"Trả về đúng 3 dòng, mỗi dòng 1 query, không đánh số, không giải thích."
    )
    try:
        resp = _client.messages.create(
            model=SCANNER_MODEL,
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}],
        )
        _print_usage("Query Generator (Haiku)", resp.usage.input_tokens, resp.usage.output_tokens)
        queries = [l.strip() for l in resp.content[0].text.strip().split("\n") if l.strip()]
        return queries[:3] if queries else [f"{domain} {query} 2024 real story"]
    except Exception as e:
        print(f"  ⚠️ Query generator lỗi: {e} — dùng fallback query")
        return [
            f"{domain} {query} 2024 real story",
            f"{domain} surprising decision upset 2024 2025",
            f"{domain} Vietnam notable event comeback 2024",
        ]


def _scan_domain_mini_deepseek(query: str, domain: str, hints: str, scanner_system: str,
                               used_stories: list[str] | None = None) -> tuple[str, int, int, int]:
    """Scan 1 domain dùng Tavily search + DeepSeek analysis."""
    # Claude Haiku sinh queries thông minh dựa trên context
    search_queries = _generate_search_queries(query, domain, hints, used_stories or [])
    searches = [_tavily_search(q, max_results=5) for q in search_queries]
    search_count = len(searches)
    search_context = "\n\n========\n\n".join(_format_tavily(s) for s in searches)

    avoid_note = ""
    if used_stories:
        avoid_note = (
            f"\n\n⚠️ CÁC STORY SAU ĐÃ ĐƯỢC DÙNG RỒI — TUYỆT ĐỐI KHÔNG DÙNG LẠI:\n"
            + "\n".join(f"- {s}" for s in used_stories[:8])
            + "\nPhải tìm story HOÀN TOÀN KHÁC — nhân vật khác, sự kiện khác.\n"
        )

    # Highlight STORY PATTERN cho DeepSeek analyzer
    m_pattern = re.search(r'STORY PATTERN cần tìm:\s*([^\n]+)', query)
    pattern_note = f"\n\n⭐ STORY PATTERN CẦN TÌM: {m_pattern.group(1).strip()}" if m_pattern else ""
    base_topic = query.split("\n\nSTORY PATTERN")[0].split("\n\nANCHOR:")[0].strip()

    user = (
        f"Kết quả tìm kiếm về '{base_topic}' trong domain {domain}:{pattern_note}\n\n"
        f"{search_context}\n\n"
        f"{'='*60}\n\n"
        f"Focus ONLY on {domain} domain ({hints}).{avoid_note}\n"
        f"Output đúng 1 STORY block theo format chuẩn rồi dừng — không cần SUMMARY.\n\n"
        f"YÊU CẦU BẮT BUỘC:\n"
        f"- Nhân vật: giải thích họ là ai, vai trò, tại sao relevant (không chỉ nêu tên)\n"
        f"- Setup/Conflict/Mechanism/Payoff: 4 field riêng biệt, không gộp\n"
        f"- Mechanism: liệt kê từng action cụ thể theo thứ tự — writer không thể tự bịa\n"
        f"- Số liệu: phải có ngữ cảnh (VD: '80,000 vs 50,000 — tỷ lệ 1.6:1')\n"
        f"- Payoff: emotional beat (câu nói thật, phản ứng cụ thể)\n"
        f"- Bridge Point 2 chiều:\n"
        f"  Chiều sai = [hành động cụ thể] → [hậu quả cụ thể]\n"
        f"  Chiều đúng = [hành động cụ thể] → [cơ chế cơ học] → [kết quả]"
    )

    resp = _deepseek_client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=[
            {"role": "system", "content": scanner_system},
            {"role": "user", "content": user},
        ],
        max_tokens=3500,
        temperature=0.7,
    )
    text   = resp.choices[0].message.content or ""
    in_tok = resp.usage.prompt_tokens
    out_tok = resp.usage.completion_tokens
    return text, in_tok, out_tok, search_count


def _run_router_deepseek(query: str, recent: list[str]) -> list[str]:
    """Chọn 2 domain phù hợp nhất dùng DeepSeek."""
    all_domains = [name for name, _ in _SCAN_DOMAINS]
    # Loại domain đã dùng gần đây khỏi danh sách nếu còn đủ lựa chọn
    available = [d for d in all_domains if d not in recent[:2]]
    if len(available) < 2:
        available = all_domains
    domain_list = "\n".join(f"- {name}" for name in available)
    recent_note = f"\nDomain ĐÃ DÙNG GẦN ĐÂY — KHÔNG ĐƯỢC CHỌN LẠI: {', '.join(recent[:3])}" if recent else ""
    prompt = (
        f"Topic: \"{query}\"{recent_note}\n\n"
        f"Chọn đúng 2 domain phù hợp nhất để tìm story (chỉ chọn từ danh sách sau):\n{domain_list}\n\n"
        f'Trả về JSON: {{"domains": ["Domain 1", "Domain 2"]}}'
    )
    resp = _deepseek_client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=80,
        temperature=0,
    )
    import json as _json
    try:
        text = resp.choices[0].message.content or ""
        text = re.search(r'\{.*\}', text, re.DOTALL).group()
        domains = _json.loads(text).get("domains", [])
        valid = [d for d in domains if d in _SCAN_DOMAIN_MAP]
        if len(valid) >= 2:
            return valid[:2]
    except Exception:
        pass
    return [_SCAN_DOMAINS[0][0], _SCAN_DOMAINS[1][0]]


def _eval_with_deepseek(query: str, combined: str, used_stories: list[str] | None = None) -> tuple[str, int, int]:
    """Evaluate candidates dùng DeepSeek."""
    avoid = ""
    if used_stories:
        avoid = (
            "\n\n⚠️ CÁC STORY SAU ĐÃ ĐƯỢC DÙNG RỒI — KHÔNG CHỌN LẠI:\n"
            + "\n".join(f"- {s}" for s in used_stories[:8])
        )
    deepseek_note = (
        "\n\nQUAN TRỌNG: Output PHẢI giữ nguyên 100% nội dung gốc của từng story được chọn "
        "— bao gồm toàn bộ STORY THÔ (Tiêu đề, Nhân vật, Bối cảnh, Setup, Conflict, Mechanism, Payoff), "
        "BRIDGE POINT, và VERDICT. KHÔNG tóm tắt, KHÔNG rút gọn, KHÔNG dùng bảng thay thế nội dung. "
        "Chỉ thêm phần ĐÁNH GIÁ và SUMMARY ở cuối sau khi đã copy đầy đủ story gốc."
    )
    resp = _deepseek_client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=[
            {"role": "system", "content": _EVALUATE_PROMPT + avoid + deepseek_note},
            {"role": "user", "content": f"Query: {query}\n\n{combined}"},
        ],
        max_tokens=8000,
        temperature=0.5,
    )
    text   = resp.choices[0].message.content or ""
    in_tok = resp.usage.prompt_tokens
    out_tok = resp.usage.completion_tokens
    return text, in_tok, out_tok


def _print_usage(label: str, input_tokens: int, output_tokens: int,
                 search_calls: int = 0, haiku: bool = False) -> None:
    cost = _calc_cost(input_tokens, output_tokens, search_calls, haiku)
    entry = {"label": label, "input": input_tokens, "output": output_tokens,
             "searches": search_calls, "cost": cost}
    with _session_lock:
        _session_usage.append(entry)
    search_note = f" + {search_calls} searches" if search_calls else ""
    print(f"  📊 {label}: {input_tokens:,} in + {output_tokens:,} out{search_note} = ${cost:.4f}")


def print_cost_summary() -> None:
    if not _session_usage:
        return
    print(f"\n{'='*60}")
    print("  COST BREAKDOWN")
    print(f"{'='*60}")
    total_in = total_out = total_searches = total_cost = 0
    for u in _session_usage:
        searches = u.get("searches", 0)
        s_note = f" {searches:>3}s" if searches else "    "
        print(f"  {u['label']:<25} {u['input']:>7,} in  {u['output']:>6,} out{s_note}  ${u['cost']:.4f}")
        total_in      += u["input"]
        total_out     += u["output"]
        total_searches += searches
        total_cost    += u["cost"]
    print(f"  {'─'*57}")
    s_note = f" {total_searches:>3}s" if total_searches else "    "
    print(f"  {'TOTAL':<25} {total_in:>7,} in  {total_out:>6,} out{s_note}  ${total_cost:.4f}")
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


def get_recent_stories(n: int = 6) -> list[str]:
    """Đọc n story files gần nhất, trả về danh sách nhân vật/tiêu đề đã dùng."""
    story_dir = Path("outputs/stories")
    if not story_dir.exists():
        return []
    files = sorted(
        [f for f in story_dir.glob("*.md")
         if not f.name.startswith("_") and "_brief" not in f.name],
        key=lambda f: f.stat().st_mtime, reverse=True
    )[:n]
    names = []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
            found = re.findall(r'Tiêu đề\s*[:：]\s*([^\n]+)', text)
            found += re.findall(r'Nhân vật\s*[:：]\s*([^\n]+)', text)
            for line in found[:3]:
                name = line.strip()[:80]
                if name and name not in names:
                    names.append(name)
        except Exception:
            pass
    return names


def get_recent_domains(n: int = 4) -> list[str]:
    """Đọc n story files gần nhất, trả về danh sách domain đã dùng."""
    import re
    story_dir = Path("outputs/stories")
    if not story_dir.exists():
        return []
    files = sorted(
        [f for f in story_dir.glob("*.md")
         if not f.name.startswith("_") and not f.name.startswith("brief_")],
        key=lambda f: f.stat().st_mtime, reverse=True
    )[:n]
    domains = []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
            found = re.findall(r'DOMAIN\s*[:：]\s*([^\n(]+)', text)
            for d in found:
                d = d.strip()
                for name in _SCAN_DOMAIN_MAP:
                    if name.lower() in d.lower() and name not in domains:
                        domains.append(name)
                        break
        except Exception:
            pass
    return domains


def run_router(query: str, strategy_domain: str = "") -> list[str]:
    """Chọn đúng 2 domain phù hợp nhất với topic. Chi phí ~$0.001."""
    # Nếu strategy đã chỉ định domain → dùng luôn, không gọi AI
    if strategy_domain and strategy_domain in _SCAN_DOMAIN_MAP:
        recent = get_recent_domains(4)
        # Chọn domain bổ sung khác với domain chính
        backup = next(
            (name for name, _ in _SCAN_DOMAINS if name != strategy_domain and name not in recent[:2]),
            next((name for name, _ in _SCAN_DOMAINS if name != strategy_domain), _SCAN_DOMAINS[1][0])
        )
        print(f"  [Router → Strategy: {strategy_domain} + {backup}]")
        return [strategy_domain, backup]
    import json
    recent = get_recent_domains(4)

    if _deepseek_client:
        print("  [Router → DeepSeek]")
        return _run_router_deepseek(query, recent)

    recent_note = ""
    if recent:
        recent_note = f"\n\nDomain đã dùng gần đây (ưu tiên TRÁNH lặp lại nếu có lựa chọn thay thế tốt): {', '.join(recent[:3])}"
    response = _client.messages.create(
        model=MODEL,
        max_tokens=60,
        system=_ROUTER_PROMPT,
        messages=[{"role": "user", "content": query + recent_note}],
    )
    _print_usage("Router", response.usage.input_tokens, response.usage.output_tokens)
    try:
        domains = json.loads(response.content[0].text).get("domains", [])
        valid = [d for d in domains if d in _SCAN_DOMAIN_MAP]
        if len(valid) >= 2:
            return valid[:2]
        if len(valid) == 1:
            for name, _ in _SCAN_DOMAINS:
                if name not in valid:
                    return [valid[0], name]
    except Exception:
        pass
    return [_SCAN_DOMAINS[0][0], _SCAN_DOMAINS[1][0]]  # fallback


def _has_structured_topic(query: str) -> bool:
    """Kiểm tra topic đã có CONCEPT/CHIỀU SAI/CHIỀU ĐÚNG chưa."""
    q = query.upper()
    return "CONCEPT" in q and ("CHIỀU SAI" in q or "CHIEU SAI" in q)


def parse_strategy(text: str) -> dict:
    """Parse <STRATEGY> block thành dict các field."""
    m = re.search(r'<STRATEGY>(.*?)</STRATEGY>', text, re.DOTALL)
    if not m:
        return {}
    raw = m.group(1)
    fields = {}
    for line in raw.splitlines():
        if ':' in line:
            key, _, val = line.partition(':')
            k = key.strip().upper().replace(' ', '_')
            fields[k] = val.strip()
    return fields


def run_anchor_template(topic: str) -> tuple[str, dict]:
    """Hội thoại 2-3 lượt để làm rõ topic, output STRATEGY đầy đủ.
    Trả về (raw_strategy_text, parsed_fields_dict)."""
    print(f"\n{'='*60}")
    print(f"  STRATEGY BUILDER — {topic}")
    print(f"  (Trả lời để làm rõ góc viết. Ctrl+C để bỏ qua.)")
    print(f"{'='*60}\n")

    messages = [{"role": "user", "content": f"Topic: {topic}"}]

    for turn in range(6):
        response = _with_retry(lambda: _client.messages.create(
            model=MODEL,
            max_tokens=1200,
            system=_ANCHOR_TEMPLATE_SYSTEM,
            messages=messages,
        ))
        _print_usage(f"Strategy Builder lượt {turn + 1}", response.usage.input_tokens, response.usage.output_tokens)

        ai_text = response.content[0].text.strip()

        # Kiểm tra đã có STRATEGY output chưa
        if '<STRATEGY>' in ai_text:
            outside = re.sub(r'<STRATEGY>.*?</STRATEGY>', '', ai_text, flags=re.DOTALL).strip()
            if outside:
                print(f"{outside}\n")

            fields = parse_strategy(ai_text)
            print("\n" + "━"*60)
            print("  STRATEGY ĐÃ SẴN SÀNG:")
            for k, v in fields.items():
                label = k.replace('_', ' ').title()
                print(f"  {label:<14}: {v}")
            print("━"*60)

            confirm = input("\nOK dùng strategy này? (Enter = yes / n = chỉnh lại): ").strip().lower()
            if confirm == 'n':
                custom = input("Nhập field cần chỉnh (VD: DOMAIN: MMA Boxing): ").strip()
                if custom and ':' in custom:
                    k, _, v = custom.partition(':')
                    fields[k.strip().upper().replace(' ', '_')] = v.strip()
                    print("✓ Đã cập nhật.\n")
            return ai_text, fields

        print(f"{ai_text}\n")
        messages.append({"role": "assistant", "content": ai_text})

        try:
            user_input = input("> ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n  (Bỏ qua strategy builder — dùng topic gốc)")
            return topic, {}

        if not user_input:
            break
        messages.append({"role": "user", "content": user_input})

    return topic, {}


def run_anchor_chat(topic: str) -> str:
    """Hội thoại 2-3 lượt để làm rõ anchor từ topic thô.
    Trả về anchor string để feed vào Template + Scanner."""
    print(f"\n{'='*60}")
    print(f"  ANCHOR CHAT — {topic}")
    print(f"  (Trả lời để làm rõ góc viết. Ctrl+C để bỏ qua.)")
    print(f"{'='*60}\n")

    messages = [{"role": "user", "content": f"Topic: {topic}"}]

    for turn in range(5):
        response = _with_retry(lambda: _client.messages.create(
            model=MODEL,
            max_tokens=500,
            system=_ANCHOR_CHAT_SYSTEM,
            messages=messages,
        ))
        _print_usage(f"Anchor Chat lượt {turn + 1}", response.usage.input_tokens, response.usage.output_tokens)

        ai_text = response.content[0].text.strip()

        anchor_match = re.search(r'<ANCHOR>(.*?)</ANCHOR>', ai_text, re.DOTALL)
        if anchor_match:
            anchor = anchor_match.group(1).strip()
            # In phần ngoài tag (câu tóm tắt của AI) nếu có
            outside = re.sub(r'<ANCHOR>.*?</ANCHOR>', '', ai_text, flags=re.DOTALL).strip()
            if outside:
                print(f"{outside}\n")
            print("━"*60)
            print("  ANCHOR ĐÃ RÕ:")
            for line in anchor.splitlines():
                print(f"  {line}")
            print("━"*60)
            confirm = input("\nOK, dùng anchor này? (Enter = yes / n = chỉnh lại): ").strip().lower()
            if confirm == 'n':
                custom = input("Chỉnh anchor (hoặc Enter để giữ nguyên): ").strip()
                return custom if custom else anchor
            return anchor

        print(f"{ai_text}\n")
        messages.append({"role": "assistant", "content": ai_text})

        try:
            user_input = input("> ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n  (Bỏ qua anchor chat — dùng topic gốc)")
            return topic

        if not user_input:
            break
        messages.append({"role": "user", "content": user_input})

    return topic


def run_template(topic: str) -> list[str]:
    """Sinh 3 angle templates từ topic thô. Trả về list 3 string, mỗi string là 1 template."""
    print(f"\n{'='*60}")
    print(f"  TEMPLATE GENERATOR — {topic}")
    print(f"{'='*60}\n")
    response = _with_retry(lambda: _client.messages.create(
        model=MODEL,
        max_tokens=800,
        system=_TEMPLATE_PROMPT,
        messages=[{"role": "user", "content": f"Topic: {topic}"}],
    ))
    _print_usage("Template Generator (Sonnet)", response.usage.input_tokens, response.usage.output_tokens)
    raw = response.content[0].text.strip()

    # Tách thành 3 blocks — mỗi block bắt đầu bằng "CONCEPT"
    blocks = re.split(r'\n(?=CONCEPT\s)', raw)
    templates = [b.strip() for b in blocks if b.strip() and "CONCEPT" in b.upper()]
    return templates[:3] if len(templates) >= 3 else templates


def pick_template(templates: list[str], topic: str) -> str:
    """Hiển thị 3 templates, hỏi user chọn hoặc nhập tay. Trả về query cuối cùng."""
    print(f"\n{'='*60}")
    print(f"  CHỌN ANGLE — {topic}")
    print(f"{'='*60}\n")
    for i, t in enumerate(templates, 1):
        print(f"  [{i}]")
        for line in t.splitlines():
            print(f"      {line}")
        print()
    print("  [0] Nhập angle của riêng mình")
    print()
    valid = [str(i) for i in range(len(templates) + 1)]
    while True:
        choice = input(f"Chọn angle ({'/'.join(valid)}): ").strip()
        if choice == "0":
            print("Nhập topic với CONCEPT/CHIỀU SAI/CHIỀU ĐÚNG:")
            custom = input("> ").strip()
            return custom if custom else topic
        if choice in valid[1:]:
            idx = int(choice) - 1
            chosen = templates[idx]
            print(f"\n✓ Dùng angle {choice}.\n")
            return f"{topic}\n{chosen}"
        print(f"❌ Nhập {' hoặc '.join(valid)}")


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
        with client.messages.stream(**kwargs) as stream:
            search_calls = 0
            for event in stream:
                if not hasattr(event, "type"):
                    continue
                if event.type == "content_block_start":
                    if hasattr(event, "content_block"):
                        if getattr(event.content_block, "type", "") == "tool_use":
                            if getattr(event.content_block, "name", "") == "web_search":
                                search_calls += 1
                                print(f"\n🔍 Searching...", end="", flush=True)
                elif event.type == "content_block_delta":
                    if hasattr(event, "delta") and hasattr(event.delta, "text"):
                        t = event.delta.text
                        print(t, end="", flush=True)
                        full_response += t
            final = stream.get_final_message()
            total_in  = final.usage.input_tokens
            total_out = final.usage.output_tokens
        return full_response, total_in, total_out, search_calls

    full_response, total_in, total_out, search_calls = _with_retry(_do_stream, max_retries, retry_wait)
    print(f"\n\n{'='*60}\n")
    _print_usage(label, total_in, total_out, search_calls=search_calls)
    return full_response


def run_checker(article: str, source_label: str = "") -> str:
    system = load_agent(CHECKER_AGENT)
    user = f"""Verify tất cả facts trong bài viết này trước khi đăng.

=== BÀI VIẾT ===
{article}
=== END ===

Xuất đầy đủ FACT CHECK REPORT theo format đã định, bao gồm phần BÀI SAU KHI FIX nếu có lỗi."""
    return stream_agent(system, user,
                        label=f"FACT CHECKER — {source_label or 'bài viết'}",
                        max_tokens=6000, use_search=True)


def _generate_queries_from_pattern(story_pattern: str, chieu_sai: str,
                                    chieu_dung: str, concept: str,
                                    used_stories: list[str]) -> list[str]:
    """Sinh 3 Tavily queries trực tiếp từ STORY PATTERN — không cần domain."""
    avoid_note = ""
    if used_stories:
        avoid_note = f"\nStory đã dùng rồi — KHÔNG tìm lại: {', '.join(s[:50] for s in used_stories[:5])}"

    prompt = (
        f"Sinh đúng 3 search queries để tìm story thật trên internet.\n\n"
        f"STORY PATTERN cần tìm: {story_pattern}\n"
        f"Chiều sai (ai làm sai → hậu quả): {chieu_sai}\n"
        f"Chiều đúng (ai làm đúng → thành công): {chieu_dung}\n"
        f"Concept: {concept}{avoid_note}\n\n"
        f"Yêu cầu:\n"
        f"- Query 1: tìm story CHIỀU SAI — nhân vật thật làm điều trong 'chiều sai' và chịu hậu quả cụ thể\n"
        f"- Query 2: tìm story CHIỀU ĐÚNG — nhân vật thật làm ngược lại và thành công\n"
        f"- Query 3: tìm story nổi tiếng nhất khớp với pattern tổng thể — bất kỳ domain nào\n"
        f"- KHÔNG giới hạn domain — tìm ở bất kỳ lĩnh vực nào có story tốt nhất\n"
        f"- Mỗi query: tên người thật hoặc sự kiện thật, 8-12 từ tiếng Anh\n"
        f"- Ưu tiên nhân vật quen với người Việt 23-40 tuổi\n\n"
        f"Trả về đúng 3 dòng, mỗi dòng 1 query, không đánh số, không giải thích."
    )
    try:
        resp = _client.messages.create(
            model=SCANNER_MODEL,
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}],
        )
        _print_usage("Query Generator Pattern (Haiku)", resp.usage.input_tokens, resp.usage.output_tokens)
        queries = [l.strip() for l in resp.content[0].text.strip().split("\n") if l.strip()]
        return queries[:3] if queries else [story_pattern[:80]]
    except Exception as e:
        print(f"  ⚠️ Pattern query generator lỗi: {e}")
        return [
            f"{chieu_sai[:60]} real story consequence",
            f"{chieu_dung[:60]} success case example",
            f"{story_pattern[:60]} famous case history",
        ]


def _scan_by_pattern_haiku(story_pattern: str, chieu_sai: str, chieu_dung: str,
                            concept: str, scanner_system: str) -> tuple[str, int, int, int]:
    """Scan bằng STORY PATTERN trực tiếp — không giới hạn domain. Dùng Haiku + web_search."""
    user = (
        f"Tìm story thật khớp với pattern sau — KHÔNG giới hạn domain:\n\n"
        f"STORY PATTERN: {story_pattern}\n\n"
        f"CHIỀU SAI (tìm ai làm điều này → hậu quả cụ thể):\n{chieu_sai}\n\n"
        f"CHIỀU ĐÚNG (tìm ai làm ngược lại → thành công):\n{chieu_dung}\n\n"
        f"CONCEPT cần bridge sang poker: {concept}\n\n"
        f"{'='*50}\n"
        f"Search 3 lần — mỗi lần một góc khác nhau:\n"
        f"  Search 1: story về chiều sai — ai làm điều đó và chịu hậu quả\n"
        f"  Search 2: story về chiều đúng — ai làm ngược lại và thành công\n"
        f"  Search 3: story nổi tiếng nhất khớp pattern — từ bất kỳ domain\n\n"
        f"Ưu tiên: nhân vật có tên thật, sự kiện verify được, quen với Nam 23-40 tuổi HCM.\n"
        f"Output 1-2 STORY block tốt nhất theo format chuẩn.\n\n"
        f"YÊU CẦU BẮT BUỘC:\n"
        f"- Nhân vật: tên + vai trò + tại sao relevant\n"
        f"- Setup/Conflict/Mechanism/Payoff: 4 field riêng biệt\n"
        f"- Mechanism: từng action cụ thể theo thứ tự — không phải 'họ vượt qua'\n"
        f"- Số liệu: phải có ngữ cảnh\n"
        f"- Bridge Point 2 chiều:\n"
        f"  Chiều sai = [hành động] → [hậu quả cụ thể]\n"
        f"  Chiều đúng = [hành động] → [cơ chế cơ học] → [kết quả]"
    )
    kwargs = dict(
        model=SCANNER_MODEL,
        max_tokens=3500,
        system=scanner_system,
        messages=[{"role": "user", "content": user}],
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
    )
    try:
        result_text = ""
        search_count = 0
        in_tok = out_tok = 0
        with _client.messages.stream(**kwargs) as stream:
            for event in stream:
                if not hasattr(event, "type"):
                    continue
                if event.type == "content_block_start":
                    if hasattr(event, "content_block"):
                        if getattr(event.content_block, "type", "") == "tool_use":
                            if getattr(event.content_block, "name", "") == "web_search":
                                search_count += 1
                                print(f"  🔍 Pattern search {search_count}...", end="", flush=True)
                elif event.type == "content_block_delta":
                    if hasattr(event, "delta") and hasattr(event.delta, "text"):
                        result_text += event.delta.text
            final = stream.get_final_message()
            in_tok = final.usage.input_tokens
            out_tok = final.usage.output_tokens
        return result_text, in_tok, out_tok, search_count
    except Exception as e:
        print(f"  ✗ Pattern scan lỗi: {e}")
        return "", 0, 0, 0


def _scan_by_pattern_deepseek(story_pattern: str, chieu_sai: str, chieu_dung: str,
                               concept: str, scanner_system: str,
                               used_stories: list[str]) -> tuple[str, int, int, int]:
    """Scan bằng STORY PATTERN trực tiếp dùng Tavily + DeepSeek — không giới hạn domain."""
    queries = _generate_queries_from_pattern(story_pattern, chieu_sai, chieu_dung, concept, used_stories)
    searches = [_tavily_search(q, max_results=5) for q in queries]
    search_count = len(searches)
    search_context = "\n\n========\n\n".join(_format_tavily(s) for s in searches)

    avoid_note = ""
    if used_stories:
        avoid_note = (
            "\n\n⚠️ STORY ĐÃ DÙNG RỒI — KHÔNG DÙNG LẠI:\n"
            + "\n".join(f"- {s}" for s in used_stories[:8])
        )

    user = (
        f"Kết quả tìm kiếm cho story pattern:\n"
        f"PATTERN: {story_pattern}\n"
        f"CHIỀU SAI: {chieu_sai}\n"
        f"CHIỀU ĐÚNG: {chieu_dung}\n\n"
        f"{search_context}\n\n{'='*60}\n\n"
        f"KHÔNG giới hạn domain — chọn story tốt nhất từ kết quả trên.{avoid_note}\n"
        f"Output 1-2 STORY block tốt nhất theo format chuẩn rồi dừng.\n\n"
        f"YÊU CẦU BẮT BUỘC:\n"
        f"- Nhân vật: tên + vai trò + tại sao relevant\n"
        f"- Setup/Conflict/Mechanism/Payoff: 4 field riêng biệt\n"
        f"- Mechanism: từng action cụ thể — không phải 'họ vượt qua'\n"
        f"- Số liệu: phải có ngữ cảnh\n"
        f"- Bridge Point:\n"
        f"  Chiều sai = [hành động] → [hậu quả cụ thể]\n"
        f"  Chiều đúng = [hành động] → [cơ chế cơ học] → [kết quả]"
    )
    resp = _deepseek_client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=[
            {"role": "system", "content": scanner_system},
            {"role": "user", "content": user},
        ],
        max_tokens=3500,
        temperature=0.7,
    )
    text = resp.choices[0].message.content or ""
    return text, resp.usage.prompt_tokens, resp.usage.completion_tokens, search_count


def _build_scan_user_prompt(query: str, domain: str, hints: str) -> str:
    """Xây user prompt cho scanner — highlight STORY PATTERN nếu có."""
    m_pattern = re.search(r'STORY PATTERN cần tìm:\s*([^\n]+)', query)
    m_concept = re.search(r'CONCEPT cần bridge:\s*([^\n]+)', query)
    base_topic = query.split("\n\nSTORY PATTERN")[0].split("\n\nANCHOR:")[0].strip()

    story_hint = ""
    if m_pattern:
        story_hint = (
            f"\n\n⭐ STORY PATTERN ƯU TIÊN TÌM: {m_pattern.group(1).strip()}"
            + (f"\nConcept cần mirror: {m_concept.group(1).strip()}" if m_concept else "")
            + "\nSearch queries phải nhắm đúng pattern này — không tìm story chung chung."
        )

    return (
        f"/scan {base_topic}{story_hint}\n\n"
        f"Focus ONLY on {domain} domain ({hints}). "
    )


def _scan_domain_mini(query: str, domain: str, hints: str, scanner_system: str) -> tuple[str, int, int]:
    """Scan 1 domain, không print streaming (chạy trong thread). Trả về (text, in_tok, out_tok)."""
    client = _client
    base_prompt = _build_scan_user_prompt(query, domain, hints)
    user = (
        f"{base_prompt}"
        f"Search tối đa 3 lần. "
        f"Tìm 1 story candidate tốt nhất. "
        f"Output đúng 1 STORY block theo format chuẩn rồi dừng — không cần SUMMARY.\n\n"
        f"YÊU CẦU BẮT BUỘC về độ chi tiết — Content Writer sẽ dùng trực tiếp output này:\n"
        f"- Nhân vật: KHÔNG chỉ nêu tên — phải giải thích họ là ai, vai trò gì, tại sao relevant. "
        f"VD: 'Hannibal — tướng Carthage, 29 tuổi, đang chiến đấu trên đất địch với quân ít hơn 1.6 lần' "
        f"thay vì chỉ 'Hannibal'.\n"
        f"- Setup/Conflict/Mechanism/Payoff: điền đủ 4 field riêng biệt — KHÔNG gộp vào 1 field 'Tình huống'. "
        f"Mechanism là phần quan trọng nhất: liệt kê từng action cụ thể nhân vật ĐÃ LÀM theo thứ tự — "
        f"không phải 'họ vượt qua', mà là bước 1 làm gì, bước 2 làm gì. Writer không thể tự bịa Mechanism.\n"
        f"- Số liệu: phải có ngữ cảnh đi kèm. VD: '80,000 vs 50,000 — tỷ lệ Rome áp đảo 1.6:1' "
        f"thay vì chỉ '80,000 và 50,000'.\n"
        f"- Payoff: phải có emotional beat — câu nói thật, phản ứng cụ thể, khoảnh khắc memorable. "
        f"Không phải chỉ kết quả số liệu.\n"
        f"- 2 chiều trong Bridge Point: "
        f"Chiều sai = [hành động cụ thể] → [hậu quả cụ thể]. "
        f"Chiều đúng = [hành động cụ thể] → [cơ chế tại sao nó work về mặt cơ học] → [kết quả]. "
        f"Cơ chế KHÔNG phải lý do cảm xúc — là lý do cơ học. "
        f"VD đạt: 'fold → stack-to-pot ratio không justify call → bảo toàn chips'. "
        f"VD không đạt: 'bình tĩnh → thắng'."
    )
    kwargs = dict(
        model=SCANNER_MODEL,
        max_tokens=3500,
        system=scanner_system,
        messages=[{"role": "user", "content": user}],
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
    )
    def _do_mini():
        text = ""
        search_calls = 0
        with client.messages.stream(**kwargs) as stream:
            for event in stream:
                if not hasattr(event, "type"):
                    continue
                if event.type == "content_block_start":
                    if hasattr(event, "content_block"):
                        if getattr(event.content_block, "type", "") == "tool_use":
                            if getattr(event.content_block, "name", "") == "web_search":
                                search_calls += 1
                elif event.type == "content_block_delta":
                    if hasattr(event, "delta") and hasattr(event.delta, "text"):
                        text += event.delta.text
            final = stream.get_final_message()
            total_in  = final.usage.input_tokens
            total_out = final.usage.output_tokens
        return text, total_in, total_out, search_calls

    return _with_retry(_do_mini, max_retries=5, wait_s=60)


def run_scanner(query: str, auto: bool = False, strategy: dict | None = None) -> str:
    """Parallel domain scan + evaluate call."""
    print(f"\n{'='*60}")
    print(f"  STORY SCANNER — {query[:80]}{'...' if len(query) > 80 else ''}")
    print(f"{'='*60}\n")

    strategy = strategy or {}

    # Parse từ query string nếu strategy dict chưa có — hỗ trợ cả Telegram flow
    def _extract(pattern_re: str, text: str) -> str:
        m = re.search(pattern_re, text, re.IGNORECASE)
        return m.group(1).strip() if m else ""

    story_pattern = (strategy.get("STORY_PATTERN")
                     or _extract(r'STORY[_ ]PATTERN[^:]*:\s*([^\n]+)', query)
                     or _extract(r'Story pattern:\s*([^\n]+)', query))
    chieu_sai     = (strategy.get("CHIỀU_SAI")
                     or _extract(r'CHI[ÊE]U[_ ]SAI[^:]*:\s*([^\n]+)', query)
                     or _extract(r'Chiều sai:\s*([^\n]+)', query))
    chieu_dung    = (strategy.get("CHIỀU_ĐÚNG")
                     or _extract(r'CHI[ÊE]U[_ ][ĐD][ÚU]NG[^:]*:\s*([^\n]+)', query)
                     or _extract(r'Chiều đúng:\s*([^\n]+)', query))
    concept       = (strategy.get("CONCEPT")
                     or _extract(r'CONCEPT[^:]*:\s*([^\n]+)', query)
                     or _extract(r'Khía cạnh:\s*([^\n]+)', query))

    scanner_system = load_agent(SCANNER_AGENT)
    domain_results: dict[str, str] = {}
    total_in = total_out = total_searches = 0

    use_deepseek = bool(_deepseek_client)
    scan_label = "DeepSeek" if use_deepseek else "Haiku"
    used_stories = get_recent_stories(6) if use_deepseek else []
    if used_stories:
        print(f"  Story đã dùng gần đây (sẽ tránh): {len(used_stories)} entries")

    # ── PATTERN SCAN: khi có STORY PATTERN rõ → search trực tiếp, bỏ domain filter ──
    if story_pattern:
        print(f"  [Pattern Scan — không giới hạn domain]")
        print(f"  Pattern: {story_pattern[:80]}...\n")
        try:
            if use_deepseek:
                text, in_tok, out_tok, searches = _scan_by_pattern_deepseek(
                    story_pattern, chieu_sai, chieu_dung, concept, scanner_system, used_stories
                )
            else:
                text, in_tok, out_tok, searches = _scan_by_pattern_haiku(
                    story_pattern, chieu_sai, chieu_dung, concept, scanner_system
                )
            domain_results["Pattern"] = text
            total_in += in_tok
            total_out += out_tok
            total_searches += searches
            print(f"\n  ✓ Pattern scan xong ({searches} searches)")
        except Exception as e:
            print(f"  ✗ Pattern scan lỗi: {e} — fallback sang domain scan")
            story_pattern = ""  # trigger fallback

    # ── DOMAIN SCAN: fallback khi không có STORY PATTERN ──
    if not story_pattern:
        strategy_domain = strategy.get("DOMAIN", "")
        chosen_domains = run_router(query, strategy_domain=strategy_domain)
        print(f"  [Domain Scan] Domains: {', '.join(chosen_domains)}\n")

        scan_query = query
        if concept:
            scan_query += f"\nCONCEPT cần bridge: {concept}"

        for domain in chosen_domains:
            hints = _SCAN_DOMAIN_MAP.get(domain, "")
            print(f"  Scanning {domain} [{scan_label}]...")
            try:
                if use_deepseek:
                    text, in_tok, out_tok, searches = _scan_domain_mini_deepseek(
                        scan_query, domain, hints, scanner_system, used_stories
                    )
                else:
                    text, in_tok, out_tok, searches = _scan_domain_mini(scan_query, domain, hints, scanner_system)
                domain_results[domain] = text
                total_in       += in_tok
                total_out      += out_tok
                total_searches += searches
                print(f"  ✓ {domain} ({searches} searches)")
            except Exception as e:
                print(f"  ✗ {domain}: {e}")

    scanner_cost = _calc_cost(total_in, total_out, total_searches,
                              haiku=not use_deepseek, deepseek=use_deepseek)
    with _session_lock:
        _session_usage.append({
            "label": f"Scanner ({scan_label})", "input": total_in, "output": total_out,
            "searches": total_searches, "cost": scanner_cost,
        })
    s_note = f" + {total_searches} searches" if total_searches else ""
    print(f"  📊 Scanner ({scan_label}): {total_in:,} in + {total_out:,} out{s_note} = ${scanner_cost:.4f}")

    # Cache domain results phòng evaluate crash
    _cache_dir = Path("outputs/stories")
    _cache_dir.mkdir(parents=True, exist_ok=True)
    _safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in query)[:60]
    _cache_file = _cache_dir / f"_domains_{_safe.strip().replace(' ', '_')}.txt"
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

    if use_deepseek:
        eval_text, total_in, total_out = _eval_with_deepseek(query, combined, used_stories)
        print(eval_text)
        # Ghép full domain stories + evaluate để đảm bảo không mất nội dung
        eval_text = combined + "\n\n" + "="*60 + "\n\n" + eval_text
    else:
        eval_kwargs = dict(
            model=MODEL,
            max_tokens=6000,
            system=_EVALUATE_PROMPT,
            messages=[{"role": "user", "content": f"Query: {query}\n\n{combined}"}],
        )
        def _do_eval():
            eval_text = ""
            with _client.messages.stream(**eval_kwargs) as stream:
                for event in stream:
                    if not hasattr(event, "type"):
                        continue
                    elif event.type == "content_block_delta":
                        if hasattr(event, "delta") and hasattr(event.delta, "text"):
                            t = event.delta.text
                            print(t, end="", flush=True)
                            eval_text += t
                final = stream.get_final_message()
                total_in  = final.usage.input_tokens
                total_out = final.usage.output_tokens
            return eval_text, total_in, total_out
        eval_text, total_in, total_out = _with_retry(_do_eval, max_retries=8, wait_s=90)
    print(f"\n\n{'='*60}\n")
    _print_usage("Story Evaluator", total_in, total_out)
    return eval_text


def run_strategist(story: str, query: str, strategy: dict | None = None) -> str:
    system = load_agent(STRATEGIST_AGENT)
    recent = load_content_log()[-5:]
    history_section = ""
    if recent:
        lines = ["=== 5 BÀI GẦN NHẤT (tránh lặp angle/audience) ==="]
        for e in recent:
            lines.append(f"- [{e['date']}] {e['topic']} | {e.get('audience','')} | {e.get('angle','')}")
        history_section = "\n" + "\n".join(lines) + "\n=== END HISTORY ==="

    # Pre-seed từ strategy nếu có — giảm drift giữa user intent và brief
    strategy_section = ""
    if strategy:
        fields_to_seed = {
            "FORMAT": strategy.get("FORMAT", ""),
            "AUDIENCE": strategy.get("AUDIENCE", ""),
            "HOOK TYPE": strategy.get("HOOK_TYPE", ""),
            "CONCEPT": strategy.get("CONCEPT", ""),
            "CHIỀU SAI": strategy.get("CHIỀU_SAI", ""),
            "CHIỀU ĐÚNG": strategy.get("CHIỀU_ĐÚNG", ""),
            "EMOTIONAL ARC": strategy.get("EMOTIONAL_ARC", ""),
            "SHARE TRIGGER": strategy.get("SHARE_TRIGGER", ""),
        }
        seeded = {k: v for k, v in fields_to_seed.items() if v}
        if seeded:
            lines = ["=== STRATEGY PRE-SEEDED (dùng làm điểm bắt đầu, có thể điều chỉnh theo story thật) ==="]
            for k, v in seeded.items():
                lines.append(f"{k}: {v}")
            lines.append("=== END STRATEGY ===")
            strategy_section = "\n" + "\n".join(lines)

    user = f"Query: {query}{history_section}{strategy_section}\n\n=== STORY ĐÃ CHỌN ===\n{story}\n=== END ==="
    return stream_agent(system, user,
                        label=f"CONTENT STRATEGIST — {query}",
                        max_tokens=1500, use_search=False)


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


def run_writer(story: str, query: str, brief: str = "",
               recent_log: list | None = None) -> str:
    system = load_agent(WRITER_AGENT)
    brief_section = f"\n=== MARKETING BRIEF ===\n{brief}\n=== END BRIEF ===" if brief else ""

    hooks_section = ""
    if recent_log:
        lines = ["=== 5 BÀI GẦN NHẤT (tránh lặp hook, cấu trúc mở bài, angle) ==="]
        for e in recent_log:
            lines.append(
                f"- [{e.get('date','')}] {e.get('topic','')} | "
                f"audience: {e.get('audience','')} | angle: {e.get('angle','')}"
            )
        lines.append("=== END HOOKS ===")
        hooks_section = "\n" + "\n".join(lines)

    user = f"""Dưới đây là story đã chọn và marketing brief.{brief_section}{hooks_section}

=== STORY ===
{story}
=== END ===

Viết bài theo lamwork style, bám sát AUDIENCE / GOAL / ANGLE / POKER LENS / CTA trong brief.
Nếu có danh sách 5 bài gần nhất — tránh dùng cùng cấu trúc hook hoặc cùng angle đã xuất hiện gần đây.
Xuất đủ PHẦN 1 (bài viết) và PHẦN 2 (checklist verify)."""
    return stream_agent(system, user,
                        label=f"CONTENT WRITER — {query}",
                        max_tokens=3000, use_search=False)


def run_reviewer(article: str, query: str, brief: str = "") -> str:
    system = load_agent(REVIEWER_AGENT)
    brief_section = (
        f"\n=== MARKETING BRIEF (để check Writer có follow đúng AUDIENCE/GOAL/CTA không) ===\n"
        f"{brief}\n=== END BRIEF ==="
        if brief else ""
    )
    user = f"""Đánh giá chất lượng bài viết này theo 7 tiêu chí.{brief_section}

=== BÀI VIẾT ===
{article}
=== END ===

Chấm điểm thật, đề xuất cụ thể.
Nếu có brief — kiểm tra thêm: CTA trong bài có khớp với GOAL trong brief không? Tone có phù hợp AUDIENCE không?"""
    return stream_agent(system, user,
                        label=f"CONTENT REVIEWER — {query}",
                        max_tokens=3000, use_search=False)


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


def auto_pick_story(scanner_output: str) -> str:
    """Tự chọn story tốt nhất không cần input: ưu tiên STRONG, fallback MODERATE, fallback đầu tiên."""
    stories = parse_stories(scanner_output)
    if not stories:
        return scanner_output
    for s in stories:
        if "STRONG" in s.get("bridge", "").upper() and "✅" in s.get("verdict", ""):
            print(f"  Auto-chọn: [{s['index']}] {s['title']} (STRONG + Verified)")
            return s["content"]
    for s in stories:
        if "STRONG" in s.get("bridge", "").upper():
            print(f"  Auto-chọn: [{s['index']}] {s['title']} (STRONG)")
            return s["content"]
    for s in stories:
        if "MODERATE" in s.get("bridge", "").upper():
            print(f"  Auto-chọn: [{s['index']}] {s['title']} (MODERATE)")
            return s["content"]
    print(f"  Auto-chọn: [{stories[0]['index']}] {stories[0]['title']} (fallback đầu tiên)")
    return stories[0]["content"]


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


def pick_story_for_step(story_path: Path, story_num: int | None = None,
                        auto: bool = False) -> str:
    """Trả về content của story đã chọn từ file — dùng cho --step brief/write.

    Logic:
    - File chỉ có 1 story  → dùng luôn, không hỏi
    - --story N            → pick story #N
    - --auto               → pick STRONG tốt nhất (không hỏi)
    - Còn lại              → hiển thị danh sách + hỏi user
    """
    raw = story_path.read_text(encoding="utf-8")
    stories = parse_stories(raw)

    if not stories:
        print("  (Không parse được stories riêng lẻ — dùng toàn bộ file)")
        return raw

    if len(stories) == 1:
        print(f"  Story duy nhất: {stories[0]['title']}")
        return stories[0]["content"]

    # Hiển thị nhanh để user biết có gì trong file
    print(f"\n  File có {len(stories)} stories:")
    for s in stories:
        b = s["bridge"].split("—")[0].strip() if s.get("bridge") else ""
        tag = f" [{b}]" if b else ""
        print(f"    [{s['index']}] {s['title']}{tag}")

    if story_num is not None:
        selected = next((s for s in stories if s["index"] == story_num), None)
        if selected:
            print(f"  Chọn story #{story_num}: {selected['title']}")
            return selected["content"]
        print(f"  ⚠️  Story #{story_num} không tồn tại — dùng story #1")
        return stories[0]["content"]

    if auto:
        return auto_pick_story(raw)

    return pick_story(raw)


def extract_fixed_article(checker_output: str) -> str | None:
    """Trích phần 'BÀI SAU KHI FIX' từ fact checker output. Trả về None nếu không có hoặc không phải bài thật."""
    markers = ["## BÀI SAU KHI FIX", "BÀI SAU KHI FIX"]
    for marker in markers:
        if marker in checker_output:
            parts = checker_output.split(marker, 1)
            body = parts[1].strip()
            # Bỏ phần "LƯU Ý CHO NGƯỜI ĐĂNG" nếu có
            for end_marker in ["## LƯU Ý", "LƯU Ý CHO NGƯỜI ĐĂNG", "---\n\n## "]:
                if end_marker in body:
                    body = body.split(end_marker)[0].strip()
                    break
            # Bỏ dòng "---" và ``` đầu nếu có
            lines = body.splitlines()
            while lines and lines[0].strip() in ("---", "```", ""):
                lines.pop(0)
            body = "\n".join(lines).strip()
            # Validation: chỉ ghi đè nếu là bài thật (không phải giải thích meta)
            # Bài thật phải dài >300 ký tự và không bắt đầu bằng câu meta
            meta_phrases = ["bài viết hiện tại", "không cần xuất", "không có claim", "không có lỗi mới"]
            first_200 = body[:200].lower()
            is_meta = any(p in first_200 for p in meta_phrases)
            if len(body) < 300 or is_meta:
                return None
            return body
    return None


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


# ── Slug-based naming ─────────────────────────────────────────────────────────
# Convention: tất cả file trong cùng 1 pipeline run dùng chung 1 slug.
#   outputs/stories/{slug}.md           ← scan output
#   outputs/stories/{slug}_brief.md     ← brief output
#   outputs/posts/{slug}.md             ← writer output
#   outputs/checks/{slug}_review.md     ← reviewer output
#   outputs/checks/{slug}_check.md      ← fact checker output
# Slug được tạo 1 lần khi scan, các step sau derive từ input file path.


def make_slug(topic: str) -> str:
    """Tạo slug từ topic + timestamp. Dùng 1 lần khi scan."""
    safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in topic)[:40]
    return safe.strip().replace(" ", "_") + f"_{datetime.now().strftime('%Y%m%d_%H%M')}"


def save_to_path(content: str, path: Path, title: str = "") -> Path:
    """Lưu content vào path cụ thể với header chuẩn."""
    path.parent.mkdir(parents=True, exist_ok=True)
    header = f"# {title or path.stem}\n_{datetime.now().strftime('%d/%m/%Y %H:%M')}_\n\n---\n\n"
    path.write_text(header + content, encoding="utf-8")
    return path


# Giữ lại save_output cho mode interactive / legacy
def save_output(content: str, label: str, output_type: str) -> Path:
    output_dir = Path("outputs") / output_type
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in label)[:40]
    path = output_dir / f"{safe.strip().replace(' ', '_')}_{timestamp}.md"
    header = f"# {label}\n_{datetime.now().strftime('%d/%m/%Y %H:%M')}_\n\n---\n\n"
    path.write_text(header + content, encoding="utf-8")
    return path


def brief_path_for(story_path: Path) -> Path:
    """Brief liên kết với story: cùng thư mục, thêm suffix _brief."""
    return story_path.parent / f"{story_path.stem}_brief.md"


def post_path_for(slug: str) -> Path:
    return Path("outputs/posts") / f"{slug}.md"


def review_path_for(slug: str) -> Path:
    return Path("outputs/checks") / f"{slug}_review.md"


def check_path_for(slug: str) -> Path:
    return Path("outputs/checks") / f"{slug}_check.md"


def get_latest_story_file() -> Path | None:
    """Story file mới nhất — bỏ qua _cache và *_brief.md."""
    d = Path("outputs/stories")
    if not d.exists():
        return None
    files = sorted(
        [f for f in d.glob("*.md")
         if not f.name.startswith("_") and not f.stem.endswith("_brief")],
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    return files[0] if files else None


def get_latest_brief_file() -> Path | None:
    """Brief file mới nhất trong outputs/stories/ — hỗ trợ cả 2 naming convention."""
    d = Path("outputs/stories")
    if not d.exists():
        return None
    files = sorted(
        [f for f in d.glob("*.md")
         if f.stem.endswith("_brief") or f.name.startswith("brief_")],
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    return files[0] if files else None


def resolve_input_file(arg_file: str | None, output_type: str, label: str) -> Path:
    """Trả về Path file input: từ arg (path hoặc slug) hoặc file mới nhất.

    Hỗ trợ 3 dạng input:
      - Không có arg → dùng file mới nhất
      - Full path    → dùng trực tiếp
      - Slug ngắn   → tự ghép thành path đúng (VD: "tilt_control_20260429_1030")
    """
    if arg_file:
        # Nếu là slug (không có / và không kết thúc .md) → ghép thành path
        is_slug = "/" not in arg_file and "\\" not in arg_file and not arg_file.endswith(".md")
        if is_slug:
            slug = arg_file.strip()
            candidates = {
                "stories":   Path("outputs/stories") / f"{slug}.md",
                "posts":     Path("outputs/posts")   / f"{slug}.md",
                "checks":    Path("outputs/checks")  / f"{slug}_review.md",
            }
            p = candidates.get(output_type, Path("outputs") / output_type / f"{slug}.md")
            if not p.exists():
                # fallback: tìm bất kỳ file nào chứa slug trong tên
                d = Path("outputs") / output_type
                matches = list(d.glob(f"*{slug}*.md")) if d.exists() else []
                if matches:
                    p = sorted(matches, key=lambda f: f.stat().st_mtime, reverse=True)[0]
                else:
                    print(f"❌ Không tìm thấy file với slug '{slug}' trong outputs/{output_type}/")
                    sys.exit(1)
        else:
            p = Path(arg_file)
            if not p.exists():
                print(f"❌ File không tìm thấy: {arg_file}")
                sys.exit(1)
        print(f"  Dùng file: {p}")
        return p

    # Không có arg → dùng file mới nhất
    if output_type == "stories":
        latest = get_latest_story_file()
    else:
        d = Path("outputs") / output_type
        files = sorted(d.glob("*.md"), key=lambda f: f.stat().st_mtime, reverse=True) if d.exists() else []
        latest = files[0] if files else None
    if latest:
        print(f"  Dùng file mới nhất: {latest}")
        return latest
    print(f"❌ Không tìm thấy file {label} nào trong outputs/{output_type}/")
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Spades Content Pipeline")
    parser.add_argument("article_file", nargs="?",
                        help="File input (.md) — dùng cho --step brief/write/review/factcheck")
    parser.add_argument("--pipeline",
                        help="Full pipeline: scan → strategist → write → review → check")
    parser.add_argument("--step",
                        choices=["template", "scan", "brief", "write", "review", "factcheck"],
                        help="Chạy 1 bước đơn lẻ")
    parser.add_argument("--topic",
                        help="Topic/query — dùng cho --step scan")
    parser.add_argument("--no-check", action="store_true",
                        help="Bỏ qua bước Fact Checker (chỉ dùng với --pipeline)")
    parser.add_argument("--no-brief", action="store_true",
                        help="Bỏ qua bước Content Strategist (chỉ dùng với --pipeline)")
    parser.add_argument("--no-review", action="store_true",
                        help="Bỏ qua bước Content Reviewer (chỉ dùng với --pipeline)")
    parser.add_argument("--auto", action="store_true",
                        help="Tự động chọn story tốt nhất, không hỏi input")
    parser.add_argument("--guided", action="store_true",
                        help="Chạy Anchor Chat trước khi scan — làm rõ góc viết qua hội thoại")
    parser.add_argument("--story", type=int, metavar="N",
                        help="Chọn story #N trong file (bỏ qua interactive picker)")
    args = parser.parse_args()

    # ── SINGLE STEP ───────────────────────────────────────────────
    if args.step:
        _session_usage.clear()

        if args.step == "template":
            topic = args.topic or (args.article_file) or input("Topic: ").strip()
            templates = run_template(topic)
            # In ra JSON để scan.md command đọc được
            import json
            print("\n__TEMPLATES__")
            print(json.dumps({"topic": topic, "templates": templates}, ensure_ascii=False))
            print("__END_TEMPLATES__")
            print_cost_summary()
            return

        if args.step == "scan":
            topic = args.topic or (args.article_file) or input("Topic: ").strip()
            result = run_scanner(topic)
            slug = make_slug(topic)
            path = save_to_path(result, Path("outputs/stories") / f"{slug}.md", topic)
            print(f"\n✓ Stories saved: {path}")
            print(f"  Slug: {slug}  (dùng slug này làm input cho các bước tiếp theo)")
            print_cost_summary()
            return

        if args.step == "brief":
            if args.article_file:
                story_path = resolve_input_file(args.article_file, "stories", "story")
            else:
                story_path = get_latest_story_file()
                if not story_path:
                    print("❌ Không tìm thấy story file nào trong outputs/stories/")
                    sys.exit(1)
                print(f"  Dùng story: {story_path}")
            slug = story_path.stem
            topic = args.topic or slug
            chosen = pick_story_for_step(story_path,
                                         story_num=args.story,
                                         auto=args.auto)
            result = run_strategist(chosen, topic)
            path = save_to_path(result, brief_path_for(story_path), f"Brief: {topic}")
            print(f"\n✓ Brief saved: {path}")
            print_cost_summary()
            return

        if args.step == "write":
            if args.article_file:
                story_path = resolve_input_file(args.article_file, "stories", "story")
            else:
                story_path = get_latest_story_file()
                if not story_path:
                    print("❌ Không tìm thấy story file nào trong outputs/stories/")
                    print("   → Chạy /scan [topic] trước")
                    sys.exit(1)
                print(f"  Dùng story: {story_path}")
            slug = story_path.stem

            # Kiểm tra brief — bắt buộc phải có trước khi write
            linked_brief = brief_path_for(story_path)
            if linked_brief.exists():
                brief = linked_brief.read_text(encoding="utf-8")
                print(f"  Dùng brief: {linked_brief}")
            else:
                print(f"❌ Chưa có brief cho story này.")
                print(f"   → Chạy: /brief {slug}")
                sys.exit(1)

            topic = args.topic or slug
            chosen = pick_story_for_step(story_path,
                                         story_num=args.story,
                                         auto=args.auto)
            recent_log = load_content_log()[-5:]
            result = run_writer(chosen, topic, brief=brief, recent_log=recent_log)
            result = result.replace(" — ", ", ").replace("— ", ", ").replace(" —", ",")
            path = save_to_path(result, post_path_for(slug), topic)
            log_article(topic, brief, path)
            print(f"\n✓ Post saved: {path}")
            print_cost_summary()
            return

        if args.step == "review":
            post_path = resolve_input_file(args.article_file, "posts", "post")
            slug = post_path.stem
            topic = args.topic or slug
            article = extract_article(post_path.read_text(encoding="utf-8"))
            # Tìm brief liên kết theo slug
            linked_brief = Path("outputs/stories") / f"{slug}_brief.md"
            if linked_brief.exists():
                brief = linked_brief.read_text(encoding="utf-8")
                print(f"  Dùng brief liên kết: {linked_brief}")
            else:
                fallback = get_latest_brief_file()
                brief = fallback.read_text(encoding="utf-8") if fallback else ""
                if fallback:
                    print(f"  ⚠️  Brief liên kết không có, dùng brief mới nhất: {fallback}")
            result = run_reviewer(article, topic, brief=brief)
            path = save_to_path(result, review_path_for(slug), f"Review: {topic}")
            print(f"\n✓ Review saved: {path}")
            print_cost_summary()
            return

        if args.step == "factcheck":
            post_path = resolve_input_file(args.article_file, "posts", "post")
            slug = post_path.stem
            topic = args.topic or slug
            article = extract_article(post_path.read_text(encoding="utf-8"))
            result = run_checker(article, topic)
            path = save_to_path(result, check_path_for(slug), f"Fact Check: {topic}")
            print(f"\n✓ Fact check saved: {path}")
            # Ghi đè file post với bài đã fix nếu có
            fixed = extract_fixed_article(result)
            if fixed:
                original = post_path.read_text(encoding="utf-8")
                header_lines = []
                for line in original.splitlines():
                    header_lines.append(line)
                    if line.strip() == "---":
                        break
                header = "\n".join(header_lines) + "\n\n"
                post_path.write_text(header + fixed, encoding="utf-8")
                print(f"✓ Post đã được cập nhật với fixes: {post_path}")
            else:
                print("  (Không có fix — bài giữ nguyên)")
            print_cost_summary()
            return

    # ── MODE 1: Full pipeline ──────────────────────────────────────
    if args.pipeline:
        query = args.pipeline
        _session_usage.clear()
        steps = "Strategy Builder → Scanner → Strategist → Writer" + ("" if args.no_check else " → Fact Checker")
        print(f"\n🔄 FULL PIPELINE: {query}")
        print(f"   {steps}\n")

        # Bước 0 — Strategy Builder (gộp anchor + template thành 1 bước sâu)
        strategy: dict = {}
        if not args.auto and not _has_structured_topic(query):
            _, strategy = run_anchor_template(query)
            # Dùng CONCEPT làm query nếu có để slug đẹp hơn
            if strategy.get("CONCEPT"):
                query = f"{query}\n\nSTRATEGY:\n" + "\n".join(
                    f"{k}: {v}" for k, v in strategy.items() if v
                )

        # Bước 1 — scan (truyền strategy để router + scanner dùng)
        scanner_out = run_scanner(query, auto=args.auto, strategy=strategy)
        slug = make_slug(args.pipeline)  # slug từ topic gốc, không phải query dài
        s_path = save_to_path(scanner_out, Path("outputs/stories") / f"{slug}.md", query)
        print(f"✓ Stories: {s_path}  [slug: {slug}]")

        # Bước 2 — chọn story
        if args.auto:
            chosen_story = auto_pick_story(scanner_out)
        else:
            chosen_story = pick_story(scanner_out)

        # Bước 3 — brief (truyền strategy để pre-seed)
        if args.no_brief:
            final_brief = ""
        else:
            raw_brief = run_strategist(chosen_story, args.pipeline, strategy=strategy)
            final_brief = raw_brief if args.auto else confirm_brief(raw_brief)
            save_to_path(final_brief, Path("outputs/stories") / f"{slug}_brief.md",
                         f"Brief: {args.pipeline}")

        # Bước 4 — viết bài
        recent_log = load_content_log()[-5:]
        writer_out = run_writer(chosen_story, args.pipeline, brief=final_brief, recent_log=recent_log)
        w_path = save_to_path(writer_out, post_path_for(slug), args.pipeline)
        log_article(args.pipeline, final_brief, w_path)
        print(f"✓ Bài viết: {w_path}")

        article = extract_article(writer_out)

        # Bước 5 — fact check (reviewer đã bỏ — strategy builder đảm nhận vai trò định hướng)
        c_path = None
        if not args.no_check:
            check_out = run_checker(article, args.pipeline)
            c_path = save_to_path(check_out, check_path_for(slug), f"Fact Check: {args.pipeline}")
            print(f"✓ Fact check: {c_path}")

        print(f"\n{'='*60}")
        print("PIPELINE HOÀN TẤT")
        print(f"  Slug    : {slug}")
        print(f"  Stories : {s_path}")
        print(f"  Bài viết: {w_path}")
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
        s_out   = run_scanner(query, auto=False)
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
