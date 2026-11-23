#!/usr/bin/env python3
# 开发者：hyy
"""REQ-029 · TCP Hello 服务端轻量验收测试脚本（S1–S7 对应 AC-1～AC-7）。

仅依赖 Python 3 标准库：socket / subprocess / ctypes / time。
服务端可执行文件通过命令行参数 --server 或环境变量 TCP_HELLO_SERVER 指定；
未指定时按构建产物路径查找，必要时执行与 S5 相同的 CMake 配置与构建步骤。

用法：
    python tcp_hello_demo/tests/test_server.py
    python tcp_hello_demo/tests/test_server.py --server tcp_hello_demo/build/Release/tcp_hello_server.exe
    python tcp_hello_demo/tests/test_server.py --only S1,S2,S6,S7 -v

场景与验收标准：
    S1 -> AC-1 连接与日志
    S2 -> AC-2 周期发送
    S3 -> AC-3 双客户端并发
    S4 -> AC-4 断开隔离
    S5 -> AC-5 构建与运行（含非法端口参数校验，与 ctest 负面用例同一判据）
    S6 -> AC-6 优雅退出
    S7 -> AC-7 端口占用

总则（技术方案 §3）：
    - 所有服务端进程以 CREATE_NEW_PROCESS_GROUP 启动，共享测试进程控制台，
      stdout/stderr 均重定向到管道并由后台线程持续读取；
    - 场景收尾统一调用 GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT, pid)，
      等待 ≤5 秒并断言退出码 0；超时判该场景失败并强制结束进程兜底，不静默吞掉；
    - 计时使用 time.monotonic()，容差 ±200ms；接收时间戳取 recv 返回时刻；
      S2 的 5 秒窗口自连接建立起算；
    - 测试客户端一律连接回环 127.0.0.1；
    - S6/S7 涉及端口状态，串行执行并在场景间确认端口已释放。
"""

from __future__ import annotations

import argparse
import ctypes
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

# ---------------------------------------------------------------------------
# 常量：与被测服务端的可观测契约保持一致
# ---------------------------------------------------------------------------

HELLO = b"Hello"                  # 固定负载，恰为 5 字节
DEFAULT_PORT = 9000               # 服务端默认端口
LOOPBACK = "127.0.0.1"            # 测试客户端统一连接回环地址
SERVER_ENV = "TCP_HELLO_SERVER"   # 服务端可执行文件路径的环境变量

SEND_INTERVAL_S = 1.0             # 每秒发送一次
TIME_TOLERANCE_S = 0.2            # 计时断言统一容差 ±200ms
FIRST_HELLO_MAX_S = SEND_INTERVAL_S + TIME_TOLERANCE_S          # 1.2s
INTERVAL_MIN_S = SEND_INTERVAL_S - TIME_TOLERANCE_S             # 0.8s
INTERVAL_MAX_S = SEND_INTERVAL_S + TIME_TOLERANCE_S             # 1.2s
SHUTDOWN_TIMEOUT_S = 5.0          # CTRL_BREAK 后等待退出的上限
LISTEN_TIMEOUT_S = 15.0           # 等待 LISTENING 日志的上限
EXIT_TIMEOUT_S = 10.0             # S7 等待服务端自行退出的上限

CREATE_NEW_PROCESS_GROUP = 0x00000200  # 子进程组 ID 等于其 PID
CTRL_BREAK_EVENT = 1

# 非法端口参数契约（与 CMakeLists.txt 的 ctest 负面用例、README §6.3 保持同一判据）
INVALID_PORT_ARGUMENTS = ("0", "65536", "not-a-port")
INVALID_PORT_EXIT_CODE = 2                              # 本实现约定，见 README §8
INVALID_PORT_ERROR = "ERROR invalid port argument"      # README §7 日志契约
CLI_TIMEOUT_S = 30.0              # 单个命令行用例的退出等待上限

# ---------------------------------------------------------------------------
# Windows 控制台控制事件
# ---------------------------------------------------------------------------

_KERNEL32 = None
if sys.platform == "win32":
    _KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _KERNEL32.GenerateConsoleCtrlEvent.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
    _KERNEL32.GenerateConsoleCtrlEvent.restype = ctypes.c_int


class ScenarioFailure(Exception):
    """单个验收场景的断言失败。"""


class TeardownResult:
    """服务端收尾结果。"""

    def __init__(self, passed: bool, message: str):
        self.passed = passed
        self.message = message


