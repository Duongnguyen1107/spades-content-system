"""
Paraphrase các cụm từ liên quan tiền thật trong library files.
Spades là no cash game — nội dung không được ngụ ý thắng/thua tiền thật.

Chạy: python scripts/fix_nocashgame.py
"""

import sys, io
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).parent.parent
LIB_DIR = BASE_DIR / "agents" / "shared" / "library"
FILES = ["tam-ly.md", "khai-niem.md", "dinh-ly.md", "co-ban.md", "index.md"]

# (pattern_exact, replacement) — thứ tự quan trọng: specific trước, general sau
REPLACEMENTS = [
    # Cụm dài trước
    ("giá trị tiền thật",           "giá trị chip thực"),
    ("kiếm tiền bền vững",          "thắng chip bền vững"),
    ("kiếm tiền không hiệu quả",    "kết quả kém dài hạn"),
    ("kiếm được tiền lợi nhuận tối đa", "thắng chip tối đa"),
    ("kiếm lợi suất cao hơn",       "khai thác hiệu quả hơn"),
    ("tối ưu hóa lợi nhuận dài hạn","tối ưu kết quả dài hạn"),
    ("tối ưu hóa lợi nhuận",        "tối ưu kết quả"),
    ("duy trì lợi nhuận lâu dài",   "duy trì kết quả tốt dài hạn"),
    ("lợi nhuận dài hạn",           "kết quả dài hạn"),
    ("lợi nhuận thực tế tích lũy",  "kết quả tích lũy dài hạn"),
    ("kiếm tiền",                   "thắng chip"),
    ("thua tiền không cần thiết",   "mất chip không cần thiết"),
    ("thua tiền",                   "mất chip"),
    ("mất tiền vô ích",             "mất chip vô ích"),
    ("mất tiền không cần thiết",    "mất chip không cần thiết"),
    ("mất tiền thêm",               "mất chip thêm"),
    ("mất tiền",                    "mất chip"),
    ("thắng tiền nhiều hơn",        "thắng pot nhiều hơn"),
    ("thắng tiền",                  "thắng chip"),
    ("thua lỗ tiền",                "mất chip"),
    ("thua lỗ lâu dài",             "thua chip dài hạn"),
    ("thua lỗ về lâu dài",          "mất chip về dài hạn"),
    ("thua lỗ dài hạn",             "mất chip dài hạn"),
    ("thua lỗ",                     "mất chip"),
    ("lời/lỗ trung bình",           "thắng/thua chip trung bình"),
    ("lời/lỗ",                      "thắng/thua chip"),
    ("áp lực tài chính",            "áp lực kết quả"),
    ("an toàn tài chính",           "ổn định kết quả"),
    ("dòng tiền",                   "kết quả theo phiên"),
    ("tiền thưởng phi tuyến tính",  "cấu trúc giải thưởng phi tuyến"),
    ("cấu trúc tiền thưởng",        "cấu trúc giải thưởng"),
    ("tiền thưởng",                 "giải thưởng thứ hạng"),
    ("giá trị $EV",                 "EV thực"),
    ("$EV",                         "EV"),
    ("lợi nhuận",                   "kết quả"),
    ("quản lý bankroll",            "quản lý stack"),
    # "bankroll" đứng độc lập giữ nguyên vì là thuật ngữ poker phổ biến
]


def apply_replacements(text: str) -> tuple[str, list[str]]:
    changes = []
    for old, new in REPLACEMENTS:
        if old in text:
            count = text.count(old)
            text = text.replace(old, new)
            changes.append(f"  '{old}' → '{new}' ({count}x)")
    return text, changes


def main():
    total_changes = 0
    for filename in FILES:
        path = LIB_DIR / filename
        if not path.exists():
            continue
        original = path.read_text(encoding="utf-8")
        updated, changes = apply_replacements(original)
        if changes:
            path.write_text(updated, encoding="utf-8")
            print(f"\n[{filename}] {len(changes)} thay đổi:")
            for c in changes:
                print(c)
            total_changes += len(changes)
        else:
            print(f"[{filename}] Không có thay đổi")

    print(f"\nTổng: {total_changes} loại cụm từ đã được replace.")


if __name__ == "__main__":
    main()
