#!/bin/bash
set -euo pipefail
export HOME=/tmp/harness-home
export OPENAI_API_KEY=relay-only
export OPENAI_API_BASE=http://127.0.0.1:18981/v1
export OPENAI_BASE_URL="$OPENAI_API_BASE"
export PI_CODING_AGENT_DIR="$HOME/pi"
export OPENCODE_CONFIG=/run-config/opencode.json
export OPENCODE_DISABLE_AUTOUPDATE=true
export OPENCODE_DISABLE_LSP_DOWNLOAD=true
export OPENCODE_DISABLE_DEFAULT_PLUGINS=true
export OPENCODE_DISABLE_MODELS_FETCH=true
export LITELLM_LOCAL_MODEL_COST_MAP=true
export MSWEA_GLOBAL_CONFIG_DIR="$HOME/mini"
export MSWEA_SILENT_STARTUP=1
export MSWEA_CONFIGURED=1
mkdir -p "$PI_CODING_AGENT_DIR" "$MSWEA_GLOBAL_CONFIG_DIR" /job
: > "$MSWEA_GLOBAL_CONFIG_DIR/.env"
cp /run-config/models.json "$PI_CODING_AGENT_DIR/models.json"
node /run-config/loopback.mjs > /job/relay.log 2>&1 &
for i in {1..50}; do
  if grep -qx 'relay ready' /job/relay.log; then break; fi
  sleep 0.1
done
grep -qx 'relay ready' /job/relay.log
cd /app 2>/dev/null || cd /testbed
base=$(git rev-parse HEAD)
printf '%s\n' "$base" > /job/base.txt
prompt=$(cat /run-config/instruction.md)
case "$1" in
  pi) args=(pi --print --mode json --no-session --no-extensions --no-skills --no-prompt-templates --provider ht --model "$MODEL_ID" --thinking off "$prompt");;
  opencode) args=(/opt/harness/node_modules/opencode-linux-x64/bin/opencode run --format json --model "ht/$MODEL_ID" -- "$prompt");;
  mini-swe-agent) args=(mini-swe-agent --yolo --exit-immediately --model "openai/$MODEL_ID" --task "$prompt" --output /job/trajectory.json --config /run-config/mini.yaml);;
  *) exit 2;;
esac
set +e
timeout -k 10 "$2" "${args[@]}" </dev/null
rc=$?
set -e
printf '%s\n' "$rc" > /job/agent-exit.txt
git -c core.hooksPath=/dev/null add -A
git --no-pager diff --no-ext-diff --no-textconv --binary --cached "$base" > /job/model.patch
