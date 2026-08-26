from pathlib import Path
import sys


RUNTIME_PACKAGES = Path(__file__).resolve().parent / "runtime_packages"
ABI_PACKAGES = RUNTIME_PACKAGES / f"cp{sys.version_info.major}{sys.version_info.minor}"
for package_root in (ABI_PACKAGES, RUNTIME_PACKAGES):
    if package_root.exists() and str(package_root) not in sys.path:
        sys.path.insert(0, str(package_root))

from macro_studio.app import main


if __name__ == "__main__":
    raise SystemExit(main())
