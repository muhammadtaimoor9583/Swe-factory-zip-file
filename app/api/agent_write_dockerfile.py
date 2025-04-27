"""
An agent, which is only responsible for the write_dockerfile tool call.
"""

import json
import shutil
from collections.abc import Callable, Iterable
from copy import deepcopy
from os.path import join as pjoin
from pathlib import Path
import os
from loguru import logger

from app import globals
from app.api import agent_common
from app.api.python import validation
from app.data_structures import MessageThread, MethodId
from app.log import print_acr, print_patch_generation
from app.model import common
from app.post_process import (
    ExtractStatus,
    extract_diff_one_instance,
    record_extract_status,
)
from app.task import SweTask, Task
import re


SYSTEM_PROMPT_DOCKERFILE = """You are a software agent specialized in creating Docker environments for software projects.  
Your task is to generate a **Dockerfile** that ensures the provided test files can be executed correctly in an isolated environment.
After that, the eval script agent will generate a eval script. The test log anlysis agent will setup the environment based on your dockerfile and run the eval script.

You will receive **environment setup information** from the **context retrieval agent**, including:
- The required **OS** and package managers.
- Necessary **dependencies** (system libraries, Python packages, Node.js modules, etc.).
- The correct **programming language version** and any virtual environments (e.g., Conda, venv).
- Any **additional configuration steps** needed before running the tests.

### Your Responsibilities:
1. Use the collected information to set up the environment properly.
2. Ensure all dependencies are installed and correctly configured.
3. Configure the system to allow the provided test files to be executed.
4. Generate a complete, structured **Dockerfile** based on the given information.

Your **Dockerfile must be robust and reproducible**, ensuring that the tests run successfully in an isolated container."""



USER_PROMPT_INIT_DOCKERFILE = """Generate a **Dockerfile** based on the collected environment setup information.  
The Dockerfile must ensure that the provided test files can be executed correctly.

### **Requirements:**
1. **Clone the repository** inside the Docker container into `/testbed/` and set `WORKDIR` to `/testbed/`.
2. **Checkout a specific commit SHA**, which will be provided by the user.
3. **Set up the environment** based on the information from the context retrieval agent:
   - Install necessary system dependencies and programming language versions.
   - Set up a virtual environment (`testbed`) if required.
   - Install all necessary libraries and dependencies.
4. **Ensure test execution** by setting up all necessary configurations.

### Important Notes:
1. You are FORBIDDEN to run tests in the dockerfile, tests will be run using eval script.
2. When building the Dockerfile, you MUST prioritize using package managers such as Conda, Maven, or NPM etc to set up the environment efficiently.
3. Ensure shell compatibility by using `/bin/bash` as the default shell environment to avoid runtime issues.  For example, **do not use `FROM alpine:latest`**, as it lacks `/bin/bash` by default, which may cause runtime errors. Instead, use a base image like `ubuntu:22.04` or `debian:bookworm` that includes Bash by default.
4. Pay more attention when using Ubuntu-based images**, as different versions may have variations in default packages, dependency resolution, and package manager behavior, which could lead to unexpected errors.
5. DO NOT use `COPY` to copy local files** into the Docker container.  
   - For example, avoid using `COPY package.json /testbed/` or `COPY requirements.txt /testbed/`.  
   - Instead, all files should be retrieved directly by **cloning the repository** inside the container to ensure a fully reproducible environment.
6. DO NOT run tests in the Dockerfile**.  
   - Do not include commands like `npm test`, `pytest`, or `mvn test` in the Dockerfile.  
   - Tests will be executed separately, and running them during the Docker build stage is an unnecessary overhead.
7. If there is a reference Dockerfile, use it as a guideline.   
8. Do not use ENTRYPOINT.
9. Please install necessary essential tools and libraries required for development and runtime, such as git etc.
10. When setting up dependencies for the target repository (e.g., `torch 3.33`), **DO NOT** install the package directly from external registries (e.g., PyPI, NPM, Maven Central) using commands like `pip install <package>` (e.g., `pip install torch`).  
   Instead, **you can install the repository itself in development mode** (`pip install -e .` for Python, `npm link` for Node.js, or `mvn install` for Java) to ensure that the local repository’s code is correctly referenced during execution.
   **Why is this important?**  
   - If you modify the repository’s source code but have already installed a pre-built package from the registry, your system may load the installed package instead of your local code, **leading to incorrect test results and making debugging difficult**.  
   - Using development mode installation (`pip install -e .`, `npm link`, `mvn install`) ensures that the system always references the latest local repository code, preventing version mismatches and ensuring that modifications are properly reflected in subsequent tests.



### **Example Format:**
The Dockerfile must be wrapped in `<dockerfile>` tags. Example:

<dockerfile>
# Base image specification. Defines the foundation OS and architecture for the container (Required)
FROM --platform=linux/x86_64 ubuntu:22.04
ARG DEBIAN_FRONTEND=noninteractive
ENV TZ=Etc/UTC
# System dependencies installation. Installs essential tools and libraries required for development and runtime (Required)
RUN apt update && apt install -y     wget     git     patch     build-essential     libffi-dev     libtiff-dev     python3     python3-pip     python-is-python3     jq     curl     locales     locales-all     tzdata     && rm -rf /var/lib/apt/lists/*
# Install package and environment manager. Downloads and sets up a lightweight environment management tool
RUN wget 'https://repo.anaconda.com/miniconda/Miniconda3-py311_23.11.0-2-Linux-x86_64.sh' -O miniconda.sh     && bash miniconda.sh -b -p /opt/miniconda3     && rm miniconda.sh
ENV PATH=/opt/miniconda3/bin:$PATH
RUN conda init --all     && conda config --append channels conda-forge
# Sets up a dedicated environment with specific dependencies for the target environemnt
RUN /bin/bash -c "source /opt/miniconda3/etc/profile.d/conda.sh &&     conda create -n testbed python=3.7 -y &&     conda activate testbed &&     pip install pytest==6.2.5 typing_extensions==3.10"
# set default workdir to testbed. (Required)
WORKDIR /testbed/
# Target Project setup. Clones source code, configures it, and installs project-specific dependencies
RUN /bin/bash -c "source /opt/miniconda3/etc/profile.d/conda.sh &&     conda activate testbed &&     git clone https://github.com/python/mypy /testbed &&     chmod -R 777 /testbed &&     cd /testbed &&     git reset --hard 6de254ef00f99ce5284ab947f2dd1179db6d28f6 &&     git remote remove origin &&     pip install -r test-requirements.txt &&     pip install -e ."
RUN echo "source /opt/miniconda3/etc/profile.d/conda.sh && conda activate testbed" >> /root/.bashrc
</dockerfile>
"""

