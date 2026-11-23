# TCP Hello Demo（Windows / C++ / Winsock）

一个最小可运行的 Windows 平台 C++ TCP 服务端 Demo：在指定端口监听，客户端连接后
**立即发送一次、此后每秒发送一次字面 5 字节 `Hello`**（不带换行符），支持多客户端并发，
客户端断开后自动清理资源，Ctrl+C / Ctrl+Break 可控退出。

- 需求：REQ-029（FR-1～FR-6 / AC-1～AC-7）
- 目标平台：**仅 Windows 10/11**（Winsock + MSVC + CMake），不做 POSIX 兼容
- 目录：`tcp_hello_demo/`

---

## 1. 行为概述

| 项目 | 约定 |
|---|---|
| 传输协议 | TCP（IPv4，监听 `0.0.0.0:<port>`） |
| 默认端口 | 9000，可由命令行第一个参数覆盖 |
| 端口合法范围 | 1–65535（十进制，非法取值直接报错退出） |
| 负载 | 字面 5 字节 `Hello`（`0x48 0x65 0x6C 0x6C 0x6F`），无换行、无分帧 |
| 发送节奏 | 连接建立后**立即**发送首个 `Hello`，此后每 1 秒一次 |
| 并发 | 每个连接一个独立线程，互不影响；Demo 验收覆盖 2 个并发连接 |
| 客户端上行数据 | 不解析、不响应，仅用于探测连接是否仍存活 |
| 断开检测 | 读端 EOF（`recv` 返回 0）或 `send` 失败即判定断开，2 秒内清理并输出日志 |
| 监听 socket | **不设置 `SO_REUSEADDR`**（原因见 §9） |
| 日志 | 仅 stdout，逐行立即刷新，格式固定（见 §7） |

---

## 2. 前提条件与版本要求

| 依赖 | 版本要求 | 说明 |
|---|---|---|
| 操作系统 | Windows 10 / Windows 11（x64） | 依赖 Winsock2 与控制台控制事件（`SetConsoleCtrlHandler`） |
| 编译器 | MSVC（Visual Studio 2019 16.x 或 Visual Studio 2022 17.x） | 需安装工作负载“**使用 C++ 的桌面开发**”；语言标准为 C++17 |
| CMake | **≥ 3.15**（`CMakeLists.txt` 的 `cmake_minimum_required`） | 安装时勾选“Add CMake to the system PATH”，或使用 VS 自带的 CMake |
| 命令行 | PowerShell 5.1（Windows 内置）或 cmd | 下文命令均以 PowerShell 给出 |
| Python | **Python 3.8 及以上**（仅运行验收脚本时需要） | 脚本仅用标准库：`socket / subprocess / ctypes / time` |
| 网络 | 本机回环 `127.0.0.1` 可用 | 测试客户端统一连接回环地址 |

> 建议从 **“x64 Native Tools Command Prompt for VS 2022”** 或
> **“Developer PowerShell for VS 2022”** 中执行构建命令，这样 `cl.exe` 与 Windows SDK 已在环境变量中。
> 若在普通 PowerShell 中构建且机器上存在多个生成器，可显式指定生成器：
> `cmake -S . -B build -G "Visual Studio 17 2022" -A x64`。

环境自检（三条命令均应有输出且版本满足上表）：

```powershell
cmake --version
cl
python --version
```

---

## 3. 目录结构

```
tcp_hello_demo/
├── CMakeLists.txt          # MSVC + CMake 构建，链接 ws2_32；含非法端口的 ctest 负面用例
├── README.md               # 本文件
├── .gitignore              # 排除 build/ 构建产物
├── src/
│   ├── main.cpp            # 入口：参数解析、WSAStartup、监听、控制事件、accept 循环
│   ├── session.h           # 会话接口：停止信号、线程安全日志、会话注册表
│   └── session.cpp         # 会话实现：每秒 Hello 推送、断开探测
├── tests/
│   └── test_server.py      # 轻量验收脚本，覆盖 AC-1～AC-7（S1–S7）
└── build/                  # 构建产物（本地生成，已被 .gitignore 排除，不入库）
```

---

## 4. 构建（干净构建目录）

在仓库根目录执行：

```powershell
cd tcp_hello_demo
cmake -S . -B build
cmake --build build --config Release
```

- 第一条完成配置并在 `tcp_hello_demo/build/` 生成工程（构建目录不存在时会自动创建）。
- 第二条以 Release 配置编译，**成功时输出无错误**。
- 可执行文件路径：`tcp_hello_demo/build/Release/tcp_hello_server.exe`。

从**干净构建目录**重新构建（例如怀疑缓存有问题时）：

