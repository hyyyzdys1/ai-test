// 开发者：hyy
//
// REQ-026 · 独立运行入口（ISS-002）
//
// 编译（在仓库根目录执行，编译器与标准库即可）：
//   g++ -std=c++17 -Wall -Wextra -pedantic -I tests/req026 \
//       tests/req026/main.cpp -o vector_size_test
// 运行：
//   ./vector_size_test
//
// 入口无需命令行参数、数据文件或第三方测试框架。从 main 可直接定位到
// 一次对 VectorSizeTest::default_constructed_size_is_zero() 的调用，
// 报告逻辑使用该次调用的返回值选择输出，并以方法名作为固定用例标识：
//   返回 true  -> "default_constructed_size_is_zero PASS"
//   返回 false -> "default_constructed_size_is_zero FAIL"

#include <iostream>

#include "vector_size_test.hpp"

namespace {

// 固定报告标识：与测试类中的用例方法名保持一致，便于将报告关联回源码。
constexpr const char* kCaseId = "default_constructed_size_is_zero";

// 按用例的实际比较结果选择输出；全部用例通过时退出码为 0，否则为非零。
int ReportResult(const char* case_id, bool passed) {
    std::cout << case_id << (passed ? " PASS" : " FAIL") << std::endl;
    return passed ? 0 : 1;
}

}  // namespace

int main() {
    // AC-1：实例化测试类，并仅对目标方法发起一次调用。
    VectorSizeTest test;
    const bool passed = test.default_constructed_size_is_zero();

    // AC-2：报告完全由本次调用的返回值决定。
    return ReportResult(kCaseId, passed);
}