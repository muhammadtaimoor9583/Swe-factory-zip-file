import requests
import json
import argparse
import os
import sys

def fetch_single_repo(repo_full_name: str, output_path: str, token: str):
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"token {token}"
    }

    url = f"https://api.github.com/repos/{repo_full_name}"

    print(f"📡 Fetching repository: {repo_full_name}")
    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        print(f"❌ Error: {response.status_code} - {response.json().get('message')}")
        sys.exit(1)

    repo = response.json()
    result = {
        "name": repo["full_name"],
        "stars": repo["stargazers_count"],
        "url": repo["html_url"],
        "description": repo["description"],
        "owner": repo["owner"]["login"],
        "language": repo["language"]
    }

    os.makedirs(output_path, exist_ok=True)
    output_file = os.path.join(output_path, f"{repo_full_name.replace('/', '_')}.json")
    print(f"💾 Saving repo info to {output_file}")
    with open(output_file, mode='w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print("✅ Done!")

def main():
    parser = argparse.ArgumentParser(description="Fetch a specific GitHub repo by owner/name")
    parser.add_argument("--repo", type=str, required=True, help="Full repo name (e.g., gabime/spdlog)")
    parser.add_argument("--output_path", type=str, required=True, help="Directory to save the result JSON")
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("❌ GitHub token not found. Please set the environment variable `GITHUB_TOKEN`.")
        sys.exit(1)

    fetch_single_repo(
        repo_full_name=args.repo,
        output_path=args.output_path,
        token=token
    )

if __name__ == "__main__":
    main()
