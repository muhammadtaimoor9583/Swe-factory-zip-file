"""
A proxy agent. Process raw response into json format.
"""

import inspect
from typing import Any

from loguru import logger
import re
import json
from app.data_structures import MessageThread
from app.model import common
from app.post_process import ExtractStatus, is_valid_json
from app.utils import parse_function_invocation
from app.repoBrowse.repo_browse_manage import  RepoBrowseManager
PROXY_PROMPT = """
You are a helpful assistant that extracts API calls and determines whether to terminate the current task, formatting the result as JSON.

### **Information Source**:
The text you receive is **an analysis of the context retrieval process**.  

The text will consist of two parts:
1. **Do we need to collect more context?**  
   - Identify if additional files, folders, or webpages should be browsed for environment setup details.
   - Extract API calls from this section (leave empty if none are needed).

2. **Should we terminate the context retrieval process?**  
   - If all necessary information has been collected, set `"terminate": true`.  
     - You should extract detailed collected information form analyssis of the context retrieval agent. This information will be used by other agent.
   - Otherwise, set `"terminate": false` and provide all collected details.



### **IMPORTANT RULES**:
- **Extract all relevant API calls from the text**:
  - If files like `requirements.txt`, `setup.cfg`, `setup.py` are mentioned, call `browse_file_for_environment_info()` on them.
  - If a directory needs exploration, use `browse_folder()`, ensuring `depth` defaults to `"1"` if unspecified.
- If the API call includes a path, the default format should use Linux-style with forward slashes (/).
- Ensure all API calls are valid Python expressions.
- browse_file_for_environment_info("path.to.file") should be written as browse_file_for_environment_info("path/to/file")
- the browse_folder API call MUST include the depth parameter, defaulting to "1" if not provided.
- You MUST ignore the argument placeholders in API calls. For example:
    Invalid Example: browse_folder(path="src", depth=1) 
    Valid Example: browse_folder("src",1)
- Provide your answer in JSON structure like this:
{
    "API_calls": ["api_call_1(args)", "api_call_2(args)", ...],
    "collected_information": <Content of collected information>.
    "terminate": true/false
}

"""


def run_with_retries(text: str, retries=5) -> tuple[str | None, list[MessageThread]]:
    msg_threads = []
    for idx in range(1, retries + 1):
        logger.debug(
            "Trying to select search APIs in json. Try {} of {}.", idx, retries
        )

        res_text, new_thread = run(text)
        msg_threads.append(new_thread)
        res_text = extract_json_from_response(res_text)
        res_text = res_text.lstrip('```json').rstrip('```')
        logger.debug(res_text)
        extract_status, data = is_valid_json(res_text)

        if extract_status != ExtractStatus.IS_VALID_JSON:
            logger.debug("Invalid json. Will retry.")
            continue

        valid, diagnosis = is_valid_response(data)
        if not valid:
            logger.debug(f"{diagnosis}. Will retry.")
            continue

        logger.debug("Extracted a valid json")
        return res_text, msg_threads
    return None, msg_threads


def run(text: str) -> tuple[str, MessageThread]:
    """
    Run the agent to extract issue to json format.
    """

    msg_thread = MessageThread()
    msg_thread.add_system(PROXY_PROMPT)
    msg_thread.add_user(text)
    res_text, *_ = common.SELECTED_MODEL.call(
        msg_thread.to_msg(), response_format="json_object"
    )

    msg_thread.add_model(res_text, [])  # no tools

    return res_text, msg_thread


def is_valid_response(data: Any) -> tuple[bool, str]:
    if not isinstance(data, dict):
        return False, "Json is not a dict"

    if not data.get("terminate"):
        terminate = data.get("terminate")
        if terminate is None:
            return False, "'terminate' parameter is missing"

        if not isinstance(terminate, bool):
            return False, "'terminate' parameter must be a boolean (true/false)"

    else:
        if not data.get("collected_information"):
            summary = data.get("collected_information")
            if summary is None:
                return False, "'collected_information' parameter is missing"

            if not isinstance(summary, str):
                return False, "'collected_information' parameter must be a str"   

        for api_call in data["API_calls"]:
            if not isinstance(api_call, str):
                return False, "Every API call must be a string"

            try:
                func_name, func_args = parse_function_invocation(api_call)
            except Exception:
                return False, "Every API call must be of form api_call(arg1, ..., argn)"
            function = getattr( RepoBrowseManager, func_name, None)
            if function is None:
                return False, f"the API call '{api_call}' calls a non-existent function"

            arg_spec = inspect.getfullargspec(function)
            arg_names = arg_spec.args[1:]  # first parameter is self

            if len(func_args) != len(arg_names):
                return False, f"the API call '{api_call}' has wrong number of arguments"

    return True, "OK"


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
