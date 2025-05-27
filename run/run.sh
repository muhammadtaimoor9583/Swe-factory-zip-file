#!/bin/bash
set -euo pipefail

MODEL="google/gemini-2.5-flash-preview"
# "deepseek/deepseek-chat-v3-0324"
# "google/gemini-2.5-flash-preview"
# "gpt-4.1-mini"
REPO_NAME="SetupBench-lite"
BASE_TASK_DIR="data_collection/collect/${REPO_NAME}"
SETUP_MAP="${BASE_TASK_DIR}/setup_map.json"
TASKS_MAP="${BASE_TASK_DIR}/tasks_map.json"
SETUP_DIR="testbed"
ROUND=5
NUM_PROCS=20
TEMP=0.2
BATCH_COUNT=17

for f in "$SETUP_MAP" "$TASKS_MAP"; do
  if [ ! -f "$f" ]; then
    echo "❌ Missing file: $f"
    exit 1
  fi
done

cleanup() {
  docker ps -a -q | xargs -r docker rm -f || true
  docker image prune -af || true
  rm -rf "$SETUP_DIR"
}

for idx in $(seq 1 $BATCH_COUNT); do
  TASK_LIST_FILE="${BASE_TASK_DIR}/batch_${idx}.txt"
  if [ ! -f "$TASK_LIST_FILE" ]; then
    echo "⚠️  Skipping missing ${TASK_LIST_FILE}"
    continue
  fi

  cleanup

  OUT_DIR="output/${REPO_NAME}/${MODEL}/round_${ROUND}_batch_${idx}"
  RESULT_DIR="output/${REPO_NAME}/${MODEL}/results"
  mkdir -p "$OUT_DIR"

  echo "▶️  Running batch_${idx} with normal mode"

  python app/main.py swe-bench \
    --model "$MODEL" \
    --setup-map "$SETUP_MAP" \
    --tasks-map "$TASKS_MAP" \
    --task-list-file "$TASK_LIST_FILE" \
    --num-processes "$NUM_PROCS" \
    --model-temperature "$TEMP" \
    --conv-round-limit "$ROUND" \
    --output-dir "$OUT_DIR" \
    --setup-dir "$SETUP_DIR" \
    --results-path "$RESULT_DIR"
done