SYSTEM_PROMPT_EVAL_SCRIPT = """You are a software agent specialized in writing evaluation scripts to run tests inside a Docker environment.  
Your task is to generate an **evaluation script** that executes the given test files in the prepared Docker environment.  

You will receive the following information:
- **Collected environment details** from the **context retrieval agent**, including dependencies, test execution commands, and special setup steps.
- **The generated Dockerfile** that defines the environment in which the tests will be executed.
- **A list of test files** that must be executed, provided by the user.
- **An evaluation script skeleton (`eval_script_skeleton`) that MUST be followed.**

### Your Responsibilities:
1. Ensure the evaluation script properly activates the environment inside the Docker container.
2. Apply the test patch (if needed) before executing the tests.
3. Use the correct test execution commands as collected by the **context retrieval agent**.

The generated script must follow best practices, ensuring all necessary steps are performed to successfully run the tests."""



USER_PROMPT_INIT_EVAL_SCRIPT = """Generate an **evaluation script** based on the collected environment setup and test execution information.  
The script must execute the provided test files inside the specified Docker environment.

### **Requirements:**
1. **Activate the environment**: Ensure the correct environment (e.g., Conda, venv) is activated before running the tests.
2. **Apply the test patch (if required)**: The test patch may need to be applied before running the tests.
3. **Execute the given test files** using the correct command found by the context retrieval agent.
4. **Ensure proper cleanup**: After running the tests, any modified files should be reset.

### Important Notes:
1. You must **execute only the specified target test files**, rather than running all tests in the repository.  
   - Running all tests can be highly time-consuming and unnecessary.  
   - Ensure that only the **required test cases** are executed based on the provided test file list.  

2. **Optimize execution efficiency by combining multiple test commands into a single command** whenever possible.  
   - Avoid running multiple separate test commands if they can be executed in one batch.  
   - This reduces redundant initialization overhead and speeds up execution.  

3. **Ensure that the output of the evaluation script is concise and structured**, making it easier for the **test log analysis agent** to process.  
   - Avoid excessive logging, unnecessary debug information, or verbose output.  

4. **Follow the structure of the reference evaluation script or eval script skeleton whenever available.  
   - Use **a simple, minimalistic structure** similar to the reference eval script to ensure clarity and maintainability.  
   - The script should be easy to modify and extend without unnecessary complexity.  

5. **The actual test patch content is omitted here for brevity (marked with [CONTENT OF TEST PATCH] placeholder).
    -You must generate the complete git apply command structure, including the heredoc syntax with delimiter (EOF_114329324912).

    -The placeholder will be programmatically replaced with the actual patch content during script execution.

    -Example structure:
    git apply -v - <<'EOF_114329324912'\n[CONTENT OF TEST PATCH]\nEOF_114329324912



Eval script skeleton:
{eval_script_skeleton}

### **Example Format:**
The script must be wrapped in `<script>` tags. Example:

<script>
#!/bin/bash
set -uxo pipefail
source /opt/miniconda3/bin/activate
conda activate testbed
cd /testbed
pip install -r test-requirements.txt && pip install -e . 
git checkout 6de254ef00f99ce5284ab947f2dd1179db6d28f6 "test-data/unit/check-functions.test" "test-data/unit/check-redefine.test"
git apply -v - <<'EOF_114329324912'
[CONTENT OF TEST PATCH]
EOF_114329324912
pytest --no-header -rA --tb=no -p no:cacheprovider -n4 mypy/test/testcheck.py::TypeCheckSuite::check-functions.test mypy/test/testcheck.py::TypeCheckSuite::check-redefine.test
git checkout 6de254ef00f99ce5284ab947f2dd1179db6d28f6 "test-data/unit/check-functions.test" "test-data/unit/check-redefine.test"
</script>
"""