```powershell
cd tcp_hello_demo
Remove-Item -Recurse -Force build
cmake -S . -B build
cmake --build build --config Release
```

构建目录 `build/` 是本地产物，已列入 `.gitignore`，不会入库；删除它即完成清理。

---

## 5. 运行与更换端口

使用默认端口 9000：

```powershell
cd tcp_hello_demo
.\build\Release\tcp_hello_server.exe
```

启动成功后 stdout 输出（9000 为当前监听端口）：

```
LISTENING port=9000
```

更换端口：把端口作为**第一个命令行参数**传入（合法范围 1–65535）：

```powershell
.\build\Release\tcp_hello_server.exe 9100
```

此时应输出 `LISTENING port=9100`，后续客户端需连接 9100。参数非法（如 `0`、`65536`、非数字）时：

```
ERROR invalid port argument: 65536 (expected 1-65535)
Usage: tcp_hello_server.exe [port]
```

并以退出码 2 结束（见 §8）。

正常停止：在运行窗口按 **Ctrl+C** 或 **Ctrl+Break**，进程释放端口并以退出码 0 退出，
stdout 输出 `SHUTDOWN complete`。

> 运行期间服务端不解析客户端发来的内容；客户端发数据不会影响 `Hello` 推送节奏。

### 5.1 手工连接验证（观察每秒 Hello）

另开一个 PowerShell 窗口，用 .NET `TcpClient` 连接并连续读取 3 次：

```powershell
$client = New-Object System.Net.Sockets.TcpClient('127.0.0.1', 9000)
$stream = $client.GetStream()
$buffer = New-Object byte[] 64
for ($i = 0; $i -lt 3; $i++) {
    $n = $stream.Read($buffer, 0, $buffer.Length)
    [System.Text.Encoding]::ASCII.GetString($buffer, 0, $n)
}
$client.Close()
```

应依次打印三行 `Hello`（首次几乎立即到达，后续约每秒一次）；服务端窗口同时出现：

```
CONNECTED peer=127.0.0.1:<客户端端口>
```

关闭客户端后 2 秒内服务端输出 `DISCONNECTED peer=127.0.0.1:<客户端端口>`，进程继续运行。

---

## 6. 验收测试（S1–S7 全量回归）

脚本：`tcp_hello_demo/tests/test_server.py`，场景与验收标准一一对应：

| 场景 | 验收标准 | 覆盖内容 |
|---|---|---|
| S1 | AC-1 | 启动日志 `LISTENING`、连接成功、`CONNECTED` 含对端地址 |
| S2 | AC-2 | 首包 ≤1.2 秒、5 秒内 ≥4 次、每次恰为 5 字节 `Hello`、间隔 ∈[0.8s,1.2s] |
| S3 | AC-3 | 双客户端并发，各自按秒收到 `Hello` |
| S4 | AC-4 | 断开一个客户端后 2 秒内 `DISCONNECTED`、不再发送、进程存活、其他连接继续、新连接可用 |
| S5 | AC-5 | 按 CMake 配置与构建成功无错误，产物运行后输出监听端口 |
| S6 | AC-6 | `CTRL_BREAK_EVENT` 后 5 秒内退出码 0，且以不设 `SO_REUSEADDR` 的新 socket 立即重绑同一端口 |
| S7 | AC-7 | 端口被占用时输出含 `10048` 的明确错误并非零退出 |

### 6.1 运行全量 S1–S7

在仓库根目录执行（先把构建产物路径传给脚本）：

```powershell
python tcp_hello_demo/tests/test_server.py --server tcp_hello_demo/build/Release/tcp_hello_server.exe
```

也可以在 `tcp_hello_demo/` 目录内执行：

```powershell
cd tcp_hello_demo
python tests/test_server.py --server build/Release/tcp_hello_server.exe
```

- 不传 `--server` 时，脚本按“`--server` > 环境变量 `TCP_HELLO_SERVER` > 构建产物”的顺序解析；
  构建产物不存在时会自动执行 §4 的 `cmake` 配置与构建（等价于 S5 的步骤）。
- 脚本退出码：`0` = 全部场景通过；`1` = 存在失败场景（每个场景单独打印 PASS/FAIL 与断言明细）；
  `2` = 脚本自身参数非法（`--port` 超出 1–65535，或 `--server` 指定的文件不存在）。
- 被测端口默认 9000；如需换端口：`--port 9100`（服务端与测试客户端会同步使用该端口）。

常用参数：

```powershell
python tcp_hello_demo/tests/test_server.py --server tcp_hello_demo/build/Release/tcp_hello_server.exe -v
python tcp_hello_demo/tests/test_server.py --server tcp_hello_demo/build/Release/tcp_hello_server.exe --only S1,S6,S7
```

