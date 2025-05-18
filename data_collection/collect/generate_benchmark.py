import os
import json
import argparse
import random
from collections import defaultdict

def load_all_instances(input_directory):
    all_instances = []
    for root, _, files in os.walk(input_directory):
        for fname in files:
            if fname == "instances_versions.jsonl":
                path = os.path.join(root, fname)
                with open(path, 'r', encoding='utf-8') as f:
                    for line in f:
                        try:
                            all_instances.append(json.loads(line))
                        except Exception as e:
                            print(f"❌ Failed to parse line in {path}: {e}")
    return all_instances

def load_sample_ids(path):
    with open(path, 'r', encoding='utf-8') as f:
        return set(line.strip() for line in f if line.strip())

def process_instances(data_list):
    tasks_map = {}
    setup_map = {}

    for entry in data_list:
        instance_id = entry.get("instance_id")
        if not instance_id:
            continue
        
        tasks_map[instance_id] = entry.copy()
        tasks_map[instance_id]["image_urls"] = ""
        tasks_map[instance_id]["PASS_TO_PASS"] = ""
        tasks_map[instance_id]["FAIL_TO_PASS"] = ""

        repo_full = entry.get("repo", "")
        version = entry.get("version", "99.99")
        env_name = f"setup_{repo_full}__{version}"

        setup_map[instance_id] = {
            "repo_path": "",
            "env_name": env_name,
            "test_cmd": "",
            "install": "",
            "pre_install": ""
        }
    
    return tasks_map, setup_map

def save_json(obj, path):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, indent=4, ensure_ascii=False)

def save_merged_jsonl(instances, path):
    with open(path, 'w', encoding='utf-8') as f:
        for inst in instances:
            f.write(json.dumps(inst, ensure_ascii=False) + '\n')

def save_batches_by_repo_mixed(instances, batch_size, output_dir):
    os.makedirs(output_dir, exist_ok=True)

    # 按 repo 分组
    repo_to_instances = defaultdict(list)
    for inst in instances:
        repo = inst.get("repo", "unknown_repo")
        repo_to_instances[repo].append(inst)

    # 打乱 repo 顺序和每个 repo 内部顺序
    repo_items = list(repo_to_instances.items())
    random.shuffle(repo_items)
    for _, inst_list in repo_items:
        random.shuffle(inst_list)

    # 聚合生成 batch
    current_batch = []
    batch_id = 1
    for _, inst_list in repo_items:
        for inst in inst_list:
            current_batch.append(inst["instance_id"])
            if len(current_batch) == batch_size:
                path = os.path.join(output_dir, f"batch_{batch_id}.txt")
                with open(path, 'w', encoding='utf-8') as f:
                    f.writelines(f"{x}\n" for x in current_batch)
                print(f"✅ Saved batch {batch_id} with {len(current_batch)} instance_ids to {path}")
                batch_id += 1
                current_batch = []

    # 写入最后不足 batch 的
    if current_batch:
        path = os.path.join(output_dir, f"batch_{batch_id}.txt")
        with open(path, 'w', encoding='utf-8') as f:
            f.writelines(f"{x}\n" for x in current_batch)
        print(f"✅ Saved final batch {batch_id} with {len(current_batch)} instance_ids to {path}")

def main(input_dir, output_dir, batch_size, sample_ids_txt=None):
    print("📂 Loading all instances...")
    all_instances = load_all_instances(input_dir)

    if sample_ids_txt:
        sample_ids = load_sample_ids(sample_ids_txt)
        filtered_instances = [inst for inst in all_instances if inst.get("instance_id") in sample_ids]
        print(f"🔍 Filtered {len(filtered_instances)} instances based on provided sample_ids.txt")
    else:
        filtered_instances = all_instances
        print(f"📊 Using all {len(filtered_instances)} instances without filtering.")

    tasks_map, setup_map = process_instances(filtered_instances)

    os.makedirs(output_dir, exist_ok=True)
    save_json(tasks_map, os.path.join(output_dir, "tasks_map.json"))
    save_json(setup_map, os.path.join(output_dir, "setup_map.json"))

    merged_jsonl_path = os.path.join(output_dir, "merged_instances_versions.jsonl")
    save_merged_jsonl(filtered_instances, merged_jsonl_path)
    print(f"📄 Merged JSONL saved to: {merged_jsonl_path}")

    print(f"📁 Output saved to: {output_dir}")

    if batch_size > 0:
        save_batches_by_repo_mixed(filtered_instances, batch_size, output_dir)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Merge and convert instances_versions.jsonl files.")
    parser.add_argument("--input_dir", required=True, help="Directory to recursively search for instances_versions.jsonl")
    parser.add_argument("--output_dir", required=True, help="Output directory for tasks_map.json and setup_map.json")
    parser.add_argument("--batch", type=int, default=0, help="Batch size for sampling instance_id files")
    parser.add_argument("--sample_ids", type=str, default=None, help="Optional txt file containing instance_ids to include")

    args = parser.parse_args()

    main(
        input_dir=args.input_dir,
        output_dir=args.output_dir,
        batch_size=args.batch,
        sample_ids_txt=args.sample_ids
    )
