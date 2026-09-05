import sys

with open("C:/Users/shin/Documents/Codex/2026-08-20/sp-x20/work/macro_tool_rebuild/macro_tool.py", "r", encoding="utf-8") as f:
    for i, line in enumerate(f):
        if "def render_step" in line or "action == \"ocr\":" in line or "action == 'ocr':" in line:
            print(f"{i+1}: {line.strip()}")
