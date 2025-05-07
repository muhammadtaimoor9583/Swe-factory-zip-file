"""
A file browsing agent. Process file content into a simple XML environment info format.
"""

import inspect
import re
from typing import Any

from loguru import logger

from app.data_structures import MessageThread
from app.model import common
from app.post_process import ExtractStatus  # Removed is_valid_json
from app.utils import parse_function_invocation

BROWSE_CONTENT_PROMPT = """
You are a file content browsing and analysis agent. Your task is to analyze the provided input (which may be a file or webpage content) and extract any information relevant to setting up the project's environment and running tests.

Return the result enclosed within <analysis></analysis> tags, in free-text format:

Keep it concise and human-readable for end-users, ensuring to preserve the original format of values where applicable.

Example format:
<analysis>
List of libraries:
- flask==2.0.3
- gunicorn
- pytest==7.1.2

Key environment variables:
- DEBUG=true
- SECRET_KEY=this-is-a-secret

Runtime Requirements:
- Python >=3.8
- Node.js 16.x

Testing:
- Test framework: pytest
- Test command: pytest tests/ --disable-warnings --maxfail=5
</analysis>
"""

def browse_file_run_with_retries(content: str, retries=3) -> str | None:
    """Run file content analysis with retries and return the parsed <analysis> content."""
    parsed_result=None
    for idx in range(1, retries + 1):
        logger.debug("Analyzing file content. Try {} of {}", idx, retries)
        
        res_text, _ = browse_file_run(content)

        # Extract <analysis> content if valid
        parsed_result = parse_analysis_tags(res_text)
        if parsed_result:
            logger.success("Successfully extracted environment config")
            logger.info("*"*6)
            logger.info(parsed_result)
            logger.info("*"*6)
            return parsed_result
        else:
            content += 'Please wrap result in clean xml identifier, do not use ```to wrap results. '
            logger.debug(res_text)
            logger.debug("Invalid response or missing <analysis> tags, retrying...")
    if parsed_result:
        return parsed_result
    else:
        return 'Do not get the content of the file.'


def browse_file_run(content: str) -> tuple[str, MessageThread]:
    """Run the simplified content analysis agent."""
    msg_thread = MessageThread()
    msg_thread.add_system(BROWSE_CONTENT_PROMPT)
    msg_thread.add_user(f"File content:\n{content}")  # Truncate to prevent overflow
    
    res_text, *_ = common.SELECTED_MODEL.call(
        msg_thread.to_msg()
    )
    msg_thread.add_model(res_text, [])
    return res_text, msg_thread


def parse_analysis_tags(data: str) -> str | None:
    """Extract and return the content within <analysis>...</analysis> tags."""
    pattern = r"<analysis>([\s\S]+?)</analysis>"
    match = re.search(pattern, data)
    if match:
        return match.group(1).strip()  # Return the content inside <analysis> tags
    return None


