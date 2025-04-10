import http.client
import requests
import os
import inspect
from typing import Any
import re
from loguru import logger
from collections.abc import Callable, Iterable
from app.data_structures import MessageThread
from app.model import common
from app.post_process import ExtractStatus, is_valid_json
import json
# 确保 API 地址正确
API_HOST = "api.agicto.cn"
API_ENDPOINT = "/v1/ai/search"

# 替换为你的 API key

SYSTEM_PROMPT = """You are a web search agent. Your task is to assist other agents by retrieving relevant information from the internet. Given a question or requirement, you should generate a suitable search query, retrieve URLs, or extract relevant content from a webpage.
"""
QUERY_GENERATION_PROMPT="""Generate a concise web search query based on the given requirement. The query should be optimized to find relevant **sources or documents**, not direct answers.

Output the result as JSON.

### Example Output:
{
  "query": <Content>
}
"""

WEBPAGE_CHOICE_PROMPT="""From the given list of search results, choose the single webpage that appears most relevant to the original requirement.
Important Notes:
- Google results often do not contain answers directly, but link to useful pages.
- Focus on link relevance based on the **title and snippet**.

### **Example Format:**
{
  "chosen_links": [
    "https://example.com/abc",
    "https://another.com/xyz"
  ]
}
"""

WEBPAGE_SYSTEM_PROMPT="""You are a webpage browsing agent. Your task is to read the content of a provided webpage and extract only the information that is most relevant to a given requirement.
"""

WEBPAGE_BROWSE_PROMPT = """
Read the webpage content and extract only the parts that are directly relevant to the requirement.

If nothing relevant is found, return an empty string. Output the result as JSON.
### **Example Format:**
{
  "relevant_information": <Content>
}
"""
def format_search_results(results):
    formatted_str = "Results from google search are shown as follows:\n"
    for i, result in enumerate(results, start=1):
        formatted_str += f"[Result {i}]\n"
        formatted_str += f"Title: {result['title']}\n"
        formatted_str += f"Link: {result['link']}\n"
        formatted_str += f"Snippet: {result['snippet']}\n\n"
    return formatted_str

def search_query(query, max_results=10, search_service="google"):
    API_KEY = os.environ.get("WEB_SEARCH_KEY")
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
            return format_search_results(result['results'])
        else:
            print("Error response:", response.read().decode())
            return None

    except Exception as e:
        print("Request failed:", str(e))
        return None
    finally:
        conn.close()


def get_jinaai_content(url: str, timeout: int = 60) -> str:
    """Fetch and return the parsed content of a web page using Jina Reader API.

    Args:
        url: The URL of the web page to fetch.
        timeout: Maximum time in seconds to wait for the response (default: 60).

    Returns:
        The parsed content of the web page as a string.

    Raises:
        ValueError: If the URL is invalid or the request fails.
        TimeoutError: If the request times out.
    """
    if not url.startswith(('http://', 'https://')):
        raise ValueError("Invalid URL - must start with http:// or https://")

    jina_reader_url = f"https://r.jina.ai/{url}"

    try:
        response = requests.get(jina_reader_url, timeout=timeout)
        response.raise_for_status()
        return f'Content of {url}:\n\n{response.text}'

    except requests.exceptions.Timeout:
        raise TimeoutError(f"Request timed out after {timeout} seconds")
    except requests.exceptions.RequestException as e:
        raise ValueError(f"Failed to fetch web content: {str(e)}")

def run(msg_thread: MessageThread) -> tuple[str, MessageThread]:
    """
    Run the agent to extract issue to json format.
    """

    # msg_thread = MessageThread()
    # msg_thread.add_system(PROXY_PROMPT)
    # msg_thread.add_user(ANALYZE_PROMPT)
    res_text, *_ = common.SELECTED_MODEL.call(
        msg_thread.to_msg(), response_format="json_object"
    )

    msg_thread.add_model(res_text, [])  # no tools

    return res_text


