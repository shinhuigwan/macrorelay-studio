"""Build the repository-shipped Deck Dock backup from the current local deck."""

from __future__ import annotations

import base64
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / ".quickslot_deck_config.json"
HOTKEYS_PATH = ROOT / "hotkeys.json"
OUTPUT_PATH = ROOT / "deck_presets" / "macrorelay_bundled_deck.json"


def main() -> None:
    deck_config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    hotkeys = json.loads(HOTKEYS_PATH.read_text(encoding="utf-8"))
    config = dict(deck_config.get("config") or {})
    presets = dict(config.get("slot_presets") or {})
    active_id = str(config.get("active_slot_preset") or "")
    active = dict(presets.get(active_id) or {})

    # Make sure the currently visible assignment is represented even if the
    # app had not yet captured it into the active preset.
    if active:
        active["slots"] = hotkeys.get("slots") or []
        active["deck_page_count"] = hotkeys.get("deck_page_count") or 1
        active["deck_page_names"] = hotkeys.get("deck_page_names") or ["페이지 1"]
        active["custom_icons"] = deck_config.get("custom_icons") or active.get("custom_icons") or {}
        active["rows"] = deck_config.get("rows", active.get("rows", 3))
        active["cols"] = deck_config.get("cols", active.get("cols", 5))
        presets[active_id] = active
        config["slot_presets"] = presets
        deck_config["config"] = config

    # Active-preset icons are identical to the root icon set; keep one copy.
    deck_config["custom_icons"] = {}
    macro_names = set()
    for slot_list in [hotkeys.get("slots") or []] + [
        preset.get("slots") or [] for preset in presets.values() if isinstance(preset, dict)
    ]:
        for slot in slot_list:
            if not isinstance(slot, dict):
                continue
            action = slot.get("action") or {}
            if isinstance(action, dict):
                if action.get("kind") == "run_macro" and action.get("macro"):
                    macro_names.add(str(action["macro"]))
                elif action.get("kind") == "multi_macros":
                    macro_names.update(str(name) for name in action.get("macros") or [] if name)
            if slot.get("macro") and slot.get("mode") != "deck_action":
                macro_names.add(str(slot["macro"]))
    asset_index_path = ROOT / "assets" / "index.json"
    asset_index = json.loads(asset_index_path.read_text(encoding="utf-8")) if asset_index_path.is_file() else {}
    macros = {}
    assets = {}
    for name in sorted(macro_names):
        macro_path = ROOT / "macros" / f"{name}.json"
        if not macro_path.is_file():
            continue
        macro = json.loads(macro_path.read_text(encoding="utf-8"))
        macros[name] = macro
        for step in macro.get("steps") or []:
            if not isinstance(step, dict):
                continue
            aliases = [step.get("asset")]
            aliases.extend(step.get("assets") or [] if isinstance(step.get("assets"), list) else [])
            for alias in aliases:
                if not isinstance(alias, str) or not alias or alias in assets:
                    continue
                metadata = asset_index.get(alias) or {}
                relative = str(metadata.get("file") or "").replace("\\", "/")
                path = ROOT / relative
                if path.is_file() and path.resolve().is_relative_to((ROOT / "assets").resolve()):
                    assets[alias] = {"file": path.name, "image_data": base64.b64encode(path.read_bytes()).decode("ascii")}
    payload = {
        "format": "macrorelay-deck-backup",
        "version": 2,
        "description": "MacroRelay built-in Deck Dock configuration",
        "deck_config": deck_config,
        "hotkeys": hotkeys,
        "macros": macros,
        "assets": assets,
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH} ({OUTPUT_PATH.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
