/**
 * @file    session.cpp
 * @brief   会话实现：每秒 Hello 推送、断开探测、线程安全日志与会话线程注册表
 * @author  hyy
 * @date    2026-11-23
 */

#include "session.h"

#include <chrono>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <string>
#include <utility>

namespace tcp_hello {

/* 私有常量 ----------------------------------------------------------------- */
namespace {

constexpr std::int64_t SEND_INTERVAL_MILLISECONDS = 1000;
constexpr std::size_t HELLO_PAYLOAD_LENGTH = 5U;
constexpr char HELLO_PAYLOAD[HELLO_PAYLOAD_LENGTH + 1U] = "Hello";
constexpr std::size_t LOG_BUFFER_SIZE = 256U;
constexpr std::size_t RECEIVE_PROBE_SIZE = 64U;

/* 私有函数 ----------------------------------------------------------------- */
/**
 * @brief  会话线程入口：在自身栈上构造 Session，保证其生命周期与线程一致
 * @param  client_socket 已接受的客户端 socket
 * @param  peer_text     对端地址文本
 */
void RunSessionWorker(SOCKET client_socket, std::string peer_text) {
    Session session(client_socket, std::move(peer_text));
    session.Run();
}

}  // namespace

/* 公共函数 ----------------------------------------------------------------- */
std::atomic<bool> &GetStopFlag(void) {
    // 函数内局部静态避免全局对象的构造顺序问题，C++11 起初始化线程安全。
    static std::atomic<bool> stop_flag{false};
    return stop_flag;
}

std::mutex &GetSessionStopMutex(void) {
    static std::mutex stop_mutex;
    return stop_mutex;
}

std::condition_variable &GetSessionStopCondition(void) {
    static std::condition_variable stop_condition;
    return stop_condition;
}

void NotifySessionsStop(void) {
    // 唤醒通知不需要持有互斥量；会话的等待谓词同时检查原子停止标志。
    GetSessionStopCondition().notify_all();
}

void LogLine(const char *text) {
    static std::mutex log_mutex;
    if (text == NULL) {
        return;
    }
    std::lock_guard<std::mutex> guard(log_mutex);
    std::printf("%s\n", text);
    // stdout 重定向到管道时默认全缓冲，逐行刷新才能让外部测试及时观察到事件。
    std::fflush(stdout);
}

/* 公共类型 ----------------------------------------------------------------- */
Session::Session(SOCKET accepted_socket, std::string peer_address_text)
    : client_socket(accepted_socket), peer_text(std::move(peer_address_text)) {
}

Session::~Session() {
    CloseSocket();
}

void Session::Run(void) {
    LogEvent("CONNECTED");
    // 连接建立后立即发送首个 Hello，满足首包 1 秒内到达并给验收留出最大余量。
    if (SendHello() == false) {
        LogEvent("DISCONNECTED");
        return;
    }

    std::unique_lock<std::mutex> lock(GetSessionStopMutex());
    while (GetStopFlag().load() == false) {
        // condition_variable::wait_for 由标准保证使用单调时钟计时，不受系统时间调整影响；
        // 停止标志置位时主线程调用 notify_all 立即唤醒等待。
        const bool is_stopped = GetSessionStopCondition().wait_for(
            lock,
            std::chrono::milliseconds(SEND_INTERVAL_MILLISECONDS),
            []() { return GetStopFlag().load() == true; });
        if (is_stopped == true) {
            break;
        }
        // 网络探测与发送不持有互斥量，避免阻塞其它会话被唤醒。
        lock.unlock();
        if (IsPeerAlive() == false) {
            LogEvent("DISCONNECTED");
            return;
        }
        if (SendHello() == false) {
            LogEvent("DISCONNECTED");
            return;
        }
        lock.lock();
    }
    // 停止标志由控制台控制事件置位：直接返回由析构函数关闭 socket，不输出 DISCONNECTED。
}

bool Session::SendHello(void) {
    std::size_t sent_total = 0U;
    while (sent_total < HELLO_PAYLOAD_LENGTH) {
        const int sent = send(client_socket, HELLO_PAYLOAD + sent_total,
                              static_cast<int>(HELLO_PAYLOAD_LENGTH - sent_total), 0);
        if (sent == SOCKET_ERROR || sent <= 0) {
            return false;
        }
        sent_total += static_cast<std::size_t>(sent);
    }
    return true;
}

bool Session::IsPeerAlive(void) {
    fd_set read_set = {};
    FD_ZERO(&read_set);
    FD_SET(client_socket, &read_set);
    timeval timeout = {};
    timeout.tv_sec = 0;
    timeout.tv_usec = 0;
    const int ready_count = select(0, &read_set, NULL, NULL, &timeout);
    if (ready_count == SOCKET_ERROR) {
        return false;
    }
    if (ready_count == 0) {
        return true;
    }

    // 读端可读：recv 返回 0 表示对端已发送 FIN，任何错误都按断开处理。
    char probe_buffer[RECEIVE_PROBE_SIZE] = {0};
    const int received = recv(client_socket, probe_buffer, static_cast<int>(sizeof(probe_buffer)), 0);
    if (received == 0) {
        return false;
    }
    if (received == SOCKET_ERROR) {
        return false;
    }
    // 客户端发来的数据不解析，读出后直接丢弃即可。
    return true;
}

void Session::CloseSocket(void) {
    if (client_socket != INVALID_SOCKET) {
        closesocket(client_socket);
        client_socket = INVALID_SOCKET;
    }
}

void Session::LogEvent(const char *event) {
    if (event == NULL) {
        return;
    }
    char message[LOG_BUFFER_SIZE];
    const int written = std::snprintf(message, sizeof(message), "%s peer=%s", event, peer_text.c_str());
    if (written > 0) {
        LogLine(message);
    }
}

void SessionRegistry::StartSession(SOCKET client_socket, const std::string &peer_text) {
    std::lock_guard<std::mutex> guard(registry_mutex);
    workers.emplace_back(RunSessionWorker, client_socket, peer_text);
}

void SessionRegistry::JoinAll(void) {
    std::lock_guard<std::mutex> guard(registry_mutex);
    for (std::thread &worker : workers) {
        if (worker.joinable() == true) {
            worker.join();
        }
    }
    workers.clear();
}

}  // namespace tcp_hello