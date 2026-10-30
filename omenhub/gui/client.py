# SPDX-FileCopyrightText: 2026 OmenSuperHub for Linux contributors
# SPDX-License-Identifier: GPL-3.0-only
"""图形界面与 omenhubd 的连接：订阅线程接收推送，请求线程执行命令，结果通过 Qt 信号回到界面线程。"""

import itertools
import logging
import queue
import threading
import time

from ..ipc import Client, IpcError
from .qt import QtCore, Signal

log = logging.getLogger(__name__)


class DaemonClient(QtCore.QObject):
    connectionChanged = Signal(bool)
    infoChanged = Signal(object)
    stateChanged = Signal(object)
    statusChanged = Signal(object)
    eventReceived = Signal(str, object)
    requestFailed = Signal(str, str)        # code, message（未提供错误回调时）
    _reply = Signal(int, bool, object)
    _incoming = Signal(str, object)

    def __init__(self, socket_path: str = None, parent=None):
        super().__init__(parent)
        self.socket_path = socket_path
        self.connected = False
        self.info, self.state, self.status = {}, {}, {}
        self._callbacks = {}
        self._tokens = itertools.count(1)
        self._requests = queue.Queue()
        self._stop = threading.Event()
        self._reply.connect(self._on_reply)
        self._incoming.connect(self._on_incoming)
        threading.Thread(target=self._event_thread, name="omenhub-events", daemon=True).start()
        threading.Thread(target=self._request_thread, name="omenhub-requests", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    # ---------------------------------------------------------------- 请求
    def call(self, cmd: str, args: dict = None, on_done=None, on_error=None) -> None:
        token = next(self._tokens)
        self._callbacks[token] = (on_done, on_error)
        self._requests.put((token, cmd, args or {}))

    def set(self, key: str, value, on_done=None, on_error=None) -> None:
        self.call("set", {"key": key, "value": value}, on_done, on_error)

    def _request_thread(self) -> None:
        client = None
        while not self._stop.is_set():
            token, cmd, args = self._requests.get()
            for attempt in (1, 2):
                try:
                    if client is None:
                        client = Client(self.socket_path, timeout=60).connect()
                    result = client.request(cmd, **args)
                    self._reply.emit(token, True, result)
                    break
                except IpcError as exc:
                    if exc.code in ("offline", "timeout"):
                        if client:
                            client.close()
                        client = None
                        if attempt == 1:
                            continue
                    self._reply.emit(token, False, (exc.code, str(exc)))
                    break

    def _on_reply(self, token: int, ok: bool, payload) -> None:
        on_done, on_error = self._callbacks.pop(token, (None, None))
        if ok:
            if isinstance(payload, dict) and "current_preset" in payload and "fields" in payload:
                self._apply_state(payload)
            if on_done:
                on_done(payload)
        else:
            code, message = payload
            if on_error:
                on_error(code, message)
            else:
                self.requestFailed.emit(code, message)

    # ---------------------------------------------------------------- 推送
    def _event_thread(self) -> None:
        delay = 1.0
        while not self._stop.is_set():
            client = None
            try:
                client = Client(self.socket_path, timeout=30).connect()
                snapshot = client.subscribe()
                self._incoming.emit("snapshot", snapshot)
                delay = 1.0
                while not self._stop.is_set():
                    message = client.read_message(timeout=30)
                    if "event" in message:
                        self._incoming.emit(message["event"], message.get("data"))
            except IpcError as exc:
                log.debug("daemon connection: %s", exc)
            finally:
                if client:
                    client.close()
            self._incoming.emit("disconnected", None)
            time.sleep(delay)
            delay = min(delay * 2, 5.0)

    def _apply_state(self, state: dict) -> None:
        self.state = state
        self.stateChanged.emit(state)

    def _on_incoming(self, event: str, data) -> None:
        if event == "snapshot":
            # 先保存全部数据再发信号，菜单重建时才能拿到完整的 state
            self.info, self.state, self.status = data["info"], data["state"], data["status"]
            self.connected = True
            self.connectionChanged.emit(True)
            self.infoChanged.emit(self.info)
            self.stateChanged.emit(self.state)
            self.statusChanged.emit(self.status)
        elif event == "disconnected":
            if self.connected:
                self.connected = False
                self.connectionChanged.emit(False)
        elif event == "status":
            self.status = data
            self.statusChanged.emit(data)
        elif event == "state":
            self._apply_state(data)
        else:
            self.eventReceived.emit(event, data)
