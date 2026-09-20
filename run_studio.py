from pathlib import Path
import sys


BASE_DIR = Path(__file__).resolve().parent
RUNTIME_PACKAGES = BASE_DIR / "runtime_packages"
VENV_PACKAGES = BASE_DIR / ".venv" / "Lib" / "site-packages"
OPENCV_PACKAGES = BASE_DIR / "runtime" / "opencv" / f"cp{sys.version_info.major}{sys.version_info.minor}" / "packages"

for package_root in (OPENCV_PACKAGES, RUNTIME_PACKAGES, VENV_PACKAGES):
    if package_root.exists() and str(package_root) not in sys.path:
        sys.path.insert(0, str(package_root))

from macro_studio.app import main


if __name__ == "__main__":
    raise SystemExit(main())
