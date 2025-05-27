#!/bin/bash

# Clean up unused Docker images to free space (optional)

# Define variables
MODEL="google/gemini-2.5-flash-preview"
# "gpt-4.1-mini"
# "google/gemini-2.5-flash-preview"
# "gpt-4.1-mini"
# "google/gemini-2.5-flash-preview"
# "gpt-4.1-mini"
# "gpt-4o-mini-2024-07-18"
# "deepseek/deepseek-chat-v3-0324"
# "google/gemini-2.5-flash-preview"
# 


# "google/gemini-2.5-flash-preview"
# 
ROUND=5
REPEAT_INDEX=1
echo $OPENAI_API_BASE_URL
# REPO_NAME="keras-team-keras"
REPO_NAME="SetupBench-lite"
# "mocha"
# "undici"
# "vert"
# "opensearch"
# "checkstyle-checkstyle"
# "python-pillow-pillow"
# "python-attrs-attrs"
# "scipy-scipy"
# "psf-black"
# "plotly-plotly.py"
# "assertj-assertj"
# "date-fns-date-fns"
# "markedjs-marked"
# "netty-netty"
# "h2database-h2database"
# "junit-team-junit5"
# 
#
# "markerikson-redux-starter-kit"
# REPO_NAME="pallets-click"
# REPO_NAME="apollographql-apollo-client"
OUTPUT_DIR="output/${MODEL}_disable_context_retrieval/${REPO_NAME}"
BASE_TASK_DIR="data_collection/collect/$REPO_NAME"
SETUP_MAP="$BASE_TASK_DIR/setup_map.json"
TASKS_MAP="$BASE_TASK_DIR/tasks_map.json"
SETUP_DIR="testbed"

# Function to check if a file exists
check_file() {
    if [ ! -f "$1" ]; then
        echo "Error: File $1 does not exist"
        exit 1
    fi
}

# Verify static files exist
check_file "$SETUP_MAP"
check_file "$TASKS_MAP"

# Ensure output directory exists
mkdir -p "$OUTPUT_DIR"

# Loop over idx from 1 to a specified number (e.g., 10, adjust as needed)
for idx in {1..1}; do
    if [ "$(docker ps -a -q)" ]; then
        docker rm -f $(docker ps -a -q)
    else
        echo "No containers to remove."
    fi
    docker image prune -f
    

    if [ "$(docker images -a -q)" ]; then
        echo "Removing all Docker images..."
        docker image rm -f $(docker images -a -q)
        echo "All Docker images removed."
    else
        echo "No Docker images to remove."
    fi
    rm -rf testbed
    # Generate file_name like batch_mode_1, batch_mode_2, etc.
    file_name="batch_$idx"
    # file_name="version_mode"
    TASK_LIST_FILE="$BASE_TASK_DIR/$file_name.txt"

    # Check if the task list file exists
    if [ -f "$TASK_LIST_FILE" ]; then
        echo "Processing $file_name..."

        # Run the Python command with the modified task-list-file and output-dir
        python app/main.py swe-bench \
            --model "$MODEL" \
            --setup-map "$SETUP_MAP" \
            --tasks-map "$TASKS_MAP" \
            --task-list-file "$TASK_LIST_FILE" \
            --num-processes 1 \
            --model-temperature 0.1 \
            --conv-round-limit "$ROUND" \
            --output-dir "$OUTPUT_DIR/round_${ROUND}_$file_name" \
            --setup-dir "$SETUP_DIR" \
            --results-path "$OUTPUT_DIR/results" \
            --disable-context-retrieval
            # --organize-output-only
    else
        echo "Warning: $TASK_LIST_FILE does not exist, skipping $file_name"
    fi
done


