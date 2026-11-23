cmake_minimum_required(VERSION 3.15)

project(tcp_hello_demo LANGUAGES CXX)

# 本 Demo 只面向 Windows + Winsock（MSVC 工具链），不做 POSIX 兼容。
if(NOT WIN32)
    message(FATAL_ERROR "tcp_hello_demo 仅支持 Windows（Winsock）平台。")
endif()

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_executable(tcp_hello_server
    src/main.cpp
    src/session.cpp
    src/session.h
)

target_include_directories(tcp_hello_server PRIVATE src)

# 显式链接 Winsock 库；std::thread 依赖的 MSVC 运行库由工具链默认提供。
target_link_libraries(tcp_hello_server PRIVATE ws2_32)

target_compile_definitions(tcp_hello_server PRIVATE
    WIN32_LEAN_AND_MEAN
    NOMINMAX
)

if(MSVC)
    target_compile_options(tcp_hello_server PRIVATE /W4 /permissive- /utf-8 /GR-)
else()
    target_compile_options(tcp_hello_server PRIVATE -Wall -Wextra)
endif()

enable_testing()

# 非法端口参数的负面用例：不依赖网络即可验证 FR-1 的参数校验退出路径。
#
# 通过判据必须【同时】满足两点：服务端非零退出 + 输出含 README §7 约定的错误行。
#   - 只用 WILL_FAIL：进程崩溃或因运行库缺失导致的启动失败同样是非零退出，会被误判为通过；
#   - WILL_FAIL 与 PASS_REGULAR_EXPRESSION 组合：CMake 中 PASS_REGULAR_EXPRESSION 一旦设置，
#     就用“输出是否匹配正则”覆盖退出码判定（匹配=通过，与退出码无关），WILL_FAIL 再对该结果取反。
#     实际判据因此变成“输出不含该错误行才通过”：正确打印 ERROR invalid port argument 并以
#     退出码 2 结束的服务端反而判 FAIL，而无任何输出的崩溃却判 PASS，与注释意图恰好相反。
# 因此改由包装脚本执行目标程序，自行校验退出码与输出，不再使用 WILL_FAIL / PASS_REGULAR_EXPRESSION。
# 为遵守本 Issue 的修改文件范围（不新增 cmake/ 目录），包装脚本在配置阶段由本文件生成到
# 构建目录；build/ 已被 .gitignore 排除，不污染源码树。脚本内容见下方 file(WRITE)。
set(INVALID_PORT_CHECK_SCRIPT "${CMAKE_CURRENT_BINARY_DIR}/check_invalid_port.cmake")
file(WRITE "${INVALID_PORT_CHECK_SCRIPT}" [==[
# 由 tcp_hello_demo/CMakeLists.txt 在配置阶段生成，请勿手工修改。
# 用法：cmake -DSERVER=<服务端可执行文件> -DARG=<非法端口参数> -P check_invalid_port.cmake
# 通过判据：服务端非零退出，且输出包含 "ERROR invalid port argument"（README §7 日志契约）。
if(NOT DEFINED SERVER)
    message(FATAL_ERROR "check_invalid_port: 缺少 -DSERVER=<服务端可执行文件>")
endif()
if(NOT DEFINED ARG)
    message(FATAL_ERROR "check_invalid_port: 缺少 -DARG=<非法端口参数>")
endif()

execute_process(
    COMMAND "${SERVER}" "${ARG}"
    RESULT_VARIABLE run_result
    OUTPUT_VARIABLE run_stdout
    ERROR_VARIABLE run_stderr
    TIMEOUT 30
)

# stdout 与 stderr 合并后再匹配，避免错误行输出通道变化导致误判。
set(run_output "${run_stdout}${run_stderr}")

if(run_result STREQUAL "Process terminated due to timeout")
    message(FATAL_ERROR "非法端口参数 ${ARG}：服务端 30 秒内未退出（挂起）")
endif()

if(run_result EQUAL 0)
    message(FATAL_ERROR "非法端口参数 ${ARG}：服务端以退出码 0 结束，期望非零")
endif()

if(NOT run_output MATCHES "ERROR invalid port argument")
    message(FATAL_ERROR
        "非法端口参数 ${ARG}：退出码 ${run_result}，但输出未包含预期的 'ERROR invalid port argument'；实际输出：${run_output}")
endif()

message(STATUS "非法端口参数 ${ARG} 被拒绝：退出码 ${run_result}，输出含 'ERROR invalid port argument'")
]==])

add_test(NAME tcp_hello_invalid_port_zero
         COMMAND ${CMAKE_COMMAND}
                 "-DSERVER=$<TARGET_FILE:tcp_hello_server>"
                 -DARG=0
                 -P "${INVALID_PORT_CHECK_SCRIPT}")
add_test(NAME tcp_hello_invalid_port_overflow
         COMMAND ${CMAKE_COMMAND}
                 "-DSERVER=$<TARGET_FILE:tcp_hello_server>"
                 -DARG=65536
                 -P "${INVALID_PORT_CHECK_SCRIPT}")
add_test(NAME tcp_hello_invalid_port_text
         COMMAND ${CMAKE_COMMAND}
                 "-DSERVER=$<TARGET_FILE:tcp_hello_server>"
                 -DARG=not-a-port
                 -P "${INVALID_PORT_CHECK_SCRIPT}")