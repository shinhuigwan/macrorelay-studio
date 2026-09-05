from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from macro_studio.app import create_app


def main() -> int:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "macro-studio-dashboard.png"
    app, window = create_app(ROOT)
    window.resize(1540, 940)
    window.show()
    window.switch_page("dashboard")
    app.processEvents()
    target.parent.mkdir(parents=True, exist_ok=True)
    if not window.grab().save(str(target)):
        raise RuntimeError(f"screenshot save failed: {target}")
    window.close()
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

