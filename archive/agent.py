#!/usr/bin/env python3
"""
Spades Content Agent
Chạy: python agent.py
"""

import anthropic
import os
import sys
from pathlib import Path
from config import SYSTEM_PROMPT, MODEL, MAX_TOKENS

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

from dotenv import load_dotenv
load_dotenv()

def run_agent(topic: str, angle: str, anchor: str, length: str, goal: str) -> str:
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    user_message = f"""Topic: {topic}
Góc độ: {angle}
Câu chuyện anchor: {anchor if anchor else "tự chọn phù hợp nhất"}
Độ dài: {length}
Mục tiêu: {goal}"""

    print(f"\n{'='*60}")
    print("SPADES CONTENT AGENT")
    print(f"{'='*60}")
    print(f"Topic: {topic}")
    print(f"{'='*60}\n")

    with client.messages.stream(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}]
    ) as stream:
        full_response = ""
        for text in stream.text_stream:
            print(text, end="", flush=True)
            full_response += text

    print(f"\n\n{'='*60}\n")
    return full_response


def interactive_mode():
    print("\n🂡  SPADES CONTENT AGENT")
    print("=" * 40)
    print("Nhập thông tin bài viết:\n")

    topic  = input("Topic (chủ đề): ").strip()
    if not topic:
        print("Topic không được để trống.")
        sys.exit(1)

    angle  = input("Góc độ [tâm lý/strategy/community/philosophy]: ").strip() or "community"
    anchor = input("Câu chuyện anchor (Enter để agent tự chọn): ").strip()
    length = input("Độ dài [ngắn ~300 / trung bình ~500 / dài ~800] (mặc định: trung bình ~500): ").strip() or "trung bình ~500 từ"
    goal   = input("Mục tiêu bài viết: ").strip() or "kết nối cộng đồng Spades"

    result = run_agent(topic, angle, anchor, length, goal)

    # Auto-save output
    save = input("\nLưu output ra file? (y/n): ").strip().lower()
    if save == "y":
        output_dir = Path("outputs")
        output_dir.mkdir(exist_ok=True)
        safe_topic = "".join(c if c.isalnum() or c in " -_" else "_" for c in topic)[:40]
        filename = output_dir / f"{safe_topic.strip().replace(' ', '_')}.md"
        filename.write_text(result, encoding="utf-8")
        print(f"✓ Đã lưu: {filename}")


if __name__ == "__main__":
    # Quick mode: python agent.py "topic" "angle" "anchor" "length" "goal"
    if len(sys.argv) >= 2:
        topic  = sys.argv[1]
        angle  = sys.argv[2] if len(sys.argv) > 2 else "community"
        anchor = sys.argv[3] if len(sys.argv) > 3 else ""
        length = sys.argv[4] if len(sys.argv) > 4 else "trung bình ~500 từ"
        goal   = sys.argv[5] if len(sys.argv) > 5 else "kết nối cộng đồng Spades"
        run_agent(topic, angle, anchor, length, goal)
    else:
        interactive_mode()
