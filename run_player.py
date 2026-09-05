from pathlib import Path
import sys

# Ensure runtime packages are available
RUNTIME_PACKAGES = Path(__file__).resolve().parent / "runtime_packages"
ABI_PACKAGES = RUNTIME_PACKAGES / f"cp{sys.version_info.major}{sys.version_info.minor}"
for package_root in (ABI_PACKAGES, RUNTIME_PACKAGES):
    if package_root.exists() and str(package_root) not in sys.path:
        sys.path.insert(0, str(package_root))

from macro_studio.player import launch_player

if __name__ == "__main__":
    macro_name = sys.argv[1] if len(sys.argv) > 1 else ""
    raise SystemExit(launch_player(macro_name))
