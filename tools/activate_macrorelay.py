from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from macro_studio.repository import MacroRepository


def main() -> int:
    parser = argparse.ArgumentParser(description="Activate MacroRelay Runner after deployment.")
    parser.add_argument("root", type=Path)
    parser.add_argument("--no-startup", action="store_true")
    args = parser.parse_args()
    repository = MacroRepository(args.root)
    payload = repository.load_hotkeys()
    settings = dict(payload.get("runner") or {})
    settings.update(
        {
            "enabled": True,
            "start_with_windows": not args.no_startup,
            "emergency_hotkey": settings.get("emergency_hotkey") or "Ctrl+Alt+Pause",
        }
    )
    payload["runner"] = settings
    repository.save_hotkeys(payload)
    runner = repository.quick_slots_runner()
    runner.restart(payload)
    runner.set_startup(not args.no_startup)
    print(f"MacroRelay Runner activated: {runner.script_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
