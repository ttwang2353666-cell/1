"""守护进程与客户端之间的通信协议：Unix 套接字上的逐行 JSON。

请求：{"id": 1, "cmd": "set", "args": {...}}
响应：{"id": 1, "ok": true, "result": ...} / {"id": 1, "ok": false, "error": "...", "code": "..."}
事件：{"event": "status", "data": {...}}（订阅后由守护进程推送）
"""

import grp
import json
import os
import pwd
import socket
import struct

from . import paths

# 错误码
E_DENIED = "denied"
E_INVALID = "invalid"
E_UNSUPPORTED = "unsupported"
E_FAILED = "failed"
E_UNKNOWN = "unknown_command"

# 不需要授权即可调用的只读命令
READ_ONLY_COMMANDS = frozenset({
    "ping", "get_info", "get_state", "get_status", "subscribe", "fan_curve_get", "bios_sensors",
    "gpu_processes", "lighting_get", "probe", "gpu_refresh",
})


class IpcError(Exception):
    def __init__(self, message: str, code: str = E_FAILED):
        super().__init__(message)
        self.code = code


def encode(message: dict) -> bytes:
    return (json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def peer_credentials(sock: socket.socket):
    """SO_PEERCRED -> (pid, uid, gid)。"""
    creds = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    return struct.unpack("3i", creds)


def is_authorized(uid: int, allowed_groups) -> bool:
    """root 或属于允许组（默认 omenhub / wheel / sudo / admin）的用户可以修改设置。

    使用组数据库而非进程的附加组，加入组后无需重新登录即可生效。
    """
    if uid == 0:
        return True
    try:
        user = pwd.getpwuid(uid)
    except KeyError:
        return False
    try:
        groups = {grp.getgrgid(gid).gr_name for gid in os.getgrouplist(user.pw_name, user.pw_gid)}
    except (KeyError, OSError):
        groups = set()
    return any(group in groups for group in allowed_groups)


class Client:
    """同步客户端（命令行与图形界面的工作线程使用）。"""

    def __init__(self, path: str = None, timeout: float = 15.0):
        self.path = path or paths.SOCKET_PATH
        self.timeout = timeout
        self.sock = None
        self._buffer = b""
        self._next_id = 1

    def connect(self) -> "Client":
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(self.path)
        except OSError as exc:
            sock.close()
            raise IpcError(f"cannot connect to {self.path}: {exc}", "offline") from exc
        self.sock = sock
        return self

    def close(self) -> None:
        if self.sock:
            try:
                self.sock.close()
            finally:
                self.sock = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, *exc):
        self.close()

    def send(self, message: dict) -> None:
        if not self.sock:
            raise IpcError("not connected", "offline")
        try:
            self.sock.sendall(encode(message))
        except OSError as exc:
            self.close()
            raise IpcError(f"connection lost: {exc}", "offline") from exc

    def read_message(self, timeout=None) -> dict:
        if not self.sock:
            raise IpcError("not connected", "offline")
        self.sock.settimeout(timeout if timeout is not None else self.timeout)
        while b"\n" not in self._buffer:
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout as exc:
                raise IpcError("timed out", "timeout") from exc
            except OSError as exc:
                self.close()
                raise IpcError(f"connection lost: {exc}", "offline") from exc
            if not chunk:
                self.close()
                raise IpcError("connection closed by daemon", "offline")
            self._buffer += chunk
        line, _, self._buffer = self._buffer.partition(b"\n")
        return json.loads(line.decode("utf-8"))

    def request(self, cmd: str, **args):
        msg_id = self._next_id
        self._next_id += 1
        self.send({"id": msg_id, "cmd": cmd, "args": args})
        while True:
            reply = self.read_message()
            if reply.get("id") == msg_id:
                if reply.get("ok"):
                    return reply.get("result")
                raise IpcError(reply.get("error") or "request failed", reply.get("code") or E_FAILED)
            # 订阅连接上的事件，忽略

    def subscribe(self):
        return self.request("subscribe")
