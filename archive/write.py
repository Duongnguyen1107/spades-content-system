#!/usr/bin/env python3
"""
Spades Content Writer
Viết bài lamwork từ story thô hoặc chạy full pipeline: scan → write

Cách chạy:
  # Viết bài từ story file của scanner
  python write.py outputs/stories/variance_20250426.md

  # Viết bài từ topic trực tiếp (không qua scanner)
  python write.py --topic "variance" --angle "tâm lý" --goal "giúp newbie hiểu variance"

  # Full pipeline: scan story → viết bài luôn
  python write.py --pipeline "tilt control"
"""

import anthropic
import os
import sys
import time
import argparse
from pathlib import Path
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

from dotenv import load_dotenv
load_dotenv()

WRITER_AGENT = Path(__file__).parent / "agents" / "content-writer.md"
SCANNER_AGENT = Path(__file__).parent / "agents" / "story-scanner.md"
MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 3000

PRICE_INPUT  = 3.00
PRICE_OUTPUT = 15.00

_session_usage: list[dict] = []


def _calc_cost(input_tokens: int, output_tokens: int) -> float:
    return (input_tokens * PRICE_INPUT + output_tokens * PRICE_OUTPUT) / 1_000_000


def _print_usage(label: str, input_tokens: int, output_tokens: int) -> None:
    cost = _calc_cost(input_tokens, output_tokens)
    _session_usage.append({"label": label, "input": input_tokens, "output": output_tokens, "cost": cost})
    print(f"  📊 {label}: {input_tokens:,} in + {output_tokens:,} out = ${cost:.4f}")


def print_cost_summary() -> None:
    if not _session_usage:
        return
    print(f"\n{'='*60}")
    print("  COST BREAKDOWN")
    print(f"{'='*60}")
    total_in = total_out = total_cost = 0
    for u in _session_usage:
        print(f"  {u['label']:<25} {u['input']:>7,} in  {u['output']:>6,} out  ${u['cost']:.4f}")
        total_in   += u["input"]
        total_out  += u["output"]
        total_cost += u["cost"]
    print(f"  {'─'*53}")
    print(f"  {'TOTAL':<25} {total_in:>7,} in  {total_out:>6,} out  ${total_cost:.4f}")
    print(f"{'='*60}\n")


