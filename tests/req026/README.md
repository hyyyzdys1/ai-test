# REQ-026 · 默认构造 `std::vector<int>` 大小测试

本目录提供一个可独立运行的 C++ 测试目标：验证默认构造的 `std::vector<int>` 的 `size()` 返回 `0`。
构建与运行只依赖 C++ 编译器和标准库，不需要第三方测试框架、命令行参数或测试数据文件。

## 文件与作用

| 路径 | 作用 |
| --- | --- |
| `tests/req026/vector_size_test.hpp` | 测试类 `VectorSizeTest`，用例方法 `default_constructed_size_is_zero()` |
| `tests/req026/main.cpp` | 独立入口：创建测试类实例、调用一次目标用例并按返回值输出报告 |
| `tests/req026/README.md` | 本说明（工作目录、编译与运行步骤、期望值修改位置） |

## 工作目录

以下所有命令均在**仓库根目录**执行，即包含 `tests/req026/` 的目录（本文件所在目录的上两级）。
命令中的源码路径和输出路径均相对于仓库根目录。请勿先 `cd` 进入 `tests/req026`，否则 `-I tests/req026` 等相对路径将失效。

## 编译

### g++（Linux / macOS / MinGW / Git Bash）

```sh
g++ -std=c++17 -Wall -Wextra -pedantic -I tests/req026 tests/req026/main.cpp -o vector_size_test
```

### clang++（macOS / Linux）

```sh
clang++ -std=c++17 -Wall -Wextra -pedantic -I tests/req026 tests/req026/main.cpp -o vector_size_test
```

编译成功后，仓库根目录下生成可执行文件 `vector_size_test`（Windows 上使用 g++/MinGW 时实际文件为 `vector_size_test.exe`）。

## 运行

Linux / macOS / Git Bash：

```sh
./vector_size_test
```

Windows PowerShell（编译产物带 `.exe` 后缀）：

```powershell
.\vector_size_test.exe
```

## 预期报告（AC-2）

正常运行输出一行：

```text
default_constructed_size_is_zero PASS
```

该行包含固定用例标识 `default_constructed_size_is_zero`（与源码中的方法名一致）和结果 `PASS`。
本次运行恰好执行一条用例；仅出现启动信息或“执行零条用例”不满足验收。

进程退出码：用例通过时为 `0`，失败时为非零。**退出码契约仍在评审中（PRD 选项 A/B），本次验收以用例报告文本为准**，退出码仅作为辅助观察。

## 源码核对（AC-1）

1. `tests/req026/main.cpp` 的 `main()` 中创建 `VectorSizeTest` 实例，并调用一次
   `default_constructed_size_is_zero()`；`ReportResult()` 完全依据该次调用的返回值输出
   `PASS` 或 `FAIL`。
2. `tests/req026/vector_size_test.hpp` 的用例方法中：

   ```cpp
   std::vector<int> values;              // 默认构造，读取大小前不写入元素
   const auto actual = values.size();    // 直接调用 size() 取得实际返回值
   const std::size_t expected = 0;       // 期望值，初始为 0
   return actual == expected;            // 显式相等比较，定义 NDEBUG 时仍会执行
   ```

## 失败与恢复验证（AC-3）

保持容器构造、取值、比较和报告逻辑不变，只修改期望值：

1. 打开 `tests/req026/vector_size_test.hpp`，将用例方法中的
   `const std::size_t expected = 0;` 改为 `const std::size_t expected = 1;`（仅此一处）。
2. 在仓库根目录使用上面的编译命令重新编译。
3. 运行可执行文件，报告应为：

   ```text
   default_constructed_size_is_zero FAIL
   ```

   此时进程退出码为非零（辅助观察）。
4. 将期望值恢复为 `0`，即 `const std::size_t expected = 0;`。
5. 再次重新编译并运行，报告应恢复为：

   ```text
   default_constructed_size_is_zero PASS
   ```

6. 确认交付源码中该行仍为 `0`，未遗留临时修改。

## 清理

如需清理构建产物，在仓库根目录删除 `vector_size_test`（Windows 为 `vector_size_test.exe`）。