def send_ctrl_break(pid: int):
    """向指定进程组发送 CTRL_BREAK_EVENT；成功返回 None，失败返回原因。

    只有以 CREATE_NEW_PROCESS_GROUP 创建的子进程，其进程组 ID 才等于 PID，
    因此这里传入的 pid 同样就是进程组标识。
    """
    if os.name != "nt":
        return "当前平台非 Windows，无法调用 GenerateConsoleCtrlEvent"
    if _KERNEL32 is None:
        return "无法加载 kernel32.GenerateConsoleCtrlEvent"
    if not _KERNEL32.GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT, pid):
        err = ctypes.get_last_error()
        return f"GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT, {pid}) 调用失败，GetLastError={err}"
    return None


# ---------------------------------------------------------------------------
# 服务端进程封装
# ---------------------------------------------------------------------------

class ServerProcess:
    """被测服务端进程：重定向 stdout/stderr，提供日志等待与干净收尾能力。"""

    def __init__(self, proc: subprocess.Popen):
        self.proc = proc
        self._stdout = []
        self._stderr = []
        self._cv = threading.Condition()
        self._streams_closed = False
        self._threads = [
            threading.Thread(target=self._reader, args=(proc.stdout, self._stdout), daemon=True),
            threading.Thread(target=self._reader, args=(proc.stderr, self._stderr), daemon=True),
        ]
        for thread in self._threads:
            thread.start()

    @classmethod
    def start(cls, server_path: Path, port_arg, cwd: Path) -> "ServerProcess":
        cmd = [str(server_path)]
        if port_arg is not None:
            cmd.append(str(port_arg))
        creationflags = CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(cwd) if cwd is not None else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
            )
        except OSError as exc:
            raise ScenarioFailure(f"无法启动服务端 {server_path}: {exc}") from exc
        return cls(proc)

    def _reader(self, stream, sink) -> None:
        """逐行读取管道，避免管道缓冲区写满导致服务端阻塞。"""
        try:
            for line in stream:
                with self._cv:
                    sink.append(line.rstrip("\r\n"))
                    self._cv.notify_all()
        finally:
            with self._cv:
                self._cv.notify_all()

    def wait_for_line(self, pattern: str, timeout: float, streams=("stdout",)):
        """在截止时间前等待匹配日志行；返回匹配行，超时返回 None。"""
        regex = re.compile(pattern)
        deadline = time.monotonic() + timeout
        with self._cv:
            while True:
                for name in streams:
                    sink = self._stdout if name == "stdout" else self._stderr
                    for line in sink:
                        if regex.search(line):
                            return line
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._cv.wait(min(remaining, 0.2))

    def output_lines(self):
        with self._cv:
            return list(self._stdout) + list(self._stderr)

    def is_running(self) -> bool:
        return self.proc.poll() is None

    @property
    def returncode(self):
        return self.proc.returncode

    def wait_exit(self, timeout: float):
        """等待进程退出并返回退出码；超时返回 None（进程保持运行）。"""
        try:
            return self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None

    def terminate_clean(self, timeout: float = SHUTDOWN_TIMEOUT_S) -> TeardownResult:
        """发送 CTRL_BREAK_EVENT，等待 ≤timeout 秒，断言退出码 0；超时强制结束。"""
        if not self.is_running():
            return TeardownResult(False, f"服务端在发送 CTRL_BREAK 前已退出，退出码 {self.returncode}")
        error = send_ctrl_break(self.proc.pid)
        if error is not None:
            self.force_kill()
            return TeardownResult(False, error + "；已强制结束进程兜底")
        code = self.wait_exit(timeout)
        if code is None:
            self.force_kill()
            return TeardownResult(False, f"CTRL_BREAK 后 {timeout:.0f} 秒内未退出，已强制结束进程")
        if code != 0:
            return TeardownResult(False, f"CTRL_BREAK 后退出码为 {code}，期望 0")
        return TeardownResult(True, f"CTRL_BREAK 后 {timeout:.0f} 秒内退出，退出码 0")

    def force_kill(self) -> None:
        if self.is_running():
            try:
                self.proc.kill()
            except OSError:
                pass
        try:
            self.proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            pass

    def close_streams(self) -> None:
        if self._streams_closed:
            return
        self._streams_closed = True
        for thread in self._threads:
            thread.join(timeout=1.0)
        for stream in (self.proc.stdout, self.proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# 端口与客户端辅助
# ---------------------------------------------------------------------------

def port_is_free(port: int, host: str = "0.0.0.0") -> bool:
    """以不设置 SO_REUSEADDR 的新 socket 尝试 bind，判断端口是否空闲。"""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        probe.close()


def wait_port_free(port: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if port_is_free(port):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)


def connect_client(port: int, timeout: float = 5.0) -> socket.socket:
    """连接回环 127.0.0.1，失败时抛出 ScenarioFailure。"""
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            client = socket.create_connection((LOOPBACK, port), timeout=2.0)
            client.settimeout(0.2)
            return client
        except OSError as exc:
            last_error = exc
            time.sleep(0.05)
    raise ScenarioFailure(f"连接 {LOOPBACK}:{port} 失败（{timeout:.0f} 秒内）：{last_error}")


def recv_messages_until(client: socket.socket, deadline: float):
    """持续接收直到单调时钟截止；每项为 (recv 返回时刻, 数据)。"""
    messages = []
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        client.settimeout(min(0.2, remaining))
        try:
            data = client.recv(64)
        except socket.timeout:
            continue
        except OSError as exc:
            raise ScenarioFailure(f"接收数据失败：{exc}") from exc
        timestamp = time.monotonic()  # 接收时间取 recv 返回时刻
        if not data:
            raise ScenarioFailure("服务端在接收窗口结束前关闭了连接")
        messages.append((timestamp, data))
    return messages


def recv_first_message(client: socket.socket, timeout: float):
    """等待首条数据，返回 (recv 返回时刻, 数据)；超时或对端关闭返回 None。"""
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        client.settimeout(min(0.2, remaining))
        try:
            data = client.recv(64)
        except socket.timeout:
            continue
        except OSError:
            return None
        timestamp = time.monotonic()
        if not data:
            return None
        return timestamp, data


def split_hello_stream(data: bytes):
    """把可能合并的数据流按 5 字节切分；非 Hello 序列返回 None。"""
    if not data or len(data) % len(HELLO) != 0:
        return None
    chunks = [data[index:index + len(HELLO)] for index in range(0, len(data), len(HELLO))]
    return chunks if all(chunk == HELLO for chunk in chunks) else None


# ---------------------------------------------------------------------------
# 场景结果记录
# ---------------------------------------------------------------------------

class Result:
    def __init__(self, key: str, title: str):
        self.key = key
        self.title = title
        self.checks = []

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(ok for ok, _ in self.checks)

    def check(self, passed: bool, message: str) -> None:
        self.checks.append((bool(passed), message))
        if not passed:
            raise ScenarioFailure(message)

    def note(self, message: str) -> None:
        self.checks.append((True, message))


# ---------------------------------------------------------------------------
# 构建产物解析（S5 与自动兜底共用）
# ---------------------------------------------------------------------------

class BuildOutcome:
    def __init__(self):
        self.ok = False
        self.error = ""
        self.server_path = None
        self.messages = []


def _locate_server(build_dir: Path):
    """在构建目录中查找服务端可执行文件。"""
    if not build_dir.is_dir():
        return None
    names = ("tcp_hello_server.exe", "tcp_hello_server")
    for config in ("Release", "RelWithDebInfo", "Debug", ""):
        base = build_dir / config if config else build_dir
        for name in names:
            candidate = base / name
            if candidate.is_file():
                return candidate
    for name in names:
        for candidate in sorted(build_dir.rglob(name)):
            if "CMakeFiles" not in candidate.parts:
                return candidate
    return None


def _error_lines(output: str):
    """收集构建输出中的错误行，排除“0 Error(s)”这类成功摘要。"""
    pattern = re.compile(r"\berror\b", re.IGNORECASE)
    zero_summary = re.compile(r"\b0\s+errors?\b", re.IGNORECASE)
    return [line.strip() for line in output.splitlines()
            if pattern.search(line) and not zero_summary.search(line)]


def _run_build_step(cmd, timeout: float, action: str) -> str:
    try:
        completed = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise ScenarioFailure(f"{action}无法执行，命令不存在：{cmd[0]}（{exc}）") from exc
    except subprocess.TimeoutExpired as exc:
        raise ScenarioFailure(f"{action}超时（{timeout:.0f} 秒）：{' '.join(cmd)}") from exc
    output = completed.stdout or ""
    errors = _error_lines(output)
    if completed.returncode != 0:
        detail = "；".join(errors[:5]) if errors else output[-400:].strip()
        raise ScenarioFailure(f"{action}失败，退出码 {completed.returncode}；{detail}")
    if errors:
        raise ScenarioFailure(f"{action}输出包含错误：{'；'.join(errors[:5])}")
    return output


def ensure_build(ctx) -> BuildOutcome:
    """执行 CMake 配置与构建（结果缓存，S5 与自动兜底共用）。"""
    if ctx.build_outcome is not None:
        if not ctx.build_outcome.ok:
            raise ScenarioFailure(ctx.build_outcome.error)
        return ctx.build_outcome

    outcome = BuildOutcome()
    ctx.build_outcome = outcome
    try:
        cmake_list = ctx.project_dir / "CMakeLists.txt"
        if not cmake_list.is_file():
            raise ScenarioFailure(f"未找到构建文件 {cmake_list}（S5 依赖 ISS-001 的构建产物）")
        cmake = shutil.which("cmake")
        if cmake is None:
            raise ScenarioFailure("未找到 cmake 可执行文件（PATH 中不存在）")
        ctx.build_dir.mkdir(parents=True, exist_ok=True)
        _run_build_step([cmake, "-S", str(ctx.project_dir), "-B", str(ctx.build_dir)], 300.0, "cmake 配置")
        _run_build_step([cmake, "--build", str(ctx.build_dir), "--config", "Release"], 900.0, "cmake 构建")
        server = _locate_server(ctx.build_dir)
        if server is None:
            raise ScenarioFailure(f"构建成功但未在 {ctx.build_dir} 下找到 tcp_hello_server.exe")
        outcome.server_path = server
        outcome.messages.append(
            f"cmake 配置成功：{cmake} -S {ctx.project_dir} -B {ctx.build_dir}")
        outcome.messages.append("cmake 构建成功（--config Release），输出无错误")
        outcome.ok = True
    except ScenarioFailure as exc:
        outcome.error = str(exc)
        raise
    return outcome


def ensure_server(ctx) -> Path:
    """解析服务端可执行文件路径：命令行参数 → 环境变量 → 构建产物。"""
    if ctx.server_path is not None:
        if not ctx.server_path.is_file():
            raise ScenarioFailure(f"--server 指定的文件不存在：{ctx.server_path}")
        return ctx.server_path
    env_path = os.environ.get(SERVER_ENV)
    if env_path:
        candidate = Path(env_path)
        if not candidate.is_file():
            raise ScenarioFailure(f"环境变量 {SERVER_ENV} 指定的文件不存在：{candidate}")
        ctx.server_path = candidate
        return candidate
    server = _locate_server(ctx.build_dir)
    if server is None:
        ensure_build(ctx)
        server = _locate_server(ctx.build_dir)
    if server is None:
        raise ScenarioFailure("未能解析服务端可执行文件路径")
    ctx.server_path = server
    return server


def start_server_process(ctx, port: int, use_default_port: bool = False,
                         require_free: bool = True) -> ServerProcess:
    server = ensure_server(ctx)
    if require_free and not wait_port_free(port, 5.0):
        raise ScenarioFailure(f"端口 {port} 在场景开始前仍被占用")
    port_arg = None if use_default_port else port
    return ServerProcess.start(server, port_arg, server.parent)


def cleanup_running_server(proc: ServerProcess, result: Result) -> None:
    """场景收尾：仍在运行时统一 CTRL_BREAK；意外提前退出也如实记为失败。"""
    if proc.is_running():
        teardown = proc.terminate_clean()
        result.checks.append((teardown.passed, "收尾：" + teardown.message))
    else:
        result.checks.append((False, f"服务端在收尾前提前退出，退出码 {proc.returncode}"))
    proc.close_streams()


def _require_listening(proc: ServerProcess, result: Result, port: int) -> None:
    line = proc.wait_for_line(rf"LISTENING port={port}\b", LISTEN_TIMEOUT_S)
    if line is None:
        tail = proc.output_lines()[-5:]
        result.check(False, f"未在 {LISTEN_TIMEOUT_S:.0f} 秒内输出 LISTENING port={port}；最近输出：{tail}")
    result.note(f"启动日志：{line}")


def _validate_stream(result: Result, label: str, messages, connected_at: float, minimum: int) -> None:
    """校验一段接收流：次数、首包时延、内容、相邻间隔。"""
    result.check(len(messages) >= minimum,
                 f"{label} 在窗口内仅收到 {len(messages)} 次，期望 ≥{minimum}")
    first_delay = messages[0][0] - connected_at
    result.check(first_delay <= FIRST_HELLO_MAX_S,
                 f"{label} 首个 Hello 用时 {first_delay:.3f}s，超过 {FIRST_HELLO_MAX_S:.1f}s")
    for index, (_, data) in enumerate(messages, 1):
        result.check(len(data) == len(HELLO) and data == HELLO,
                     f"{label} 第 {index} 次收到 {data!r}（{len(data)} 字节），期望 b'Hello'（5 字节）")
    for index in range(1, len(messages)):
        delta = messages[index][0] - messages[index - 1][0]
        result.check(INTERVAL_MIN_S <= delta <= INTERVAL_MAX_S,
                     f"{label} 第 {index}→{index + 1} 次间隔 {delta:.3f}s，"
                     f"超出 [{INTERVAL_MIN_S:.1f}s, {INTERVAL_MAX_S:.1f}s]")


def check_invalid_port_arguments(server: Path, result: Result) -> None:
    """校验非法端口参数契约：非零退出且输出含 README §7 约定的错误行。

    与 CMakeLists.txt 的 ctest 负面用例使用同一判据——两项必须同时成立：
    只看退出码时，进程崩溃或运行库缺失导致的任意非零退出都会被误判为通过；
    只看输出时，退出码契约不再被校验。两者都满足才算参数校验路径工作正常。
    """
    creationflags = CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    for argument in INVALID_PORT_ARGUMENTS:
        try:
            completed = subprocess.run(
                [str(server), argument],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=CLI_TIMEOUT_S,
                creationflags=creationflags,
            )
        except subprocess.TimeoutExpired as exc:
            raise ScenarioFailure(
                f"非法端口参数 {argument!r}：服务端 {CLI_TIMEOUT_S:.0f} 秒内未退出（挂起）") from exc
        except OSError as exc:
            raise ScenarioFailure(f"非法端口参数 {argument!r}：无法启动服务端（{exc}）") from exc

        output = (completed.stdout or "") + (completed.stderr or "")
        result.check(completed.returncode != 0,
                     f"非法端口参数 {argument!r}：退出码 {completed.returncode}，"
                     f"期望非零（本实现约定 {INVALID_PORT_EXIT_CODE}）")
        result.check(INVALID_PORT_ERROR in output,
                     f"非法端口参数 {argument!r}：输出缺少 {INVALID_PORT_ERROR!r}；"
                     f"实际输出：{output[-200:]!r}")
        result.note(f"非法端口参数 {argument!r} 被拒绝：退出码 {completed.returncode}，"
                    f"输出含 {INVALID_PORT_ERROR!r}")


# ---------------------------------------------------------------------------
# 场景实现
# ---------------------------------------------------------------------------

def scenario_s1(ctx, result: Result) -> None:
    """S1/AC-1：启动日志含 LISTENING，连接后日志含带对端地址的 CONNECTED。"""
    port = ctx.port
    proc = start_server_process(ctx, port, use_default_port=(port == DEFAULT_PORT))
    client = None
    try:
        _require_listening(proc, result, port)
        client = connect_client(port)
        peer_port = client.getsockname()[1]
        result.note(f"客户端已连接 {LOOPBACK}:{port}（本地端口 {peer_port}）")
        line = proc.wait_for_line(rf"CONNECTED peer={re.escape(LOOPBACK)}:{peer_port}\b", 5.0)
        result.check(line is not None,
                     f"未输出含对端地址的日志 CONNECTED peer={LOOPBACK}:{peer_port}")
        result.note(f"连接日志：{line}")
    finally:
        if client is not None:
            client.close()
        cleanup_running_server(proc, result)


def scenario_s2(ctx, result: Result) -> None:
    """S2/AC-2：首个 ≤1.2s、5 秒内 ≥4 次、每次恰为 5 字节 Hello、间隔 ∈[0.8s,1.2s]。"""
    port = ctx.port
    proc = start_server_process(ctx, port)
    client = None
    try:
        _require_listening(proc, result, port)
        client = connect_client(port)
        connected_at = time.monotonic()          # 5 秒窗口自连接建立起算
        deadline = connected_at + 5.0
        messages = recv_messages_until(client, deadline)
        _validate_stream(result, "S2", messages, connected_at, 4)
        result.note(f"S2 共收到 {len(messages)} 次 Hello")
    finally:
        if client is not None:
            client.close()
        cleanup_running_server(proc, result)


def scenario_s3(ctx, result: Result) -> None:
    """S3/AC-3：双客户端并发，各自按秒收到 Hello，内容正确且互不影响。"""
    port = ctx.port
    proc = start_server_process(ctx, port)
    clients = {}
    try:
        _require_listening(proc, result, port)
        clients["A"] = connect_client(port)
        connected_a = time.monotonic()
        clients["B"] = connect_client(port)
        connected_b = time.monotonic()

        collected = {}
        threads = [
            threading.Thread(target=_collect_stream,
                             args=(collected, "A", clients["A"], connected_a + 3.0), daemon=True),
            threading.Thread(target=_collect_stream,
                             args=(collected, "B", clients["B"], connected_b + 3.0), daemon=True),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=6.0)
        for key in ("A", "B"):
            if key not in collected:
                result.check(False, f"S3 客户端 {key} 接收线程未在预期时间内结束")
            status, payload = collected[key]
            if status != "ok":
                raise ScenarioFailure(f"S3 客户端 {key} 接收异常：{payload}")
        _validate_stream(result, "S3 客户端 A", collected["A"][1], connected_a, 3)
        _validate_stream(result, "S3 客户端 B", collected["B"][1], connected_b, 3)
        result.note("两个客户端均独立按秒收到 Hello")
    finally:
        for client in clients.values():
            client.close()
        clients.clear()
        cleanup_running_server(proc, result)


def _collect_stream(sink, key, client, deadline) -> None:
    try:
        sink[key] = ("ok", recv_messages_until(client, deadline))
    except Exception as exc:  # 线程内异常交由主线程判定失败
        sink[key] = ("error", exc)


def scenario_s4(ctx, result: Result) -> None:
    """S4/AC-4：断开一个客户端后 2 秒内出现 DISCONNECTED、不再发送、进程存活，另一客户端继续接收、新客户端可连接。"""
    port = ctx.port
    proc = start_server_process(ctx, port)
    clients = {}
    try:
        _require_listening(proc, result, port)
        clients["A"] = connect_client(port)
        a_peer_port = clients["A"].getsockname()[1]
        clients["B"] = connect_client(port)
        b_peer_port = clients["B"].getsockname()[1]
        for label, peer_port in (("A", a_peer_port), ("B", b_peer_port)):
            line = proc.wait_for_line(rf"CONNECTED peer={re.escape(LOOPBACK)}:{peer_port}\b", 5.0)
            result.check(line is not None, f"S4 客户端 {label} 的连接日志缺失：{line!r}")

        # 服务端在连接建立后立即发送首个 Hello，先清空 A 的接收缓冲，
        # 避免把关闭前已到达的数据误判为“断开后仍在发送”。
        _drain_available(clients["A"])

        # 半关闭 A 的发送方向：服务端读端会收到 EOF，从而走断开检测路径；
        # 同时 A 仍可接收，便于断言“该连接不再收到数据”。
        closed_at = time.monotonic()
        clients["A"].shutdown(socket.SHUT_WR)
        line = proc.wait_for_line(
            rf"DISCONNECTED peer={re.escape(LOOPBACK)}:{a_peer_port}\b", 2.0)
        result.check(line is not None,
                     f"S4 关闭客户端 A 后 2 秒内未输出 DISCONNECTED peer={LOOPBACK}:{a_peer_port}")
        result.note(f"断开日志：{line}")

        leftovers = _observe_no_data(clients["A"], closed_at + 2.0)
        result.check(not leftovers, f"S4 断开后该连接仍收到数据：{leftovers!r}")

        b_messages = recv_messages_until(clients["B"], time.monotonic() + 2.0)
        result.check(len(b_messages) >= 1,
                     "S4 断开一个客户端后，另一客户端未继续收到 Hello")
        for index, (_, data) in enumerate(b_messages, 1):
            result.check(split_hello_stream(data) is not None,
                         f"S4 客户端 B 第 {index} 次收到 {data!r}，不是合法的 Hello 序列")

        result.check(proc.is_running(),
                     f"S4 断开一个客户端后服务端进程已退出，退出码 {proc.returncode}")

        clients["C"] = connect_client(port)
        first = recv_first_message(clients["C"], FIRST_HELLO_MAX_S + 0.5)
        result.check(first is not None, "S4 新客户端连接后未收到 Hello")
        result.check(first[1] == HELLO, f"S4 新客户端收到 {first[1]!r}，期望 b'Hello'")
    finally:
        for client in clients.values():
            try:
                client.close()
            except OSError:
                pass
        clients.clear()
        cleanup_running_server(proc, result)


def _drain_available(client: socket.socket, idle: float = 0.25):
    """读取并丢弃当前已缓冲的数据，直到短暂空闲或对端关闭；返回已读数据。"""
    drained = []
    while True:
        client.settimeout(idle)
        try:
            data = client.recv(64)
        except socket.timeout:
            return drained
        except OSError:
            return drained
        if not data:
            return drained
        drained.append(data)


def _observe_no_data(client: socket.socket, until: float):
    """在截至时刻前观察客户端是否还能收到数据；返回收到的数据片段列表。"""
    received = []
    while True:
        remaining = until - time.monotonic()
        if remaining <= 0:
            break
        client.settimeout(min(0.2, remaining))
        try:
            data = client.recv(64)
        except socket.timeout:
            continue
        except OSError:
            break
        if not data:
            break  # 服务端已关闭连接
        received.append(data)
    return received


def scenario_s5(ctx, result: Result) -> None:
    """S5/AC-5：CMake 配置与构建成功无错误，构建产物运行后输出监听端口；
    非法端口参数以非零退出并输出错误行（与 §6.3 的 ctest 负面用例同一判据）。"""
    outcome = ensure_build(ctx)
    for message in outcome.messages:
        result.note(message)
    result.check(outcome.server_path is not None, "构建后未定位到服务端可执行文件")
    result.note(f"构建产物：{outcome.server_path}")

    # 命令行参数校验不触碰网络端口，先于监听场景执行，失败可立即定位到参数解析路径。
    check_invalid_port_arguments(outcome.server_path, result)

    port = ctx.port
    if not wait_port_free(port, 5.0):
        result.check(False, f"端口 {port} 在 S5 运行前仍被占用")
    proc = ServerProcess.start(outcome.server_path, None if port == DEFAULT_PORT else port,
                               Path(outcome.server_path).parent)
    try:
        _require_listening(proc, result, port)
    finally:
        cleanup_running_server(proc, result)


def scenario_s6(ctx, result: Result) -> None:
    """S6/AC-6：CTRL_BREAK 后 5 秒内退出码 0，且立即以不设 SO_REUSEADDR 的新 socket 重绑成功。"""
    port = ctx.port
    proc = start_server_process(ctx, port)
    client = None
    torn_down = False
    try:
        _require_listening(proc, result, port)
        client = connect_client(port)
        teardown = proc.terminate_clean(SHUTDOWN_TIMEOUT_S)
        torn_down = True
        result.check(teardown.passed, "S6 " + teardown.message)
        proc.close_streams()

        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)  # 不设置 SO_REUSEADDR
        bind_error = None
        try:
            probe.bind(("0.0.0.0", port))
            result.note(f"退出后立即重绑 {port} 成功（未设置 SO_REUSEADDR）")
        except OSError as exc:
            bind_error = exc
        finally:
            probe.close()
        result.check(bind_error is None, f"S6 服务端退出后立即重绑 {port} 失败：{bind_error}")
    finally:
        if client is not None:
            client.close()
        if not torn_down and proc.is_running():
            teardown = proc.terminate_clean(SHUTDOWN_TIMEOUT_S)
            result.checks.append((False, "S6 异常收尾：" + teardown.message))
        proc.close_streams()
        released = wait_port_free(port, SHUTDOWN_TIMEOUT_S)
        result.checks.append((released, f"S6 结束后端口 {port} 已释放"))