def run_query_generation( msg_thread: MessageThread, retries=3,print_callback: Callable[[dict], None] | None = None) -> tuple[str | None, list[MessageThread]]:
    
    for idx in range(1, retries + 1):
        logger.debug(
            "Trying to generate query for google search. Try {} of {}.", idx, retries
        )
        msg_thread.add_user(QUERY_GENERATION_PROMPT)
        res_text = run(msg_thread)
        res_text = extract_json_from_response(res_text)
        # res_text = msg_threads.append(new_thread)
        res_text = res_text.lstrip('```json').rstrip('```')
        logger.debug(res_text)
        extract_status, data = is_valid_json(res_text)

        if extract_status != ExtractStatus.IS_VALID_JSON:
            logger.debug("Invalid json. Will retry.")
            continue

        if not isinstance(data, dict):
            valid = False
            diagnosis =  "Json is not a dict"
        elif not data.get("query"):
            valid = False
            diagnosis =  "'query' parameter is missing"
        else:
            valid = True
            diagnosis = 'OK'
        # valid, diagnosis = is_valid_response(data)
        if not valid:
            logger.debug(f"{diagnosis}. Will retry.")
            continue

        logger.debug("Extracted a valid json")
        return data['query']
    return None
   
def run_choose_webpage( msg_thread: MessageThread, retries=3,print_callback: Callable[[dict], None] | None = None) -> tuple[str | None, list[MessageThread]]:
    
    for idx in range(1, retries + 1):
        logger.debug(
            "Trying to select urls from google search results. Try {} of {}.", idx, retries
        )
        msg_thread.add_user(WEBPAGE_CHOICE_PROMPT)
        res_text = run(msg_thread)
        res_text = extract_json_from_response(res_text)
        # res_text = msg_threads.append(new_thread)
        res_text = res_text.lstrip('```json').rstrip('```')
        logger.debug(res_text)
        extract_status, data = is_valid_json(res_text)

        if extract_status != ExtractStatus.IS_VALID_JSON:
            logger.debug("Invalid json. Will retry.")
            continue

        if not isinstance(data, dict):
            valid = False
            diagnosis =  "Json is not a dict"
        elif not data.get("chosen_links"):
            valid = False
            diagnosis =  "'chosen_links' parameter is missing"
        elif not isinstance(data.get("chosen_links"), list):
            valid = False
            diagnosis =  "'chosen_links' is not a list"
        else:
            valid = True
            diagnosis = 'OK'
        # valid, diagnosis = is_valid_response(data)
        if not valid:
            logger.debug(f"{diagnosis}. Will retry.")
            continue

        logger.debug("Extracted a valid json")
        return data['chosen_links']
    return None

def run_browse_webpage( msg_thread: MessageThread, retries=3,print_callback: Callable[[dict], None] | None = None) -> tuple[str | None, list[MessageThread]]:
    
    for idx in range(1, retries + 1):
        logger.debug(
            "Trying to select urls from google search results. Try {} of {}.", idx, retries
        )
        msg_thread.add_user(WEBPAGE_BROWSE_PROMPT)
        res_text = run(msg_thread)
        res_text = extract_json_from_response(res_text)
        # res_text = msg_threads.append(new_thread)
        res_text = res_text.lstrip('```json').rstrip('```')
        logger.debug(res_text)
        extract_status, data = is_valid_json(res_text)

        if extract_status != ExtractStatus.IS_VALID_JSON:
            logger.debug("Invalid json. Will retry.")
            continue

        if not isinstance(data, dict):
            valid = False
            diagnosis =  "Json is not a dict"
        elif not data.get("relevant_information"):
            valid = False
            diagnosis =  "'relevant_information' parameter is missing"
        else:
            valid = True
            diagnosis = 'OK'
        # valid, diagnosis = is_valid_response(data)
        if not valid:
            logger.debug(f"{diagnosis}. Will retry.")
            continue

        logger.debug("Extracted a valid json")
        return data['relevant_information']
    return None





def extract_json_from_response(res_text: str):
    """
    从文本响应中提取 JSON 代码块
    """
    json_extracted = None

    # Pattern 1: 识别 ```json 标记的代码块
    json_matches = re.findall(r"```json([\s\S]*?)```", res_text, re.IGNORECASE)
    if json_matches:
        json_extracted = json_matches[0].strip()

    # Pattern 2: 识别普通的 ``` 代码块
    if not json_extracted:
        json_code_blocks = re.findall(r"```([\s\S]*?)```", res_text, re.IGNORECASE)
        for content in json_code_blocks:
            clean_content = content.strip()
            # 尝试解析为 JSON，确保是 JSON 格式
            try:
                json.loads(clean_content)  # 测试是否有效 JSON
                json_extracted = clean_content
                break
            except json.JSONDecodeError:
                continue  # 跳过非 JSON 代码块

    return json_extracted if json_extracted else res_text  # 返回提取的 JSON 或原始文本


# print(search_query('handsome'))