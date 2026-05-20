"""Audit library files for incomplete entries."""
import re, sys, io
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

lib_dir = Path(__file__).parent.parent / "agents" / "shared" / "library"
issues = []
ok = 0

for f in ["tam-ly.md", "khai-niem.md", "dinh-ly.md", "co-ban.md"]:
    path = lib_dir / f
    text = path.read_text(encoding="utf-8")
    blocks = [b for b in text.split("---") if "**Tên khái niệm:**" in b]
    for block in blocks:
        ten = re.search(r"\*\*Tên khái niệm:\*\*\s*(.+)", block)
        ten_str = ten.group(1).strip()[:40] if ten else "?"
        problems = []
        for field in ["Định nghĩa", "Cơ chế tâm lý", "Chiều sai", "Chiều đúng", "Ví dụ bàn"]:
            m = re.search(rf"\*\*{field}:\*\*\s*(.+?)(?=\n\n\*\*|\*Ngu|\Z)", block, re.DOTALL)
            if not m:
                problems.append(f"MISSING: {field}")
            else:
                val = m.group(1).strip()
                if val != "N/A" and len(val) < 15:
                    problems.append(f"TRUNCATED({len(val)}c): {field} = {val[:50]}")
                elif val != "N/A" and val[-1] not in ".?)!…\"" and len(val) < 50:
                    problems.append(f"SHORT: {field} = {val[:50]}")
        if problems:
            issues.append((f, ten_str, problems))
        else:
            ok += 1

print(f"OK: {ok} entries | Issues: {len(issues)}")
for f, ten, probs in issues:
    print(f"  [{f}] {ten}")
    for p in probs:
        print(f"    {p}")
