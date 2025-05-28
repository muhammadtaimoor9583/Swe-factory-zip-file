import os
import re
import json
import argparse
import multiprocessing
import time
import shutil
from openai import OpenAI
from dotenv import load_dotenv
from tqdm import tqdm

# --- Configuration ---
load_dotenv()  # Load environment variables from .env file

PREV_FILE_NAME    = "test_output_prev_apply.txt"
AFTER_FILE_NAME   = "test_output_after_apply.txt"
STATUS_FILE_NAME  = "status.json"

MAX_LINES     = 2000
HEAD_LINES    = 1000
TAIL_LINES    = 1000

EXIT_CODE_RE  = re.compile(r"echo OMNIGRIL_EXIT_CODE=(\d)")

# process-local storage for OpenAI client
_process_local_client         = None
_process_local_api_key_used   = None
_process_local_base_url_used  = None

def truncate_content(content, subdir):
    lines = content.splitlines()
    if len(lines) <= MAX_LINES:
        return content
    omitted   = len(lines) - (HEAD_LINES + TAIL_LINES)
    head_part = lines[:HEAD_LINES]
    tail_part = lines[-TAIL_LINES:]
    notice    = f"... ({omitted} lines omitted) ..."
    print(f"[{subdir}][PID {os.getpid()}] Content truncated: {omitted} lines omitted.")
    return "\n".join(head_part + [notice] + tail_part)

def extract_exit_code(content):
    m = EXIT_CODE_RE.search(content)
    return int(m.group(1)) if m else None

def get_openai_client(api_key_for_init, base_url_for_init):
    global _process_local_client, _process_local_api_key_used, _process_local_base_url_used
    if (_process_local_client is None
        or _process_local_api_key_used != api_key_for_init
        or _process_local_base_url_used != base_url_for_init):
        _process_local_client = OpenAI(api_key=api_key_for_init, base_url=base_url_for_init)
        _process_local_client.models.list()  # validate credentials
        _process_local_api_key_used  = api_key_for_init
        _process_local_base_url_used = base_url_for_init
    return _process_local_client

def process_subdirectory(args_tuple):
    subdir, model_name, api_key, base_url = args_tuple
    status_path = os.path.join(subdir, STATUS_FILE_NAME)
    prev_path   = os.path.join(subdir, PREV_FILE_NAME)
    after_path  = os.path.join(subdir, AFTER_FILE_NAME)

    # if status.json exists with valid status, return it
    if os.path.isfile(status_path):
        try:
            data = json.load(open(status_path, encoding="utf-8"))
            if data.get("status") in ["success", "failure", "none"]:
                return data
        except Exception:
            pass

    # missing one of the outputs -> none
    if not (os.path.isfile(prev_path) and os.path.isfile(after_path)):
        data = {"status": "none", "tokens": 0}
        with open(status_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        return data

    prev  = open(prev_path,  encoding="utf-8", errors="ignore").read()
    after = open(after_path, encoding="utf-8", errors="ignore").read()

    prev_exit  = extract_exit_code(prev)
    after_exit = extract_exit_code(after)
    # use exit codes only
    if prev_exit is not None and after_exit is not None:
        result = (prev_exit == 1 and after_exit == 0)
        tokens = 0
    else:
        # fallback if needed:
        result, tokens = False, 0

    status = "success" if result else "failure"
    status_data = {"status": status, "tokens": tokens}
    with open(status_path, 'w', encoding='utf-8') as f:
        json.dump(status_data, f, indent=2)
    return status_data

def classify_and_batch_subdirectories(src_subdirs, dest_root, batch_size=10):
    """
    src_subdirs: list of full paths to each original subdir
    dest_root:   root directory under which to create categories and batches
    """
    cats = {"fail2pass": [], "fail2fail": [], "pass2pass": [], "pass2fail": [], "none": []}

    # classify
    for subdir in src_subdirs:
        prev = ""
        after = ""
        p = os.path.join(subdir, PREV_FILE_NAME)
        a = os.path.join(subdir, AFTER_FILE_NAME)
        if os.path.exists(p):  prev  = open(p, 'r', encoding='utf-8', errors='ignore').read()
        if os.path.exists(a):  after = open(a, 'r', encoding='utf-8', errors='ignore').read()
        pre_exit  = extract_exit_code(prev)
        aft_exit  = extract_exit_code(after)

        if pre_exit is None or aft_exit is None:
            cat = "none"
        elif pre_exit == 1 and aft_exit == 0:
            cat = "fail2pass"
        elif pre_exit == 1 and aft_exit == 1:
            cat = "fail2fail"
        elif pre_exit == 0 and aft_exit == 0:
            cat = "pass2pass"
        elif pre_exit == 0 and aft_exit == 1:
            cat = "pass2fail"
        else:
            cat = "none"

        cats[cat].append(subdir)

    # copy & batch
    for cat, dirs in cats.items():
        cat_dir = os.path.join(dest_root, cat)
        os.makedirs(cat_dir, exist_ok=True)
        num_batches = (len(dirs) + batch_size - 1) // batch_size
        for i in range(num_batches):
            batch_dir = os.path.join(cat_dir, f"batch_{i}")
            os.makedirs(batch_dir, exist_ok=True)
            chunk = dirs[i*batch_size:(i+1)*batch_size]
            for src in chunk:
                dst = os.path.join(batch_dir, os.path.basename(src))
                if not os.path.exists(dst):
                    shutil.copytree(src, dst)
    print(f"Sorted & batched under '{dest_root}'.")

def main():
    parser = argparse.ArgumentParser(
        description="Detect fail2pass and then classify & batch into a new directory."
    )
    parser.add_argument("target_folder",
        help="Top-level folder containing subdirs to scan.")
    parser.add_argument("sorted_folder",
        help="Path to create the sorted & batched copies.")
    parser.add_argument("--processes", type=int, default=20,
        help="Number of worker processes.")
    parser.add_argument("--batch-size", type=int, default=10,
        help="How many subdirs per batch.")
    args = parser.parse_args()

    if args.processes < 1:
        parser.error("--processes must be >= 1")
    if not os.path.isdir(args.target_folder):
        parser.error(f"Folder not found: {args.target_folder}")

    api_key = os.getenv("OPENAI_KEY") or parser.error("Environment var OPENAI_KEY required.")
    base_url = os.getenv("OPENAI_API_BASE_URL")

    subs = [ os.path.join(args.target_folder, d)
             for d in os.listdir(args.target_folder)
             if os.path.isdir(os.path.join(args.target_folder, d)) ]

    # run detection
    with multiprocessing.Pool(args.processes) as pool:
        list(tqdm(
            pool.imap(process_subdirectory,
                      [(s, None, api_key, base_url) for s in subs]),
            total=len(subs), desc="Processing"
        ))

    # now classify and batch into new directory
    classify_and_batch_subdirectories(subs, args.sorted_folder, batch_size=args.batch_size)

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
