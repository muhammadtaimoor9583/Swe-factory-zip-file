import http.client
import json

# 确保 API 地址正确
API_HOST = "api.agicto.cn"
API_ENDPOINT = "/v1/ai/search"

# 替换为你的 API key
API_KEY = "your_api_key_here"

def search_query(query, max_results=10, search_service="google"):
    conn = http.client.HTTPSConnection(API_HOST)
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "max_results": max_results,
        "query": query,
        "search_service": search_service
    }

    try:
        conn.request("POST", API_ENDPOINT, json.dumps(data), headers)
        response = conn.getresponse()

        if response.status == 200:
            response_data = response.read().decode()
            result = json.loads(response_data)
            return result['results']
        else:
            print("Error response:", response.read().decode())
            return None

    except Exception as e:
        print("Request failed:", str(e))
        return None
    finally:
        conn.close()

# 示例调用
if __name__ == "__main__":
    query = "软件工程郭亮宏"
    results = search_query(query)
    if results:
        for result in results:
            print(result)
