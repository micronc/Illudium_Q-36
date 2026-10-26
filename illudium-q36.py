"""
Illudium-Q36 Intelligence Briefing Generator
---------------------------------------
Paste raw threat intel (CVE, IOC dump, threat report) and/or load multiple
intel files, then receive a structured executive briefing powered by Claude.

Output: Markdown file saved to ./briefings/

For the analyst console (multi-file UI + text/voice chat):
    python app.py

Requirements:
    pip install -r requirements.txt

Setup: # Only if you are doing this in a lab or dev. NOT PROD! (Hopefully)
    Create a .env file with:
        ANTHROPIC_API_KEY=your_key_here

Usage:
    python illudium-q36.py
    python illudium-q36.py --files report.md iocs.csv notes.txt
    python illudium-q36.py --chat
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import core


def read_stdin_intel() -> str:
    print("Paste your raw threat intelligence below.")
    print("When finished, enter END on its own line.\n")

    lines: list[str] = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line.strip() == "END":
            break
        lines.append(line)
    return "\n".join(lines).strip()


def run_briefing(filenames: list[str], include_paste: bool) -> None:
    pasted = ""
    if include_paste or not filenames:
        pasted = read_stdin_intel()

    corpus = core.load_intel_corpus(
        filenames=filenames or None,
        extra_text=pasted or None,
    )

    if not corpus.strip():
        print("No input provided. Exiting.")
        return

    print("\nAnalyzing threat intelligence...")
    briefing = core.generate_briefing(corpus)
    output_file = core.save_briefing(briefing)

    print(f"\nBriefing saved to: {output_file}")
    print("\n" + "=" * 60)
    print(briefing)
    print("=" * 60)


def run_cli_chat(filenames: list[str]) -> None:
    """Simple terminal chat against the loaded intel corpus."""
    corpus = core.load_intel_corpus(filenames=filenames or None)
    files = filenames or [f["name"] for f in core.list_intel_files()]

    print("=" * 60)
    print("  Illudium-Q36 Analyst Chat (text)")
    print("  Type your question. Commands: /briefing  /files  /quit")
    print("=" * 60)
    if files:
        print(f"Loaded intel: {', '.join(files)}")
    else:
        print("No intel files in ./intel — answers will note the empty corpus.")
    print()

    history: list[dict] = []
    while True:
        try:
            user = input("analyst> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            break

        if not user:
            continue
        if user in {"/quit", "/exit", "quit", "exit"}:
            break
        if user == "/files":
            listed = core.list_intel_files()
            if not listed:
                print("(no files)")
            else:
                for f in listed:
                    print(f"  - {f['name']} ({f['size']} bytes)")
            continue
        if user == "/briefing":
            if not corpus.strip():
                print("No intel loaded.")
                continue
            print("Generating briefing...")
            briefing = core.generate_briefing(corpus)
            path = core.save_briefing(briefing)
            print(briefing)
            print(f"\nSaved: {path}\n")
            history.append(
                {
                    "role": "user",
                    "content": "Generate an executive briefing from the loaded intelligence.",
                }
            )
            history.append({"role": "assistant", "content": briefing})
            continue

        print("Thinking...")
        try:
            reply, history, _usage = core.chat_with_intel(user, history, corpus)
        except Exception as exc:  # noqa: BLE001
            print(f"Error: {exc}")
            continue
        print(f"\n{reply}\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Illudium-Q36 — executive briefings and analyst chat from threat intel."
    )
    parser.add_argument(
        "--files",
        "-f",
        nargs="+",
        default=[],
        help="One or more intel files under ./intel (or paths whose basenames are used).",
    )
    parser.add_argument(
        "--paste",
        action="store_true",
        help="Also read pasted intel from stdin (END to finish), even when --files is set.",
    )
    parser.add_argument(
        "--chat",
        action="store_true",
        help="Interactive text chat about the loaded intel instead of a one-shot briefing.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List intel files in ./intel and exit.",
    )
    args = parser.parse_args()

    core.ensure_dirs()

    # Normalize to basenames stored in intel/
    filenames: list[str] = []
    for raw in args.files:
        src = Path(raw)
        if src.is_file() and src.parent.resolve() != core.INTEL_DIR.resolve():
            # Copy external file into intel corpus
            data = src.read_bytes()
            saved = core.save_intel_bytes(src.name, data)
            filenames.append(saved)
            print(f"Imported {src} → intel/{saved}")
        else:
            filenames.append(src.name)

    if args.list:
        files = core.list_intel_files()
        if not files:
            print("No intel files in ./intel")
        else:
            for f in files:
                print(f"{f['name']}\t{f['size']}\t{f['modified']}")
        return

    print("=" * 60)
    print("  Threat Intelligence Briefing Generator")
    print("  Powered by Anthropic Claude")
    print("=" * 60)
    print()

    if args.chat:
        run_cli_chat(filenames)
        return

    run_briefing(filenames, include_paste=args.paste or not filenames)


if __name__ == "__main__":
    try:
        main()
    except ValueError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        sys.exit(1)
