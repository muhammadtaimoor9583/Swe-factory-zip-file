#!/bin/bash
set -uxo pipefail
cd /testbed
git checkout faa0a7a9c5a3550ed5461fab7d8e31c37fd1a2ef "tests/test_pattern_formatter.cpp"

# Apply test patch to update target tests
git apply -v - <<'EOF_114329324912'
[CONTENT OF TEST PATCH]
EOF_114329324912

# Create a build directory, navigate into it, configure CMake to build tests, and build the project
mkdir -p build
cd build
cmake .. -DSPDLOG_BUILD_TESTS=ON
cmake --build .

# Navigate to the directory where the spdlog-utests executable is located and run the specific test
cd /testbed/build/tests/
./spdlog-utests "[pattern_formatter]"
rc=$? # Required: Save the exit code immediately after running tests

echo "OMNIGRIL_EXIT_CODE=$rc" # Required: Echo the test status

# Return to /testbed for git checkout cleanup
cd /testbed
git checkout faa0a7a9c5a3550ed5461fab7d8e31c37fd1a2ef "tests/test_pattern_formatter.cpp"