from __future__ import annotations

import ctypes
import sys
import traceback

from PySide6 import QtCore, QtGui, QtWidgets

from . import __version__
from .main_window import MainWindow
from .repository import MacroRepository
from .remote import RemoteController
from .theme import stylesheet


def configure_windows_app_identity() -> None:
    """Give the Python-hosted window its own taskbar identity and icon cache key."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MacroRelay.Studio")
    except Exception:
        pass


def create_app(root=None) -> tuple[QtWidgets.QApplication, MainWindow]:
    configure_windows_app_identity()
    QtCore.QCoreApplication.setApplicationName("MacroRelay Studio")
    QtCore.QCoreApplication.setOrganizationName("MacroRelay")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setApplicationDisplayName(f"MacroRelay Studio {__version__}")
    app.setStyle("Fusion")
    app.setStyleSheet(stylesheet())
    repository = MacroRepository(root)
    remote = RemoteController(repository.root)
    if remote.load().get("enabled"):
        remote.ensure_running()
    remote_watchdog = QtCore.QTimer(app)
    remote_watchdog.setInterval(5000)
    remote_watchdog.timeout.connect(remote.ensure_running)
    remote_watchdog.start()
    # QApplication owns this timer, but retaining explicit Python references
    # also prevents wrapper collection in long-running Studio sessions.
    app._macrorelay_remote = remote  # type: ignore[attr-defined]
    app._macrorelay_remote_watchdog = remote_watchdog  # type: ignore[attr-defined]

    def stop_remote_runtime() -> None:
        remote_watchdog.stop()
        remote.stop_agent()
        if remote.uses_local_relay():
            remote.stop_local_relay()

    app.aboutToQuit.connect(stop_remote_runtime)
    icon_path = repository.root / "branding" / "macrorelay-studio.ico"
    icon = QtGui.QIcon()
    if icon_path.exists():
        icon = QtGui.QIcon(str(icon_path))
        app.setWindowIcon(icon)
    window = MainWindow(repository)
    if not icon.isNull():
        window.setWindowIcon(icon)
    return app, window


def main() -> int:
    app, window = create_app()

    def exception_hook(exc_type, value, tb) -> None:
        details = "".join(traceback.format_exception(exc_type, value, tb))
        QtWidgets.QMessageBox.critical(window, "예기치 않은 오류", f"{value}\n\n{details[-3000:]}")

    sys.excepthook = exception_hook
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
