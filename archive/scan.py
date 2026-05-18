#!/usr/bin/env python3
"""
Spades Story Scanner
Tìm story thật từ internet → bridge point sang poker

Cách chạy:
  python scan.py                        # daily scan — tin mới 24h
  python scan.py "variance"             # on-demand — tìm story về variance
  python scan.py "Ronaldo comeback"     # on-demand — tìm story cụ thể
  python scan.py "tilt control"         # on-demand — poker concept
"""

import anthropic
import os
import sys
from pathlib import Path
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

from dotenv import load_dotenv
load_dotenv()

AGENT_FILE = Path(__file__).parent / "agents" / "story-scanner.md"
MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 4000


def load_agent_prompt() -> str:
    if not AGENT_FILE.exists():
        raise FileNotFoundError(f"Agent file không tìm thấy: {AGENT_FILE}")
    return AGENT_FILE.read_text(encoding="utf-8")


def build_user_message(query: str) -> str:
    if not query or query.lower() in ["daily", "today", "hôm nay"]:
        return "/scan daily"
    return f"/scan {query}"


def run_scanner(query: str = "") -> str:
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    system_prompt = load_agent_prompt()
    user_message = build_user_message(query)

    mode = "DAILY SCAN" if not query or query.lower() in ["daily", "today"] else f"ON-DEMAND: {query}"

    print(f"\n{'='*60}")
    print(f"SPADES STORY SCANNER")
    print(f"Mode: {mode}")
    print(f"{'='*60}\n")

    with client.messages.stream(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=system_prompt,
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
        messages=[{"role": "user", "content": user_message}]
    ) as stream:
        full_response = ""
        for event in stream:
            # Stream text chunks
            if hasattr(event, "type"):
                if event.type == "content_block_start":
                    if hasattr(event, "content_block"):
                        if event.content_block.type == "text":
                            pass
                        elif event.content_block.type == "tool_use":
                            tool_name = getattr(event.content_block, "name", "")
                            if tool_name == "web_search":
                                print(f"\n🔍 Đang search...", end="", flush=True)
                elif event.type == "content_block_delta":
                    if hasattr(event, "delta"):
                        if hasattr(event.delta, "text"):
                            text = event.delta.text
                            print(text, end="", flush=True)
                            full_response += text
                        elif hasattr(event.delta, "partial_json"):
                            # Tool input being built — show minimal indicator
                            pass
                elif event.type == "content_block_stop":
                    pass

    print(f"\n\n{'='*60}\n")
    return full_response


def save_output(content: str, query: str) -> Path:
    output_dir = Path("outputs") / "stories"
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    if not query or query.lower() in ["daily", "today"]:
        filename = output_dir / f"daily_{timestamp}.md"
    else:
        safe_query = "".join(c if c.isalnum() or c in " -_" else "_" for c in query)[:30]
        filename = output_dir / f"{safe_query.strip().replace(' ', '_')}_{timestamp}.md"

    header = f"# Story Scan — {query or 'Daily'}\n_{datetime.now().strftime('%d/%m/%Y %H:%M')}_\n\n---\n\n"
    filename.write_text(header + content, encoding="utf-8")
    return filename


if __name__ == "__main__":
    query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else ""

    result = run_scanner(query)

    save = input("\nLưu output ra file? (y/n): ").strip().lower()
    if save == "y":
        saved_path = save_output(result, query)
        print(f"✓ Đã lưu: {saved_path}")
