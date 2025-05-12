import os
import json
import argparse
import multiprocessing
import time
from openai import OpenAI
from dotenv import load_dotenv
from tqdm import tqdm

# --- Configuration ---
load_dotenv()  # Load environment variables from .env file

# Constants for file names
PREV_FILE_NAME = "test_output_prev_apply.txt"
AFTER_FILE_NAME = "test_output_after_apply.txt"
STATUS_FILE_NAME = "status.json"

# Truncation settings
MAX_LINES = 2000
HEAD_LINES = 1000
TAIL_LINES = 1000

# Process-local storage for OpenAI client
_process_local_client = None
_process_local_api_key_used = None
_process_local_base_url_used = None


def truncate_content(content, subdir):
    """
    Truncate content if it exceeds MAX_LINES. Keep HEAD_LINES and TAIL_LINES, insert omission notice.
    """
    lines = content.splitlines()
    if len(lines) <= MAX_LINES:
        return content
    omitted = len(lines) - (HEAD_LINES + TAIL_LINES)
    head_part = lines[:HEAD_LINES]
    tail_part = lines[-TAIL_LINES:]
    notice = f"... ({omitted} lines omitted) ..."
    print(f"[{subdir}][Process {os.getpid()}] Content truncated: {omitted} lines omitted.")
    return "\n".join(head_part + [notice] + tail_part)


def get_openai_client(api_key_for_init, base_url_for_init):
    """
    Initializes and returns an OpenAI client for the current process.
    """
    global _process_local_client, _process_local_api_key_used, _process_local_base_url_used
    if (_process_local_client is None or
            _process_local_api_key_used != api_key_for_init or
            _process_local_base_url_used != base_url_for_init):
        _process_local_client = OpenAI(api_key=api_key_for_init, base_url=base_url_for_init)
        _process_local_client.models.list()  # Validate credentials
        _process_local_api_key_used = api_key_for_init
        _process_local_base_url_used = base_url_for_init
    return _process_local_client


def check_fail2pass_with_llm(prev_content, after_content, model_name, api_key, base_url, subdir, max_retries=3):
    """
    Uses LLM to detect a true fail->pass case.
    Retries on API errors. Returns (True/False) for valid responses, or (None) on persistent errors.
    Also returns token usage count.
    """
    # Truncate long contents
    prev_trunc = truncate_content(prev_content, subdir)
    after_trunc = truncate_content(after_content, subdir)

    try:
        client = get_openai_client(api_key, base_url)
    except Exception as e:
        print(f"[{subdir}][Process {os.getpid()}] Client init error: {e}")
        return None, 0

    prompt = f"""
Analyze two test outputs. Respond with only 'true' if at least one test case failed in File 1 and passed in File 2; otherwise 'false'.

File 1 (before patch):
---
{prev_trunc}
---
File 2 (after patch):
---
{after_trunc}
---
"""
    total_tokens = 0
    for attempt in range(1, max_retries + 1):
        try:
            resp = client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": "You are a test analysis assistant. Respond strictly with true or false."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.0,
                max_tokens=5
            )
            # Extract usage tokens
            usage = getattr(resp, 'usage', None) or resp.get('usage', {})
            tokens = usage.total_tokens if hasattr(usage, 'total_tokens') else usage.get('total_tokens', 0)
            total_tokens += tokens
            text = resp.choices[0].message.content.strip().lower()
            if text == "true":
                return True, total_tokens
            if text == "false":
                return False, total_tokens
            print(f"[{subdir}][Process {os.getpid()}] Unexpected LLM response '{text}', retrying...")
        except Exception as e:
            print(f"[{subdir}][Process {os.getpid()}] API error on attempt {attempt}: {e}")
        time.sleep(2 ** attempt)
    print(f"[{subdir}][Process {os.getpid()}] Persistent API errors; will retry on next run.")
    return None, total_tokens


def process_subdirectory(args_tuple):
    """
    Process a subdirectory: determine status, record token usage, write status.json for definitive results.
    """
    subdir, model_name, api_key, base_url = args_tuple
    status_path = os.path.join(subdir, STATUS_FILE_NAME)
    prev_path = os.path.join(subdir, PREV_FILE_NAME)
    after_path = os.path.join(subdir, AFTER_FILE_NAME)

    # Skip if status.json already has a definitive status
    if os.path.isfile(status_path):
        try:
            data = json.load(open(status_path))
            if data.get("status") in ["success", "failure", "none"]:
                return data
        except Exception:
            pass

    if not (os.path.isfile(prev_path) and os.path.isfile(after_path)):
        data = {"status": "none", "tokens": 0}
        with open(status_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        return data

    try:
        prev = open(prev_path, encoding="utf-8", errors="ignore").read().strip()
        after = open(after_path, encoding="utf-8", errors="ignore").read().strip()
    except Exception as e:
        print(f"[{subdir}][Process {os.getpid()}] File read error: {e}")
        return None

    if prev and after:
        result, tokens = check_fail2pass_with_llm(prev, after, model_name, api_key, base_url, subdir)
        if result is None:
            return None
        status = "success" if result else "failure"
    else:
        status = "failure"
        tokens = 0

    status_data = {"status": status, "tokens": tokens}
    try:
        with open(status_path, 'w', encoding='utf-8') as f:
            json.dump(status_data, f, indent=2)
    except Exception as e:
        print(f"[{subdir}][Process {os.getpid()}] Write status error: {e}")
    return status_data


def main():
    parser = argparse.ArgumentParser(
        description="Detect fail2pass with token tracking and retry logic."
    )
    parser.add_argument("target_folder", help="Top-level folder to scan.")
    parser.add_argument("--model", default="gpt-3.5-turbo", help="OpenAI model name.")
    parser.add_argument("--processes", type=int, default=20, help="Worker processes count.")
    args = parser.parse_args()

    if args.processes < 1:
        parser.error("--processes must be >= 1")
    if not os.path.isdir(args.target_folder):
        parser.error(f"Folder not found: {args.target_folder}")

    api_key = os.getenv("OPENAI_KEY") or parser.error("Environment var OPENAI_KEY required.")
    base_url = os.getenv("OPENAI_API_BASE_URL")

    subs = [os.path.join(args.target_folder, d)
            for d in os.listdir(args.target_folder)
            if os.path.isdir(os.path.join(args.target_folder, d))]

    results = []
    with multiprocessing.Pool(args.processes) as pool:
        for res in tqdm(
                pool.imap(process_subdirectory, [(s, args.model, api_key, base_url) for s in subs]),
                total=len(subs), desc="Processing"
        ):
            if res is not None:
                results.append(res)

    total = len(subs)
    success = sum(1 for r in results if r["status"] == "success")
    failure = sum(1 for r in results if r["status"] == "failure")
    none = sum(1 for r in results if r["status"] == "none")
    skipped = total - len(results)
    total_tokens = sum(r.get("tokens", 0) for r in results)

    print("\n--- Summary ---")
    print(f"Targeted dirs: {total}")
    print(f"Success: {success}, Failure: {failure}, None: {none}, Skipped (retries): {skipped}")
    print(f"Total tokens consumed: {total_tokens}")

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