HEREDOC_DELIMITER = "EOF_114329324912"

apply_test_patch_command = f"git apply -v - <<'{HEREDOC_DELIMITER}'\n[CONTENT OF TEST PATCH]\n{HEREDOC_DELIMITER}"


def get_system_prompt_dockerfile():
    return SYSTEM_PROMPT_DOCKERFILE

def get_system_prompt_eval_script():
    return SYSTEM_PROMPT_EVAL_SCRIPT

def get_user_prompt_init_dockerfile():
    return USER_PROMPT_INIT_DOCKERFILE

def get_user_prompt_init_eval_script(eval_script_skeleton):
    return USER_PROMPT_INIT_EVAL_SCRIPT.format(eval_script_skeleton=eval_script_skeleton)

    
def write_dockerfile_with_retries(
    message_thread: MessageThread,
    output_dir: str,
    task: Task,
    retries=3,
    print_callback: Callable[[dict], None] | None = None,
) -> tuple[str, float, int, int]:
    """
    Since the agent may not always write an applicable patch, we allow for retries.
    This is a wrapper around the actual run.
    """
    # (1) replace system prompt
    # messages = deepcopy(message_thread.messages)
    new_thread = message_thread
    # new_thread: MessageThread = MessageThread(messages=messages)
    # new_thread = agent_common.replace_system_prompt(new_thread, SYSTEM_PROMPT)
    # # (2) add the initial user prompt
    # user_prompt_init=USER_PROMPT_INIT.format(eval_script_skeleton=eval_script_skeleton)
    # user_prompt_init +="\nFor contents in <original></original> and <patched></patched>, please do not contain them in extra ```\n```. Something like <original>\n```js\n```</original> is forbidden. Keep these contents clean."
    # new_thread.add_user(user_prompt_init)
    # print_acr(user_prompt_init, "dockerfile generation", print_callback=print_callback)

    can_stop = False
    result_msg = ""
    dockerfile_extracted = None
    os.makedirs(output_dir, exist_ok=True)
    for i in range(1, retries + 2):
        if i > 1:
            debug_file = pjoin(output_dir, f"debug_agent_write_dockerfile_{i - 1}.json")
            with open(debug_file, "w") as f:
                json.dump(new_thread.to_msg(), f, indent=4)

        if can_stop or i > retries:
            break

        logger.info(f"Trying to extract a dockerfile. Try {i} of {retries}.")

        raw_dockerfile_file = pjoin(output_dir, f"agent_dockerfile_raw_{i}")

        # actually calling model
        res_text, *_ = common.SELECTED_MODEL.call(new_thread.to_msg())

        new_thread.add_model(res_text, [])  # no tools

        logger.info(f"Raw dockerfile and produced in try {i}. Writing dockerfile into file.")

        with open(raw_dockerfile_file, "w") as f:
            f.write(res_text)

        print_patch_generation(
            res_text, f"try {i} / {retries}", print_callback=print_callback
        )

        # Attemp to extract a real patch from the raw patch
        # Extract Dockerfile content from model response using regex

        
        # Initialize extraction flags
        dockerfile_extracted = extract_dockerfile_from_response(res_text, output_dir)


        # Determine if both files are extracted
        can_stop = dockerfile_extracted 

        if can_stop:
            result_msg = "Successfully extracted both Dockerfile."
            print_acr(result_msg, f"dockerfile generation try {i}/{retries}", print_callback=print_callback)
            break
        else:
            feedback = "Failed to extract"
            feedback += "Dockerfile" if not dockerfile_extracted else ""
            new_thread.add_user(feedback + ". Please return result in defined format.")
            print_acr(feedback, f"Retry {i}/{retries}", print_callback=print_callback)
    if result_msg == '':
        result_msg = 'Failed to extract'
        
    return result_msg

