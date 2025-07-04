#!/bin/bash
set -uxo pipefail

# Navigate to the project root directory
cd /testbed

# Ensure the target test file is at the specified commit SHA before applying any patch
git checkout 7e022c430053f71c3db80bf0eb3432392932f7e3 "tests/test_registry.cpp"

cd build
./tests/spdlog-utests
rc=$? # Required: Save the exit code immediately after running tests

echo "OMNIGRIL_EXIT_CODE=$rc" # Required: Echo the test status

# Return to /testbed for git checkout cleanup
cd /testbed
# Clean up: Revert changes to the test file to the original state
git checkout 7e022c430053f71c3db80bf0eb3432392932f7e3 "tests/test_registry.cpp"