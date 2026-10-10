"""One Deck per configuration root; repeat launches only reveal the owner."""

import hashlib
import os
from pathlib import Path

from PySide6 import QtCore, QtNetwork


class DeckSingleInstance(QtCore.QObject):
    activate_requested = QtCore.Signal()

    def __init__(self, root: Path, parent=None):
        super().__init__(parent)
        root = root.resolve()
        runtime = root / "runtime"
        runtime.mkdir(parents=True, exist_ok=True)
        identity = hashlib.sha256(os.path.normcase(str(root)).encode("utf-8")).hexdigest()[:32]
        self.name = "macrorelay-deck-" + identity
        self.lock = QtCore.QLockFile(str(runtime / "quickslot-instance.lock"))
        self.lock.setStaleLockTime(0)
        self.server = QtNetwork.QLocalServer(self)
        self.server.setSocketOptions(QtNetwork.QLocalServer.UserAccessOption)
        self.server.newConnection.connect(self._accept)
        self.owner = False
        self.error = ""
        self.window = None
        self.activation_pending = False
        self.activate_requested.connect(self._reveal)

    def acquire_or_notify(self) -> bool:
        self.error = ""
        if self.lock.tryLock(0):
            # Only the lock owner may remove a socket left by a crashed owner.
            QtNetwork.QLocalServer.removeServer(self.name)
            if not self.server.listen(self.name):
                self.error = self.server.errorString()
                self.lock.unlock()
                return False
            self.owner = True
            return True
        if self.lock.error() != QtCore.QLockFile.LockFailedError:
            self.error = "덕덱 실행 잠금 파일을 만들 수 없습니다. 폴더 권한을 확인해 주세요."
            return False
        # The first launch may be between acquiring the lock and listening.
        for _ in range(10):
            socket = QtNetwork.QLocalSocket()
            socket.connectToServer(self.name)
            if socket.waitForConnected(150):
                socket.disconnectFromServer()
                return False
            QtCore.QThread.msleep(100)
        self.error = "기존 덕덱이 아직 시작 중이거나 응답하지 않습니다. 잠시 후 다시 실행해 주세요."
        return False

    def _accept(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            socket.close()
            socket.deleteLater()
            self.activate_requested.emit()

    def bind_window(self, window):
        self.window = window
        if self.activation_pending:
            self._reveal()

    def _reveal(self):
        self.activation_pending = True
        if self.window is None:
            return
        if self.window.isMinimized():
            self.window.showNormal()
        else:
            self.window.show()
        self.window.raise_()
        self.window.activateWindow()
        self.activation_pending = False

    def close(self):
        self.window = None
        self.server.close()
        if self.owner:
            self.lock.unlock()
            self.owner = False