def scenario_s7(ctx, result: Result) -> None:
    """S7/AC-7：端口被不设 SO_REUSEADDR 的普通 bind 占用时，服务端输出 WSA 10048 并非零退出。"""
    port = ctx.port
    blocker = None
    proc = None
    try:
        if not wait_port_free(port, 5.0):
            result.check(False, f"端口 {port} 在 S7 开始前仍被占用")
        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)  # 不设置 SO_REUSEADDR
        blocker.bind(("0.0.0.0", port))
        result.note(f"已用不设 SO_REUSEADDR 的普通 bind 占用端口 {port}")

        # 端口已被 blocker 占用，跳过 start_server_process 的空闲校验
        proc = start_server_process(ctx, port, require_free=False)
        code = proc.wait_exit(EXIT_TIMEOUT_S)
        if code is None:
            proc.force_kill()
            result.check(False, f"S7 端口被占用时服务端 {EXIT_TIMEOUT_S:.0f} 秒内未退出（挂起）")
        result.check(code != 0, f"S7 端口占用时退出码为 {code}，期望非零")
        output = "\n".join(proc.output_lines())
        result.check(re.search(r"10048", output) is not None,
                     f"S7 错误输出缺少 WSA 错误码 10048；实际输出：{output[-400:]}")
        result.check(re.search(r"(?i)port in use|address already in use|占用", output) is not None,
                     f"S7 错误输出未指明端口占用；实际输出：{output[-400:]}")
        result.check(re.search(r"(?i)\berror\b", output) is not None,
                     f"S7 未输出 ERROR 级错误日志；实际输出：{output[-400:]}")
    finally:
        if proc is not None:
            if proc.is_running():
                teardown = proc.terminate_clean(2.0)
                result.checks.append((False, "S7 兜底收尾：" + teardown.message))
            proc.close_streams()
        if blocker is not None:
            blocker.close()
        released = wait_port_free(port, SHUTDOWN_TIMEOUT_S)
        result.checks.append((released, f"S7 结束后端口 {port} 已释放"))


