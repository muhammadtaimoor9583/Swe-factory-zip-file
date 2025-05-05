import os
import json
import argparse
from collections import Counter, defaultdict

def analyze_data(directory):
    repo_version_distribution = defaultdict(set)
    repo_year_distribution = defaultdict(set)
    repo_instance_count = defaultdict(int)
    total_instance_count = 0
    total_unique_versions = set()

    # 遍历路径下所有 instances_versions.jsonl 文件
    for root, _, files in os.walk(directory):
        for fname in files:
            if fname == "instances_versions.jsonl":
                file_path = os.path.join(root, fname)
                file_instance_count = 0
                with open(file_path, 'r', encoding='utf-8') as file:
                    for line in file:
                        data = json.loads(line)
                        file_instance_count += 1

                        repo = data.get('repo', 'unknown')
                        version = data.get('version', 'unknown')
                        created_at = data.get('created_at', '')[:4]  # Extract year

                        repo_version_distribution[repo].add(version)
                        total_unique_versions.add(version)
                        if created_at:
                            repo_year_distribution[repo].add(created_at)
                        repo_instance_count[repo] += 1

                print(f"📄 Collected {file_instance_count} records from {file_path}")
                total_instance_count += file_instance_count

    # Display Results
    print("\n====== Per Repository Stats ======")
    for repo in repo_version_distribution:
        num_versions = len(repo_version_distribution[repo])
        num_years = len(repo_year_distribution[repo])
        num_instances = repo_instance_count[repo]

        print(f"Repository: {repo}")
        print(f"  📌 Total instances: {num_instances}")
        print(f"  ✅ Total unique versions: {num_versions}")
        print(f"  📅 Covered years: {num_years} ({', '.join(sorted(repo_year_distribution[repo]))})\n")

    print("====== Summary ======")
    print(f"📦 Total repositories analyzed: {len(repo_version_distribution)}")
    print(f"🧾 Total instances collected: {total_instance_count}")
    print(f"🔢 Total unique versions across all repos: {len(total_unique_versions)}")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Analyze repo version and year coverage.')
    parser.add_argument('--directory', required=True, help='Root directory to search for instances_versions.jsonl')
    args = parser.parse_args()

    analyze_data(args.directory)