def write_eval_script_with_retries(
    message_thread: MessageThread,
    output_dir: str,
    test_patch: str,
    task: Task,
    retries=3,
    print_callback: Callable[[dict], None] | None = None,
) -> tuple[str, float, int, int]:
    """
    Since the agent may not always write an applicable patch, we allow for retries.
    This is a wrapper around the actual run.
    """
    # (1) replace system prompt
    # messages = deepcopy(message_thread.messages)
    new_thread = message_thread
    # new_thread: MessageThread = MessageThread(messages=messages)
    # new_thread = agent_common.replace_system_prompt(new_thread, SYSTEM_PROMPT)
    # # (2) add the initial user prompt
    # user_prompt_init=USER_PROMPT_INIT.format(eval_script_skeleton=eval_script_skeleton)
    # user_prompt_init +="\nFor contents in <original></original> and <patched></patched>, please do not contain them in extra ```\n```. Something like <original>\n```js\n```</original> is forbidden. Keep these contents clean."
    # new_thread.add_user(user_prompt_init)
    # print_acr(user_prompt_init, "dockerfile generation", print_callback=print_callback)
    script_extracted = None
    can_stop = False
    result_msg = ""
    os.makedirs(output_dir, exist_ok=True)
    for i in range(1, retries + 2):
        if i > 1:
            debug_file = pjoin(output_dir, f"debug_agent_write_eval_script_{i - 1}.json")
            with open(debug_file, "w") as f:
                json.dump(new_thread.to_msg(), f, indent=4)

        if can_stop or i > retries:
            break

        logger.info(f"Trying to extract a eval script. Try {i} of {retries}.")

        raw_dockerfile_file = pjoin(output_dir, f"agent_eval_script_raw_{i}")

        # actually calling model
        res_text, *_ = common.SELECTED_MODEL.call(new_thread.to_msg())

        new_thread.add_model(res_text, [])  # no tools

        logger.info(f"Raw script and produced in try {i}. Writing script into file.")

        with open(raw_dockerfile_file, "w") as f:
            f.write(res_text)

        print_patch_generation(
            res_text, f"try {i} / {retries}", print_callback=print_callback
        )

        # Attemp to extract a real patch from the raw patch
        # Extract Dockerfile content from model response using regex

        
        # Initialize extraction flags
        script_extracted = extract_eval_script_from_response(res_text, output_dir,test_patch)


        # Determine if both files are extracted
        can_stop = script_extracted 

        if can_stop:
            result_msg = "Successfully extracted eval_script."
            print_acr(result_msg, f"eval script generation try {i}/{retries}", print_callback=print_callback)
            break
        else:
            feedback = "Failed to extract "
            feedback += "Script" if not script_extracted else ""
            new_thread.add_user(feedback + ". Please return result in defined format.")
            print_acr(feedback, f"Retry {i}/{retries}", print_callback=print_callback)

    if result_msg == '':
        result_msg = 'Failed to extract'
        
    return result_msg

def replace_heredoc_content(original_content, test_patch):
    """替换 heredoc 中的内容为指定的 test_patch"""
    lines = original_content.splitlines()
    output_lines = []
    in_heredoc = False
    heredoc_delimiter = "EOF_114329324912"
    
    for line in lines:
        if f"git apply -v - <<'{heredoc_delimiter}'" in line:
            # 找到 heredoc 开始行
            output_lines.append(line)
            in_heredoc = True
            # 直接插入 test_patch 内容
            output_lines.extend(test_patch.splitlines())
        elif in_heredoc and line == heredoc_delimiter:
            # 找到 heredoc 结束行
            output_lines.append(line)
            in_heredoc = False
        elif not in_heredoc:
            # 非 heredoc 内容保持不变
            output_lines.append(line)
    
    return '\n'.join(output_lines)