# ---------------------------------------------------------------------------
# 命令行与执行入口
# ---------------------------------------------------------------------------

SCENARIOS = [
    ("S1", "连接与日志（AC-1）", scenario_s1),
    ("S2", "周期发送（AC-2）", scenario_s2),
    ("S3", "双客户端并发（AC-3）", scenario_s3),
    ("S4", "断开隔离（AC-4）", scenario_s4),
    ("S5", "构建与运行（AC-5）", scenario_s5),
    ("S6", "优雅退出（AC-6）", scenario_s6),
    ("S7", "端口占用（AC-7）", scenario_s7),
]


class Context:
    def __init__(self, args):
        self.project_dir = (Path(args.project_dir).resolve()
                            if args.project_dir else Path(__file__).resolve().parents[1])
        self.build_dir = (Path(args.build_dir).resolve()
                          if args.build_dir else self.project_dir / "build")
        self.server_path = Path(args.server).resolve() if args.server else None
        self.port = args.port
        self.verbose = args.verbose
        self.build_outcome = None


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        description="REQ-029 TCP Hello 服务端验收测试（S1–S7 / AC-1～AC-7）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="服务端路径优先级：--server > 环境变量 TCP_HELLO_SERVER > 构建产物（必要时自动执行 CMake 构建）。")
    parser.add_argument("--server", help="服务端可执行文件路径（ISS-001 构建产物）")
    parser.add_argument("--project-dir", help="tcp_hello_demo 目录，默认脚本所在目录的上一级")
    parser.add_argument("--build-dir", help="CMake 构建目录，默认 <project-dir>/build")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="被测端口，默认 9000")
    parser.add_argument("--only", help="只运行指定场景，例如 S1,S6,S7")
    parser.add_argument("--verbose", "-v", action="store_true", help="输出全部断言明细")
    return parser.parse_args(argv)


