#!/bin/bash
set -uxo pipefail
cd /testbed
git checkout faa0a7a9c5a3550ed5461fab7d8e31c37fd1a2ef "tests/test_pattern_formatter.cpp"

# Apply test patch to update target tests
git apply -v - <<'EOF_114329324912'
diff --git a/tests/test_pattern_formatter.cpp b/tests/test_pattern_formatter.cpp
--- a/tests/test_pattern_formatter.cpp
+++ b/tests/test_pattern_formatter.cpp
@@ -1,6 +1,8 @@
 #include "includes.h"
 #include "test_sink.h"
 
+#include <chrono>
+
 using spdlog::memory_buf_t;
 using spdlog::details::to_string_view;
 
@@ -19,6 +21,21 @@ static std::string log_to_str(const std::string &msg, const Args &...args) {
     return oss.str();
 }
 
+// log to str and return it with time
+template <typename... Args>
+static std::string log_to_str_with_time(spdlog::log_clock::time_point log_time, const std::string &msg, const Args &...args) {
+    std::ostringstream oss;
+    auto oss_sink = std::make_shared<spdlog::sinks::ostream_sink_mt>(oss);
+    spdlog::logger oss_logger("pattern_tester", oss_sink);
+    oss_logger.set_level(spdlog::level::info);
+
+    oss_logger.set_formatter(
+        std::unique_ptr<spdlog::formatter>(new spdlog::pattern_formatter(args...)));
+
+    oss_logger.log(log_time, {}, spdlog::level::info, msg);
+    return oss.str();
+}
+
 TEST_CASE("custom eol", "[pattern_formatter]") {
     std::string msg = "Hello custom eol test";
     std::string eol = ";)";
@@ -58,6 +75,15 @@ TEST_CASE("date MM/DD/YY ", "[pattern_formatter]") {
             oss.str());
 }
 
+TEST_CASE("GMT offset ", "[pattern_formatter]") {
+    using namespace std::chrono_literals;
+    const auto now = std::chrono::system_clock::now();
+    const auto yesterday = now - 24h;
+
+    REQUIRE(log_to_str_with_time(yesterday, "Some message", "%z", spdlog::pattern_time_type::utc, "\n") ==
+            "+00:00\n");
+}
+
 TEST_CASE("color range test1", "[pattern_formatter]") {
     auto formatter = std::make_shared<spdlog::pattern_formatter>(
         "%^%v%$", spdlog::pattern_time_type::local, "\n");
EOF_114329324912

# Create a build directory, navigate into it, configure CMake to build tests, and build the project
mkdir -p build
cd build
cmake .. -DSPDLOG_BUILD_TESTS=ON
cmake --build .

# Execute only the specified target test files using CTest, filtering by test name.
# tests/test_pattern_formatter.cpp contains Catch2 TEST_CASEs often derived from the file name,
# so `pattern_formatter` is used as a regex filter.
ctest -R "pattern_formatter" --output-on-failure
rc=$? # Required: Save the exit code immediately after running tests

echo "OMNIGRIL_EXIT_CODE=$rc" # Required: Echo the test status

# Clean up / revert changes to the target test file
cd /testbed
git checkout faa0a7a9c5a3550ed5461fab7d8e31c37fd1a2ef "tests/test_pattern_formatter.cpp"