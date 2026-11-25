#!/bin/bash
set -euo pipefail
cd /app 2>/dev/null || cd /testbed
git rev-parse HEAD > /logs/verifier/base.txt
if [ -s /candidate/patch ]; then
  if ! git apply --check /candidate/patch || ! git apply /candidate/patch; then
    printf 'invalid_patch\n' > /logs/verifier/status.txt
    exit 42
  fi
fi
exec /bin/bash /tests/test.sh