def _select_scenarios(only):
    if not only:
        return list(SCENARIOS)
    wanted = {item.strip().upper() for item in only.split(",") if item.strip()}
    known = {key for key, _, _ in SCENARIOS}
    unknown = wanted - known
    if unknown:
        raise SystemExit(f"未知场景：{', '.join(sorted(unknown))}")
    return [entry for entry in SCENARIOS if entry[0] in wanted]


def _run_one(ctx, key, title, func) -> Result:
    result = Result(key, title)
    try:
        func(ctx, result)
    except ScenarioFailure as exc:
        message = str(exc)
        # Result.check 已记录过同一条失败断言时不重复追加
        if not result.checks or result.checks[-1] != (False, message):
            result.checks.append((False, message))
    except Exception as exc:  # 未预期异常同样判失败，不吞掉堆栈
        result.checks.append((False, f"未预期异常：{exc.__class__.__name__}: {exc}"))
        if ctx.verbose:
            traceback.print_exc()
    return result


def _print_result(result: Result, verbose: bool) -> None:
    status = "PASS" if result.passed else "FAIL"
    print(f"[{result.key}] {result.title}：{status}")
    if verbose or not result.passed:
        for ok, message in result.checks:
            print(f"    {'OK  ' if ok else 'FAIL'} {message}")