def extract_dockerfile_from_response(res_text: str, output_dir: str):
        # Process Dockerfile
        dockerfile_path = pjoin(output_dir, "Dockerfile")
        dockerfile_extracted = False
        
        # Pattern 1: <dockerfile> tags
        docker_matches = re.findall(r"<dockerfile>([\s\S]*?)</dockerfile>", res_text)
        for content in docker_matches:
            clean_content = content.strip()
            if clean_content:
                lines = clean_content.splitlines()
                if len(lines) >= 2 and "```" in lines[0] and "```" in lines[-1]:
                    lines = lines[1:-1]
                filtered_content = '\n'.join(lines)
                with open(dockerfile_path, "w") as f:
                    f.write(filtered_content)
                dockerfile_extracted = True
                break  # Stop after first valid extraction

        # Pattern 2: ```dockerfile code block
        if not dockerfile_extracted:
            docker_code_blocks = re.findall(r"```\s*dockerfile\s*([\s\S]*?)```", res_text, re.IGNORECASE)
            for content in docker_code_blocks:
                clean_content = content.strip()
                if clean_content:
                    lines = clean_content.splitlines()
                    if len(lines) >= 2 and "```" in lines[0] and "```" in lines[-1]:
                        lines = lines[1:-1]
                    filtered_content = '\n'.join(lines)
                    with open(dockerfile_path, "w") as f:
                        f.write(filtered_content)
                    dockerfile_extracted = True
                    break
        return dockerfile_extracted

def  extract_eval_script_from_response(res_text: str, output_dir: str, test_patch:str):
        # Process eval.sh
        script_path = pjoin(output_dir, "eval.sh")
        script_skeleton_path = pjoin(output_dir, "eval_skeleton.sh")
        script_extracted = False
        # Pattern 1: <script> tags
        script_matches = re.findall(r"<script>([\s\S]*?)</script>", res_text)
        for content in script_matches:
            clean_content = content.strip()
            if clean_content:
                lines = clean_content.splitlines()
                if len(lines) >= 2 and "```" in lines[0] and "```" in lines[-1]:
                    lines = lines[1:-1]
                filtered_content = '\n'.join(lines)
                filtered_content_with_test_patch = replace_heredoc_content(filtered_content,test_patch)
                with open(script_skeleton_path, "w") as f:
                    f.write(filtered_content)
                with open(script_path, "w") as f:
                    f.write(filtered_content_with_test_patch)
                script_extracted = True
                break

        # Pattern 2: ```script code block
        if not script_extracted:
            script_code_blocks = re.findall(r"```\s*script\s*([\s\S]*?)```", res_text, re.IGNORECASE)
            for content in script_code_blocks:
                clean_content = content.strip()
                if clean_content:
                    lines = clean_content.splitlines()
                    if len(lines) >= 2 and "```" in lines[0] and "```" in lines[-1]:
                        lines = lines[1:-1]
                    filtered_content = '\n'.join(lines)
                    filtered_content_with_test_patch = replace_heredoc_content(filtered_content,test_patch)
                    with open(script_skeleton_path, "w") as f:
                        f.write(filtered_content)
                    with open(script_path, "w") as f:
                        f.write(filtered_content_with_test_patch)
                    script_extracted = True
                    break
        
        # Pattern 3: ```bash code block
        if not script_extracted:
            bash_code_blocks = re.findall(r"```\s*bash.*([\s\S]*?)```", res_text, re.IGNORECASE)
            for content in bash_code_blocks:
                clean_content = content.strip()
                if clean_content:
                    lines = clean_content.splitlines()
                    if len(lines) >= 2 and "```" in lines[0] and "```" in lines[-1]:
                        lines = lines[1:-1]
                    filtered_content = '\n'.join(lines)
                    filtered_content_with_test_patch = replace_heredoc_content(filtered_content,test_patch)
                    with open(script_skeleton_path, "w") as f:
                        f.write(filtered_content)
                    with open(script_path, "w") as f:
                        f.write(filtered_content_with_test_patch)
                    
                    
                    script_extracted = True
                    break
        return script_extracted 


def angelic_debugging_message(
    incorrect_locations: Iterable[tuple[str, MethodId]],
) -> str:
    msg = []

    if incorrect_locations:
        msg.append("The following methods should not have been changed:")
        msg.extend(
            f"    {filename}: {method_id!s}"
            for filename, method_id in incorrect_locations
        )

    return "\n".join(msg)