| 参数 | 含义 |
|---|---|
| `--server <path>` | 服务端可执行文件路径（ISS-001 构建产物） |
| `--project-dir <dir>` | `tcp_hello_demo` 目录，默认脚本所在目录的上一级 |
| `--build-dir <dir>` | CMake 构建目录，默认 `<project-dir>/build` |
| `--port <n>` | 被测端口，默认 9000 |
| `--only S1,S6,S7` | 只运行指定场景 |
| `--verbose` / `-v` | 输出全部断言明细 |

### 6.2 运行约束与回归口径

- **必须在 Windows 上运行**：脚本通过 `GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT, pid)` 收尾，
  所有服务端进程均以 `CREATE_NEW_PROCESS_GROUP` 启动（因此进程组 ID 等于 PID）。
  非 Windows 环境脚本会给出警告，S6 等场景不成立。
- **被测端口（默认 9000）必须空闲**：脚本在各场景开始前用不设 `SO_REUSEADDR` 的 socket 探测端口，
  被占用则判该场景失败；请先结束占用该端口的进程（`netstat -ano | findstr :9000`）。
- **S6 / S7 串行执行且场景间确认端口已释放**：脚本按 S1→S7 顺序串行运行；各场景开始前会等待
  端口空闲（最多 5 秒），S6 / S7 收尾后还会显式确认端口已释放；不要并行启动多个测试实例。
- **全量回归口径**：**任何代码变更后（含 `src/`、`CMakeLists.txt`、测试脚本）都必须重跑
  S1–S7 全量**，不允许只跑子集作为回归结论；调试时可临时用 `--only` 缩小范围，但回归结论以全量为准。
- 计时断言统一 ±200ms 容差，测试客户端统一连接回环 `127.0.0.1`。

### 6.3 命令行负面用例（ctest，可选）

`CMakeLists.txt` 另外注册了 3 个非法端口用例（端口 `0`、`65536`、非数字），可在构建后执行：

```powershell
cd tcp_hello_demo\build
ctest -C Release --output-on-failure
```

三个用例均以“服务端非零退出”为通过判据。

---

## 7. stdout 日志格式

所有日志写入 stdout，**每行产生后立即刷新**（重定向到管道时也能实时读取），格式固定，
可直接用于脚本断言。`<ip:port>` 为客户端对端地址，例如 `127.0.0.1:54321`。

| 事件 | 格式与示例 | 出现时机 |
|---|---|---|
| 监听成功 | `LISTENING port=9000` | `bind` + `listen` 成功后立即输出，`<port>` 为实际监听端口 |
| 新连接 | `CONNECTED peer=127.0.0.1:54321` | 每接受一个连接输出一条 |
| 断开 | `DISCONNECTED peer=127.0.0.1:54321` | 检测到对端断开（EOF 或发送失败）时输出 |
| 端口占用 | `ERROR bind failed: port in use (WSA 10048)` | 目标端口已被占用（`WSAEADDRINUSE`=10048） |
| 其他启动错误 | `ERROR bind failed (WSA <code>)`、`ERROR socket failed (WSA <code>)`、`ERROR listen failed (WSA <code>)`、`ERROR WSAStartup failed (WSA <code>)` | 对应 Winsock 调用失败，`<code>` 为 `WSAGetLastError()` 数值 |
| 运行期错误 | `ERROR accept failed (WSA <code>)`、`ERROR select failed (WSA <code>)` | accept/select 异常（停止过程中的 accept 失败不输出） |
| 参数错误 | `ERROR invalid port argument: <arg> (expected 1-65535)`、`ERROR too many arguments`、`Usage: tcp_hello_server.exe [port]` | 命令行参数非法时 |
| 正常退出 | `SHUTDOWN complete` | 控制台控制事件触发、清理完成后输出（仅正常退出时输出） |

说明：

- 以控制事件（Ctrl+C / Ctrl+Break）退出时，服务端**不逐个输出**活动连接的 `DISCONNECTED`，
  只在清理完成后输出 `SHUTDOWN complete`。
- 启动失败时只输出对应的 `ERROR ...` 行，不输出 `SHUTDOWN complete`。

---

## 8. 退出码语义

| 退出码 | 含义 | 典型触发 |
|---|---|---|
| `0` | 正常退出 | 收到 `CTRL_C_EVENT` / `CTRL_BREAK_EVENT`，停止监听、回收会话、释放端口后退出 |
| `1` | 启动或运行失败 | `socket()` / `bind()` / `listen()` 失败（含端口占用 10048）、`select` 失败、`SetConsoleCtrlHandler` 注册失败 |
| `2` | 命令行参数非法 | 端口参数非数字 / 超出 1–65535、参数个数多于 1 个 |
| `3` | `WSAStartup` 失败（Winsock 初始化失败） | 系统网络栈异常等 |