def main(argv=None) -> int:
    args = _parse_args(argv)
    if not 1 <= args.port <= 65535:
        print(f"端口非法：{args.port}（合法范围 1–65535）", file=sys.stderr)
        return 2
    if args.server and not Path(args.server).is_file():
        print(f"--server 指定的文件不存在：{args.server}", file=sys.stderr)
        return 2

    ctx = Context(args)
    selected = _select_scenarios(args.only)

    print(f"项目目录：{ctx.project_dir}")
    print(f"构建目录：{ctx.build_dir}")
    print(f"服务端可执行文件：{ctx.server_path or os.environ.get(SERVER_ENV) or '（自动解析构建产物）'}")
    print(f"被测端口：{ctx.port}；测试客户端地址：{LOOPBACK}")
    if os.name != "nt":
        print("警告：目标平台为 Windows，非 Windows 环境下 CTRL_BREAK 收尾与端口语义可能不成立。")

    results = []
    for key, title, func in selected:
        result = _run_one(ctx, key, title, func)
        results.append(result)
        _print_result(result, ctx.verbose)
        print()

    failed = [result.key for result in results if not result.passed]
    passed = len(results) - len(failed)
    print(f"总结：通过 {passed}/{len(results)} 个场景；失败：{', '.join(failed) if failed else '无'}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())