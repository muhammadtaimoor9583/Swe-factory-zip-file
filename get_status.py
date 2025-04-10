import os
import json
import argparse

def count_finished_status_and_cost(directory):
    # 用于 status.json 的统计
    finished_count = 0  # 统计 is_finish=True 的数量
    total_status_files = 0  # 统计 status.json 文件总数

    # 用于 cost.json 的统计
    total_tokens_sum = 0  # 所有 total_tokens 的总和
    total_cost_files = 0  # 统计 cost.json 文件总数
    total_files = 0
    # 遍历目录及其子目录
    for root, _, files in os.walk(directory):
        for file in files:
            file_path = os.path.join(root, file)

            # 处理 status.json
            if file == "status.json":
                total_status_files += 1
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        # 确保是一个字典，并检查 is_finish 键
                        if isinstance(data, dict) and data.get("is_finish") is True:
                            finished_count += 1
                        else:
                            print(file_path)
                except (json.JSONDecodeError, IOError) as e:
                    print(f"Error reading {file_path}: {e}")

            # 处理 cost.json
            elif file == "cost.json":
                total_cost_files += 1
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        # 确保是一个字典，并尝试获取 total_tokens
                        if isinstance(data, dict) and "total_tokens" in data:
                            try:
                                total_tokens = float(data["total_tokens"])  # 转换为浮点数以支持小数
                                total_tokens_sum += total_tokens
                            except (TypeError, ValueError) as e:
                                print(f"Invalid 'total_tokens' in {file_path}: {e}")
                except (json.JSONDecodeError, IOError) as e:
                    print(f"Error reading {file_path}: {e}")
            elif file =="meta.json":
                total_files+=1

    # 输出 status.json 的统计结果
    print(f"Total 'status.json' files found: {total_status_files}")
    print(f"Files with 'is_finish = true': {finished_count}")
    print(f'total num: {total_files}')
    # 输出 cost.json 的统计结果
    print(f"Total 'cost.json' files found: {total_cost_files}")
    if total_cost_files > 0:
        avg_tokens = total_tokens_sum / total_cost_files
        print(f"Sum of 'total_tokens': {total_tokens_sum}")
        print(f"Average 'total_tokens' across cost.json files: {avg_tokens:.2f}")
    else:
        print("No 'cost.json' files found, cannot compute average 'total_tokens'.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Analyze 'status.json' and 'cost.json' files in a directory.")
    parser.add_argument("directory", type=str, help="Path to the target directory")

    args = parser.parse_args()
    count_finished_status_and_cost(args.directory)