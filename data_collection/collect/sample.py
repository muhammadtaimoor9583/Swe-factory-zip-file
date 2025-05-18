import os
import json
import argparse
import random
import math
from collections import defaultdict

def sample_instances(directory, ratio=0.25):
    repo_to_instances = defaultdict(list)

    # Step 1: Load all instances and group by repo + version
    for root, _, files in os.walk(directory):
        for fname in files:
            if fname == "instances_versions.jsonl":
                file_path = os.path.join(root, fname)
                with open(file_path, 'r', encoding='utf-8') as file:
                    for line in file:
                        data = json.loads(line)
                        repo = data.get('repo', 'unknown')
                        repo_to_instances[repo].append(data)

    print(f"🧮 Sampling ratio per repo: {ratio:.2%}")

    sampled = []
    for repo in sorted(repo_to_instances.keys()):
        instances = repo_to_instances[repo]
        versions = defaultdict(list)
        for inst in instances:
            versions[inst['version']].append(inst)

        total_quota = math.ceil(len(instances) * ratio)
        version_weights = {v: len(versions[v]) for v in versions}
        total_weight = sum(version_weights.values())

        version_quota = {
            v: math.ceil((version_weights[v] / total_weight) * total_quota)
            for v in versions
        }

        version_sample = []
        for v, count in version_quota.items():
            candidates = versions[v]
            if len(candidates) <= count:
                version_sample.extend(candidates)
            else:
                version_sample.extend(random.sample(candidates, count))

        print(f"✅ Repo {repo}: selected {len(version_sample)} of {len(instances)} instances from {len(versions)} versions")
        sampled.extend(version_sample)

    print(f"\n🎉 Total sampled instances: {len(sampled)}")

    # Step 3: Save to sampled_instance_ids.txt in the directory
    id_path = os.path.join(directory, "sampled_instance_ids.txt")
    with open(id_path, 'w', encoding='utf-8') as id_file:
        for inst in sampled:
            if 'instance_id' in inst:
                id_file.write(inst['instance_id'] + '\n')

    print(f"📝 Instance IDs saved to: {id_path}")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Sample repo instances with version coverage')
    parser.add_argument('--directory', required=True, help='Root directory containing instances_versions.jsonl files')
    parser.add_argument('--ratio', type=float, default=0.2, help='Sampling ratio per repo (e.g., 0.25 for 25%)')
    args = parser.parse_args()

    sample_instances(args.directory, args.ratio)
