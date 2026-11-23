/**
 * @file    session.h
 * @brief   TCP Hello Demo 会话接口：停止信号、线程安全日志与每连接会话线程注册表
 * @author  hyy
 * @date    2026-11-23
 */

#ifndef TCP_HELLO_DEMO_SRC_SESSION_H
#define TCP_HELLO_DEMO_SRC_SESSION_H

// 本头文件只服务于 C++ 代码，接口使用标准库类型，不提供 C 语言链接保护。
#include <atomic>
#include <condition_variable>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include <winsock2.h>

namespace tcp_hello {

/* 公共函数 ----------------------------------------------------------------- */
/**
 * @brief  取得全局停止标志
 * @return 停止标志引用，进程生命周期内有效
 */
std::atomic<bool> &GetStopFlag(void);

/**
 * @brief  取得会话等待使用的互斥量
 * @return 互斥量引用，进程生命周期内有效
 */
std::mutex &GetSessionStopMutex(void);

/**
 * @brief  取得会话等待使用的条件变量
 * @return 条件变量引用，进程生命周期内有效
 */
std::condition_variable &GetSessionStopCondition(void);

/**
 * @brief  唤醒全部等待中的会话线程，供主线程在退出流程中调用
 * @return 无
 */
void NotifySessionsStop(void);

/**
 * @brief  线程安全地向 stdout 输出一行日志并立即刷新
 * @param  text 待输出文本，调用方保证以 NUL 结尾；传入 NULL 时忽略
 * @return 无
 */
void LogLine(const char *text);

/* 公共类型 ----------------------------------------------------------------- */
/**
 * @brief  单个客户端连接的会话，在独立 std::thread 中执行
 */
class Session {
public:
    /**
     * @brief  构造会话
     * @param  accepted_socket 已接受的客户端 socket，会话结束时由本对象关闭
     * @param  peer_address_text 对端地址文本，格式为 ip:port
     */
    Session(SOCKET accepted_socket, std::string peer_address_text);

    // 会话持有 socket 并绑定到固定线程执行，禁止拷贝。
    Session(const Session &) = delete;
    Session &operator=(const Session &) = delete;

    ~Session();

    /**
     * @brief  启动会话主循环：输出 CONNECTED、立即发送首个 Hello，此后每秒发送
     * @return 无
     */
    void Run(void);

private:
    /**
     * @brief  发送完整的 5 字节 Hello 负载
     * @return true 表示全部发送成功，false 表示对端已不可写
     */
    bool SendHello(void);

    /**
     * @brief  探测对端是否已关闭：select 可读后 recv 返回 0 即视为 EOF
     * @return true 表示连接仍可用，false 表示已断开
     */
    bool IsPeerAlive(void);

    /**
     * @brief  关闭客户端 socket 并置为 INVALID_SOCKET，保证只关闭一次
     * @return 无
     */
    void CloseSocket(void);

    /**
     * @brief  按统一格式输出会话事件日志
     * @param  event 事件名，例如 CONNECTED 或 DISCONNECTED
     * @return 无
     */
    void LogEvent(const char *event);

    SOCKET client_socket;
    std::string peer_text;
};

/**
 * @brief  会话注册表：以互斥量保护线程容器，退出时统一 join 全部会话线程
 */
class SessionRegistry {
public:
    SessionRegistry(void) = default;
    SessionRegistry(const SessionRegistry &) = delete;
    SessionRegistry &operator=(const SessionRegistry &) = delete;

    /**
     * @brief  为新连接创建会话线程并登记到受互斥量保护的容器
     * @param  client_socket 已接受的客户端 socket，所有权转移给会话线程
     * @param  peer_text     对端地址文本，格式为 ip:port
     * @return 无
     */
    void StartSession(SOCKET client_socket, const std::string &peer_text);

    /**
     * @brief  等待全部会话线程结束；调用前应先置位停止标志并唤醒等待
     * @return 无
     */
    void JoinAll(void);

private:
    std::mutex registry_mutex;
    std::vector<std::thread> workers;
};

}  // namespace tcp_hello

#endif  // TCP_HELLO_DEMO_SRC_SESSION_H