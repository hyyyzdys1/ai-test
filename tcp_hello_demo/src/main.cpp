/**
 * @file    main.cpp
 * @brief   TCP Hello Demo 服务端入口：参数解析、Winsock 生命周期、监听与优雅退出
 * @author  hyy
 * @date    2026-11-23
 */

#include "session.h"

#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <string>

// winsock2.h 必须先于 windows.h 包含（session.h 已先行包含），避免引入旧版 winsock.h。
#include <winsock2.h>
#include <windows.h>

/* 私有常量 ----------------------------------------------------------------- */
namespace {

constexpr std::uint16_t DEFAULT_PORT = 9000U;
constexpr std::uint32_t MIN_PORT = 1U;
constexpr std::uint32_t MAX_PORT = 65535U;
constexpr long ACCEPT_SELECT_TIMEOUT_USEC = 200000L;
constexpr std::size_t LOG_BUFFER_SIZE = 256U;
constexpr std::size_t PEER_TEXT_SIZE = 64U;
constexpr int EXIT_CODE_OK = 0;
constexpr int EXIT_CODE_STARTUP_ERROR = 1;
constexpr int EXIT_CODE_INVALID_ARGUMENT = 2;
constexpr int EXIT_CODE_WSA_STARTUP_ERROR = 3;

/* 私有函数 ----------------------------------------------------------------- */
void PrintUsage(void) {
    tcp_hello::LogLine("Usage: tcp_hello_server.exe [port]");
}

void LogSocketError(const char *operation, int error_code) {
    char message[LOG_BUFFER_SIZE];
    const int written = std::snprintf(message, sizeof(message),
                                      "ERROR %s failed (WSA %d)", operation, error_code);
    if (written > 0) {
        tcp_hello::LogLine(message);
    } else {
        tcp_hello::LogLine("ERROR socket operation failed");
    }
}

void LogBindError(int error_code) {
    char message[LOG_BUFFER_SIZE];
    int written = 0;
    if (error_code == WSAEADDRINUSE) {
        written = std::snprintf(message, sizeof(message),
                                "ERROR bind failed: port in use (WSA %d)", error_code);
    } else {
        written = std::snprintf(message, sizeof(message),
                                "ERROR bind failed (WSA %d)", error_code);
    }
    if (written > 0) {
        tcp_hello::LogLine(message);
    } else {
        tcp_hello::LogLine("ERROR bind failed");
    }
}

void LogInvalidPort(const char *argument) {
    char message[LOG_BUFFER_SIZE];
    const int written = std::snprintf(message, sizeof(message),
                                      "ERROR invalid port argument: %s (expected 1-65535)", argument);
    if (written > 0) {
        tcp_hello::LogLine(message);
    } else {
        tcp_hello::LogLine("ERROR invalid port argument");
    }
}

bool ParsePort(const char *text, std::uint16_t &out_port) {
    if (text == NULL || *text == '\0') {
        return false;
    }

    // 只接受纯十进制数字，避免 atoi 语义宽松导致的非法取值被静默接受。
    std::uint32_t value = 0U;
    for (const char *cursor = text; *cursor != '\0'; ++cursor) {
        if (*cursor < '0' || *cursor > '9') {
            return false;
        }
        const std::uint32_t digit = static_cast<std::uint32_t>(static_cast<unsigned char>(*cursor)) -
                                    static_cast<std::uint32_t>(static_cast<unsigned char>('0'));
        value = value * 10U + digit;
        if (value > MAX_PORT) {
            return false;
        }
    }
    if (value < MIN_PORT) {
        return false;
    }

    out_port = static_cast<std::uint16_t>(value);
    return true;
}

bool FormatPeerText(const sockaddr_in &peer_address, char *out_text, std::size_t text_size) {
    if (out_text == NULL || text_size == 0U) {
        return false;
    }

    // 监听 socket 固定为 AF_INET，按网络字节序逐字节展开 IPv4 地址，
    // 既避免依赖 inet_ntop 的 SDK 版本差异，也不使用非线程安全的静态缓冲区。
    const unsigned char *octets =
        reinterpret_cast<const unsigned char *>(&peer_address.sin_addr.s_addr);
    const std::uint16_t peer_port = ntohs(peer_address.sin_port);
    const int written = std::snprintf(out_text, text_size, "%u.%u.%u.%u:%u",
                                      static_cast<unsigned>(octets[0]),
                                      static_cast<unsigned>(octets[1]),
                                      static_cast<unsigned>(octets[2]),
                                      static_cast<unsigned>(octets[3]),
                                      static_cast<unsigned>(peer_port));
    return written > 0;
}

void AcceptOne(SOCKET listen_socket, tcp_hello::SessionRegistry &registry) {
    sockaddr_in peer_address = {};
    int peer_length = static_cast<int>(sizeof(peer_address));
    const SOCKET client_socket = accept(listen_socket,
                                        reinterpret_cast<sockaddr *>(&peer_address),
                                        &peer_length);
    if (client_socket == INVALID_SOCKET) {
        // 停止流程中的 accept 失败属于预期情况，不再输出错误。
        if (tcp_hello::GetStopFlag().load() == false) {
            LogSocketError("accept", WSAGetLastError());
        }
        return;
    }

    char peer_text[PEER_TEXT_SIZE] = {0};
    if (FormatPeerText(peer_address, peer_text, sizeof(peer_text)) == false) {
        tcp_hello::LogLine("ERROR failed to format peer address");
        closesocket(client_socket);
        return;
    }
    registry.StartSession(client_socket, std::string(peer_text));
}

int AcceptLoop(SOCKET listen_socket, tcp_hello::SessionRegistry &registry) {
    // accept 不允许无限阻塞：每 200ms 用 select 轮询一次监听 socket 并检查停止标志。
    while (tcp_hello::GetStopFlag().load() == false) {
        fd_set read_set = {};
        FD_ZERO(&read_set);
        FD_SET(listen_socket, &read_set);
        timeval timeout = {};
        timeout.tv_sec = 0;
        timeout.tv_usec = ACCEPT_SELECT_TIMEOUT_USEC;
        const int ready_count = select(0, &read_set, NULL, NULL, &timeout);
        if (ready_count == SOCKET_ERROR) {
            const int error_code = WSAGetLastError();
            if (error_code == WSAEINTR) {
                continue;
            }
            LogSocketError("select", error_code);
            return EXIT_CODE_STARTUP_ERROR;
        }
        if (ready_count == 0) {
            continue;
        }
        if (FD_ISSET(listen_socket, &read_set) != 0) {
            AcceptOne(listen_socket, registry);
        }
    }
    return EXIT_CODE_OK;
}

int RunServer(std::uint16_t port) {
    const SOCKET listen_socket = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (listen_socket == INVALID_SOCKET) {
        LogSocketError("socket", WSAGetLastError());
        return EXIT_CODE_STARTUP_ERROR;
    }

    sockaddr_in local_address = {};
    local_address.sin_family = AF_INET;
    local_address.sin_addr.s_addr = htonl(INADDR_ANY);
    local_address.sin_port = htons(port);

    // 不设置 SO_REUSEADDR：Windows 下其语义可能允许端口抢占，关闭后端口占用检测才确定，
    // 也保证正常退出后同一端口可以立即被重新绑定。
    if (bind(listen_socket, reinterpret_cast<const sockaddr *>(&local_address),
             static_cast<int>(sizeof(local_address))) == SOCKET_ERROR) {
        LogBindError(WSAGetLastError());
        closesocket(listen_socket);
        return EXIT_CODE_STARTUP_ERROR;
    }
    if (listen(listen_socket, SOMAXCONN) == SOCKET_ERROR) {
        LogSocketError("listen", WSAGetLastError());
        closesocket(listen_socket);
        return EXIT_CODE_STARTUP_ERROR;
    }

    char listening_message[LOG_BUFFER_SIZE];
    const int written = std::snprintf(listening_message, sizeof(listening_message),
                                      "LISTENING port=%u", static_cast<unsigned>(port));
    if (written > 0) {
        tcp_hello::LogLine(listening_message);
    }

    tcp_hello::SessionRegistry registry;
    const int loop_result = AcceptLoop(listen_socket, registry);

    // 无论因控制事件还是监听错误退出，都先唤醒并等待全部会话线程，避免残留线程悬挂。
    tcp_hello::GetStopFlag().store(true);
    tcp_hello::NotifySessionsStop();
    registry.JoinAll();
    closesocket(listen_socket);
    return loop_result;
}

BOOL WINAPI ConsoleCtrlHandler(DWORD ctrl_type) {
    // 控制台控制事件处理函数只置位停止标志；socket 清理与线程回收统一在主线程完成。
    if (ctrl_type == CTRL_C_EVENT || ctrl_type == CTRL_BREAK_EVENT) {
        tcp_hello::GetStopFlag().store(true);
        return TRUE;
    } else {
        return FALSE;
    }
}

}  // namespace

