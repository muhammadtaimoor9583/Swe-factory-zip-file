#!/bin/bash
set -uxo pipefail

# Navigate to the project root directory
cd /testbed

# Ensure the target test file is at the specified commit SHA before applying any patch
git checkout 7e022c430053f71c3db80bf0eb3432392932f7e3 "tests/test_registry.cpp"

# Apply test patch (if any exists)
# The content of the patch will be programmatically inserted here.
git apply -v - <<'EOF_114329324912'
diff --git a/tests/test_registry.cpp b/tests/test_registry.cpp
--- a/tests/test_registry.cpp
+++ b/tests/test_registry.cpp
@@ -25,6 +25,18 @@ TEST_CASE("explicit register", "[registry]") {
 }
 #endif
 
+TEST_CASE("register_or_replace", "[registry]") {
+    spdlog::drop_all();
+    auto logger1 = std::make_shared<spdlog::logger>(tested_logger_name,
+                                                   std::make_shared<spdlog::sinks::null_sink_st>());
+    spdlog::register_logger(logger1);
+    REQUIRE(spdlog::get(tested_logger_name) == logger1);
+
+    auto logger2 = std::make_shared<spdlog::logger>(tested_logger_name, std::make_shared<spdlog::sinks::null_sink_st>());
+    spdlog::register_or_replace(logger2);
+    REQUIRE(spdlog::get(tested_logger_name) == logger2);
+}
+
 TEST_CASE("apply_all", "[registry]") {
     spdlog::drop_all();
     auto logger = std::make_shared<spdlog::logger>(tested_logger_name,
EOF_114329324912

# Navigate to the build directory where the test executable is located.
# As per the collected information, all tests in tests/test_registry.cpp are tagged with '[registry]'.
# The previous attempt failed because the tag '[test_registry]' was incorrect.
# Correcting the Catch2 filter to '[registry]' to match the actual tags.
cd build
./tests/spdlog-utests "[registry]"
rc=$? # Required: Save the exit code immediately after running tests

echo "OMNIGRIL_EXIT_CODE=$rc" # Required: Echo the test status

# Return to /testbed for git checkout cleanup
cd /testbed
# Clean up: Revert changes to the test file to the original state
git checkout 7e022c430053f71c3db80bf0eb3432392932f7e3 "tests/test_registry.cpp"