// Developer: hyy
#ifndef TESTS_REQ026_VECTOR_SIZE_TEST_HPP
#define TESTS_REQ026_VECTOR_SIZE_TEST_HPP

#include <cstddef>
#include <vector>

// 验证默认构造的 std::vector<int> 大小为 0。
class VectorSizeTest {
 public:
  // 返回本次断言的比较结果，供独立入口生成报告。
  // 这里使用普通的相等比较而不是 assert，因此定义 NDEBUG 时检查依然会执行。
  bool default_constructed_size_is_zero() const {
    std::vector<int> values;
    const auto actual = values.size();
    const std::size_t expected = 0;
    return actual == expected;
  }
};

#endif  // TESTS_REQ026_VECTOR_SIZE_TEST_HPP