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

# 非法端口参数的负面用例：不依赖网络即可验证 FR-1 的退出路径。
add_test(NAME tcp_hello_invalid_port_zero
         COMMAND $<TARGET_FILE:tcp_hello_server> 0)
add_test(NAME tcp_hello_invalid_port_overflow
         COMMAND $<TARGET_FILE:tcp_hello_server> 65536)
add_test(NAME tcp_hello_invalid_port_text
         COMMAND $<TARGET_FILE:tcp_hello_server> not-a-port)

# 通过判据必须同时满足两点：非零退出（WILL_FAIL）且输出中出现预期的端口参数校验错误行。
# 仅用 WILL_FAIL 时，进程崩溃或因运行库缺失导致的启动失败同样是非零退出，会被误判为通过；
# 追加 PASS_REGULAR_EXPRESSION 后，失败原因被收敛到 README §7 约定的日志契约。
set_tests_properties(
    tcp_hello_invalid_port_zero
    tcp_hello_invalid_port_overflow
    tcp_hello_invalid_port_text
    PROPERTIES
    WILL_FAIL TRUE
    PASS_REGULAR_EXPRESSION "ERROR invalid port argument"
)