约定：**0 = 控制事件正常退出；非零 = 启动失败或参数错误**。验收脚本据此区分正常退出与启动错误
（例如 S7 断言“非零退出且输出含 10048”，S6 断言“退出码 0”）。

---

## 9. 监听 socket 不设置 SO_REUSEADDR 的说明

实现中监听 socket **不调用 `setsockopt(SO_REUSEADDR)`**。原因与可观测影响：

- Windows 下 `SO_REUSEADDR` 的语义与 POSIX 不同：在部分情况下它允许新 socket **抢占**
  已被其他 socket 绑定的端口，导致“端口已被占用”无法被可靠检测。
- 不设置后，端口被占用时 `bind` 会稳定返回 `WSAEADDRINUSE`（10048），服务端输出
  `ERROR bind failed: port in use (WSA 10048)` 并以非零码退出（见退出码 1），行为确定、可断言。
- 正常退出时服务端主动 `closesocket` 全部 socket，端口立即释放；退出后以**同样不设置
  `SO_REUSEADDR`** 的新 socket 可以立即重新绑定同一端口（AC-6/S6 的断言依据）。

> 因此测试脚本的所有端口探测 socket 也一律不设置 `SO_REUSEADDR`，与实现保持同一语义。

---

## 10. 防火墙检查

本 Demo 监听 `0.0.0.0:<port>`。**使用回环地址 `127.0.0.1` 的本地测试通常不经过防火墙**，
无需放行规则；只有从其他机器访问时才需要在本机放行入站 TCP 端口。

1. 查看当前防火墙配置文件状态：

   ```powershell
   netsh advfirewall show currentprofile
   ```

2. 检查是否已有针对 9000 端口的规则：

   ```powershell
   netsh advfirewall firewall show rule name=all | Select-String -Pattern "9000"
   ```

   或直接查看监听状态（确认服务端真的进入监听）：

   ```powershell
   netstat -ano | findstr :9000
   Get-NetTCPConnection -LocalPort 9000 -State Listen
   ```

3. 需要放行入站 TCP 9000（**需管理员权限的 PowerShell**）：

   ```powershell
   netsh advfirewall firewall add rule name="TCP Hello Demo 9000" dir=in action=allow protocol=TCP localport=9000
   ```

4. 验证/删除规则（删除同样需管理员权限）：

   ```powershell
   netsh advfirewall firewall show rule name="TCP Hello Demo 9000"
   netsh advfirewall firewall delete rule name="TCP Hello Demo 9000"
   ```

> 若从其他机器连接失败但本机 `netstat` 已显示监听：先确认防火墙规则与端口是否一致，
> 再确认客户端使用的是服务端所在机器的 IP 与同一端口。测试脚本无需任何防火墙配置。

---

## 11. 常见问题

| 现象 | 原因与处理 |
|---|---|
| `cmake -S . -B build` 报找不到编译器 | 未在 MSVC 环境执行。使用 “x64 Native Tools Command Prompt for VS 2022”，或加 `-G "Visual Studio 17 2022" -A x64`，或确认已安装“使用 C++ 的桌面开发”工作负载 |
| `cmake_minimum_required` 版本报错 | CMake 低于 3.15，升级 CMake |
| `message(FATAL_ERROR "tcp_hello_demo 仅支持 Windows（Winsock）平台。")` | 在非 Windows 平台配置，本 Demo 不支持 Linux/macOS |
| 启动即输出 `ERROR bind failed: port in use (WSA 10048)` 并退出 1 | 端口被占用。用 `netstat -ano \| findstr :9000` 找到占用进程，或换端口运行（如 `tcp_hello_server.exe 9100`） |
| 启动即输出其他 `ERROR ... (WSA <code>)` 并退出 1/3 | 按日志中的 Winsock 错误码定位；退出码 3 表示 `WSAStartup` 失败 |
| 测试脚本 S6/S7 失败且提示端口仍被占用 | 上一次测试残留进程或端口未释放；先结束占用进程，再重跑全量 S1–S7 |
| 客户端收不到数据 | 确认连接的是回环 `127.0.0.1` 与服务端实际端口，且服务端已输出 `LISTENING port=<port>` |

---

## 12. 构建产物管理

- `.gitignore` 已排除 `build/`：构建产物、CMake 缓存与中间文件均不入库。
- 提交前请确认 `git status` 中不包含 `build/` 下的任何文件。