def load_agent(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Agent file không tìm thấy: {path}")
    return path.read_text(encoding="utf-8")


def _stream_call(client, kwargs: dict, label: str = "Agent",
                 show_search: bool = False,
                 max_retries: int = 5, retry_wait: int = 60) -> str:
    for attempt in range(1, max_retries + 1):
        try:
            full_response = ""
            with client.messages.stream(**kwargs) as stream:
                for event in stream:
                    if hasattr(event, "type"):
                        if show_search and event.type == "content_block_start":
                            if hasattr(event, "content_block"):
                                if getattr(event.content_block, "type", "") == "tool_use":
                                    print(f"\n🔍 Đang search...", end="", flush=True)
                        elif event.type == "content_block_delta":
                            if hasattr(event, "delta") and hasattr(event.delta, "text"):
                                text = event.delta.text
                                print(text, end="", flush=True)
                                full_response += text
                usage = stream.get_final_message().usage
            print(f"\n\n{'='*60}\n")
            _print_usage(label, usage.input_tokens, usage.output_tokens)
            return full_response
        except anthropic.RateLimitError:
            if attempt < max_retries:
                print(f"\n⏳ Rate limit — đợi {retry_wait}s rồi thử lại (lần {attempt}/{max_retries})...", flush=True)
                time.sleep(retry_wait)
            else:
                raise


def run_writer(input_content: str, source_label: str = "") -> str:
    """Chạy content-writer agent với input là story thô hoặc topic."""
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    system_prompt = load_agent(WRITER_AGENT)

    print(f"\n{'='*60}")
    print(f"SPADES CONTENT WRITER")
    if source_label:
        print(f"Source: {source_label}")
    print(f"{'='*60}\n")

    return _stream_call(client, dict(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=system_prompt,
        messages=[{"role": "user", "content": input_content}]
    ), label="Content Writer")


def run_scanner_for_pipeline(query: str) -> str:
    """Chạy story-scanner, trả về output để feed vào writer."""
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    system_prompt = load_agent(SCANNER_AGENT)

    print(f"\n{'='*60}")
    print(f"PIPELINE STEP 1: STORY SCANNER")
    print(f"Query: {query}")
    print(f"{'='*60}\n")

    return _stream_call(client, dict(
        model=MODEL,
        max_tokens=4000,
        system=system_prompt,
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
        messages=[{"role": "user", "content": f"/scan {query}"}]
    ), label="Story Scanner", show_search=True)


def save_output(content: str, label: str, output_type: str = "post") -> Path:
    output_dir = Path("outputs") / output_type
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    safe_label = "".join(c if c.isalnum() or c in " -_" else "_" for c in label)[:35]
    filename = output_dir / f"{safe_label.strip().replace(' ', '_')}_{timestamp}.md"

    header = f"# {label}\n_{datetime.now().strftime('%d/%m/%Y %H:%M')}_\n\n---\n\n"
    filename.write_text(header + content, encoding="utf-8")
    return filename


def build_direct_input(topic: str, angle: str = "", goal: str = "") -> str:
    """Build input message khi viết bài trực tiếp không qua scanner."""
    parts = [f"Topic: {topic}"]
    if angle:
        parts.append(f"Góc độ: {angle}")
    if goal:
        parts.append(f"Mục tiêu: {goal}")
    parts.append("Không có story thô từ scanner — tự chọn story phù hợp nhất dựa trên topic và viết bài.")
    return "\n".join(parts)


def main():
    parser = argparse.ArgumentParser(description="Spades Content Writer")
    parser.add_argument("story_file", nargs="?", help="File story thô từ scanner (.md)")
    parser.add_argument("--topic", help="Topic trực tiếp (không qua scanner)")
    parser.add_argument("--angle", default="", help="Góc độ bài viết")
    parser.add_argument("--goal", default="", help="Mục tiêu bài viết")
    parser.add_argument("--pipeline", help="Full pipeline: scan topic này rồi viết bài luôn")

    args = parser.parse_args()

    # MODE 1: Full pipeline — scan rồi write
    if args.pipeline:
        print(f"\n🔄 FULL PIPELINE MODE: {args.pipeline}")
        scanner_output = run_scanner_for_pipeline(args.pipeline)

        print("\n" + "="*60)
        print("PIPELINE STEP 2: CONTENT WRITER")
        print("="*60)

        # Chọn story từ scanner output
        writer_input = f"""Dưới đây là kết quả từ story-scanner. Chọn story tốt nhất (STRONG bridge quality) và viết bài hoàn chỉnh.

=== SCANNER OUTPUT ===
{scanner_output}
=== END SCANNER OUTPUT ===

Yêu cầu:
- Chọn story có BRIDGE QUALITY: STRONG
- Nếu không có STRONG thì chọn MODERATE
- Viết bài hoàn chỉnh theo lamwork style
- Xuất đủ PHẦN 1 (bài viết) và PHẦN 2 (checklist verify)"""

        result = run_writer(writer_input, source_label=f"Pipeline: {args.pipeline}")
        label = args.pipeline

    # MODE 2: Viết từ story file
    elif args.story_file:
        story_path = Path(args.story_file)
        if not story_path.exists():
            print(f"❌ File không tìm thấy: {args.story_file}")
            sys.exit(1)

        story_content = story_path.read_text(encoding="utf-8")
        writer_input = f"""Dưới đây là story thô từ story-scanner. Viết bài hoàn chỉnh từ story này.

=== STORY THÔ ===
{story_content}
=== END STORY ===

Viết bài theo lamwork style. Xuất đủ PHẦN 1 và PHẦN 2."""

        result = run_writer(writer_input, source_label=story_path.name)
        label = story_path.stem

    # MODE 3: Topic trực tiếp
    elif args.topic:
        writer_input = build_direct_input(args.topic, args.angle, args.goal)
        result = run_writer(writer_input, source_label=f"Topic: {args.topic}")
        label = args.topic

    # Không có input
    else:
        print("\n📝 SPADES CONTENT WRITER")
        print("="*40)
        print("\nChọn mode:")
        print("1. Viết từ story file (scanner output)")
        print("2. Viết từ topic trực tiếp")
        print("3. Full pipeline (scan + write)")

        choice = input("\nChọn (1/2/3): ").strip()

        if choice == "1":
            file_path = input("Path đến story file: ").strip()
            story_path = Path(file_path)
            if not story_path.exists():
                print(f"❌ File không tìm thấy")
                sys.exit(1)
            story_content = story_path.read_text(encoding="utf-8")
            writer_input = f"=== STORY THÔ ===\n{story_content}\n=== END ===\n\nViết bài theo lamwork style. Xuất đủ PHẦN 1 và PHẦN 2."
            result = run_writer(writer_input, story_path.name)
            label = story_path.stem

        elif choice == "2":
            topic = input("Topic: ").strip()
            angle = input("Góc độ (Enter để bỏ qua): ").strip()
            goal = input("Mục tiêu bài viết (Enter để bỏ qua): ").strip()
            writer_input = build_direct_input(topic, angle, goal)
            result = run_writer(writer_input, f"Topic: {topic}")
            label = topic

        elif choice == "3":
            query = input("Scan topic/concept: ").strip()
            scanner_output = run_scanner_for_pipeline(query)
            writer_input = f"Chọn story STRONG từ scanner output này và viết bài:\n\n{scanner_output}"
            result = run_writer(writer_input, f"Pipeline: {query}")
            label = query

        else:
            print("❌ Lựa chọn không hợp lệ")
            sys.exit(1)

    print_cost_summary()

    # Save
    save = input("\nLưu output ra file? (y/n): ").strip().lower()
    if save == "y":
        saved_path = save_output(result, label, "posts")
        print(f"✓ Đã lưu: {saved_path}")


if __name__ == "__main__":
    main()
