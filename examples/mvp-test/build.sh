#!/bin/bash
# Build the MVP demo test image into the host's (rootless) Docker daemon so the
# worker can launch it. Run this on the worker host.
#
#   ./examples/mvp-test/build.sh
#
# Then submit a test from the dashboard with framework_image = mvp-test:latest
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
docker build -t mvp-test:latest "$SCRIPT_DIR"
echo "Built mvp-test:latest"