/* 公共函数 ----------------------------------------------------------------- */
int main(int argc, char *argv[]) {
    std::uint16_t port = DEFAULT_PORT;
    if (argc > 2) {
        tcp_hello::LogLine("ERROR too many arguments");
        PrintUsage();
        return EXIT_CODE_INVALID_ARGUMENT;
    }
    if (argc == 2) {
        if (ParsePort(argv[1], port) == false) {
            LogInvalidPort(argv[1]);
            PrintUsage();
            return EXIT_CODE_INVALID_ARGUMENT;
        }
    }

    if (SetConsoleCtrlHandler(ConsoleCtrlHandler, TRUE) == FALSE) {
        char handler_message[LOG_BUFFER_SIZE];
        const int written = std::snprintf(handler_message, sizeof(handler_message),
                                          "ERROR SetConsoleCtrlHandler failed (GetLastError %lu)",
                                          static_cast<unsigned long>(GetLastError()));
        if (written > 0) {
            tcp_hello::LogLine(handler_message);
        } else {
            tcp_hello::LogLine("ERROR SetConsoleCtrlHandler failed");
        }
        return EXIT_CODE_STARTUP_ERROR;
    }

    WSADATA wsa_data = {};
    // WSAStartup 直接返回错误码，其取值与 WSAGetLastError 的错误码空间一致。
    const int startup_result = WSAStartup(MAKEWORD(2, 2), &wsa_data);
    if (startup_result != 0) {
        LogSocketError("WSAStartup", startup_result);
        return EXIT_CODE_WSA_STARTUP_ERROR;
    }

    const int exit_code = RunServer(port);
    const int cleanup_result = WSACleanup();
    (void)cleanup_result;
    if (exit_code == EXIT_CODE_OK) {
        tcp_hello::LogLine("SHUTDOWN complete");
    }
    return exit_code;
}
