import json
import os
import shutil
from collections.abc import Callable, Mapping
from copy import deepcopy
from os.path import join as pjoin
from pathlib import Path
from docstring_parser import parse
from loguru import logger
import re
from app.analysis import sbfl
from app.analysis.sbfl import NoCoverageData
from app.api import agent_proxy,  agent_write_dockerfile, agent_analyze_test_log,agent_web_search,agent_common
# agent_write_locations, agent_write_patch,
from app.data_structures import FunctionCallIntent, MessageThread
from app.log import log_exception,setup_logger,close_logger
# from app.search.search_manage import SearchManager
from app.repoBrowse.repo_browse_manage import  RepoBrowseManager
# from app.api.python.validation import PythonValidator
from app.task import Task
import docker
from datetime import datetime
from app.model import common
from app.api.docker_utils import (
    cleanup_container,
    remove_image,
    copy_to_container,
    exec_run_with_timeout,
    BuildImageError,
    build_container,
    EvaluationError

)
import traceback
MAX_LINE_NUM = 2000
DIFF_MODIFIED_FILE_REGEX = r"--- a/(.*)"
ansi_escape = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
class ProjectApiManager:
    ################# State machine specific ################
    # NOTE: this section is for state machine; APIs in stratified mode are specified
    # in agent_api_selector.py
    api_functions = [
        "browse_folder",
        "browse_file_for_environment_info",
        "browse_webpage_for_environment_info",
         "write_dockerfile",
         'write_eval_script',
         'search_files_by_keyword',
         'analyze_test_log',
         'setup_docker_and_run_test',
         'web_search'


        # "search_class",
        # "search_class_in_file",
        # "search_method",
        # "search_method_in_class",
        # "search_method_in_file",
        # "search_code",
        # "search_code_in_file",
        # "write_patch",
    ]

    def next_tools(self) -> list[str]:
        """
        Return the list of tools that should be used in the next round.
        """
        repo_browse_tools = [
            "browse_folder",
            "browse_file_for_environment_info",
            "browse_webpage_for_environment_info",
        ]

        debugging_tools = []
        # [
        #     "setup_dockerfile",
        #     "exec_command_in_docker",
        #     "get_webpage_content",
        # ]
        all_tools = repo_browse_tools + ["write_dockfile"]+debugging_tools
        if not self.curr_tool:
            # this means we are at the beginning of the conversation
            # you have to start from doing some search
            return ["browse_folder"]

        state_machine = {
            "browse_folder": repo_browse_tools,
            "browse_file_for_environment_info": repo_browse_tools + ["write_dockfile"],
            "browse_webpage_for_environment_info": repo_browse_tools + ["write_dockfile"],
            "write_dockerfile":"setup_dockerfile"
        }
        return state_machine[self.curr_tool]

    def get_test_files(self):
        test_files = re.findall(DIFF_MODIFIED_FILE_REGEX, self.test_patch)
        return test_files

    def get_eval_script_skeleton(self):
        HEREDOC_DELIMITER = "EOF_114329324912"
        test_files = self.test_files
        reset_test_files = ['"' + t + '"' for t in test_files]
        # Reset test files to the state they should be in before the patch.
        reset_tests_command = f"git checkout {self.task.commit} {' '.join(reset_test_files)}"
        apply_test_patch_command = (
            f"git apply -v - <<'{HEREDOC_DELIMITER}'\n[CONTENT OF TEST PATCH]\n{HEREDOC_DELIMITER}"
        )
        # apply_test_patch_command = (
        #     f"git apply -v - <<'{HEREDOC_DELIMITER}'\n{self.test_patch}\n{HEREDOC_DELIMITER}"
        # )
        
        eval_commands = [
            f"cd /testbed",
        ]
        eval_commands += [
            reset_tests_command,
            apply_test_patch_command,
            reset_tests_command,  # Revert tests after done, leave the repo in the same state as before
        ]
        return "\n".join(["#!/bin/bash", "set -uxo pipefail"] + eval_commands) + "\n"

    def __init__(self, task: Task, output_dir: str ,client:docker.DockerClient,enable_web_search:False,start_time:None):
        # for logging of this task instance
        self.task = task

        # where to write our output
        self.output_dir = os.path.abspath(output_dir)
        self.build_image_dir = os.path.join(self.output_dir, "build_image") 
        self.run_test_dir = os.path.join(self.output_dir, "run_test") 
        self.task.setup_project()
        # self.setup_project(self.task)
        # build search manager
        # self.search_manager = SearchManager(self.task.project_path,self.task.language)
        self.repo_browse_manager = RepoBrowseManager(self.task.project_path)
        # keeps track which tools is currently being used
        self.curr_tool: str | None = None
        self.task_id = task.task_id
        self.patch = self.task.patch
        self.test_patch = self.task.test_patch
        self.reference_setup = self.task.reference_setup
        self.context_retrieval_num = 0
        self.write_dockerfile_num = 0
        self.write_eval_script_num = 0
        self.setup_dockerfile_num = 0
        self.run_test_num = 0
        self.test_log_analysis_num = 0
        self.web_search_num = 0
        self.test_files = self.get_test_files()
        # record the sequence of tools used, and their return status
        self.tool_call_sequence: list[Mapping] = []
        self.eval_script_skeleton = self.get_eval_script_skeleton()
        # record layered API calls
        self.tool_call_layers: list[list[Mapping]] = []
        self.timeout = 3600
        self.repo_basic_info = self.get_repository_basic_info()
        # record cost and token information
        self.cost: float = 0.0
        self.input_tokens: int = 0
        self.output_tokens: int = 0
        self.root_structure = self.browse_folder('/',1)
        self.client = client
        self.write_dockerfile_agent_msg_thread = None
        self.write_eval_script_agent_msg_thread = None
        self.test_log_analysis_agent_msg_thread = None
        self.context_retrieval_agent_msg_thread = None
        self.web_search_agent_msg_thread = None
        self.start_time = start_time
        self.enable_web_search = enable_web_search

        # we set this to false when the code information  is modified.
        self.is_context_retrieval = True
        # we set this to false when the dockerfile is modified.
        self.is_write_dockerfile = True
        # we set this to false when the eval script is modified.
        self.is_write_eval_script = True
        self.is_web_search = False
        # when this is set to True, we finish this task.
        self.is_finish = False
        # self.

        # if we need modify dockerfile and eval script at the same time, the dockerfile is in a higher priotiry,
        # cause dockerfile is the base of running eval script.

        # When no information to collect and no file to modify, we will try to run test.
        # self.is_run_test = False

    @classmethod
    def get_short_func_summary_for_openai(cls) -> str:
        """
        Get a short summary of all tool functions.
        Intended to be used for constructing the initial system prompt.
        """
        summary = ""
        for fname in cls.api_functions:
            if not hasattr(cls, fname):
                continue
            func_obj = getattr(cls, fname)
            doc = parse(func_obj.__doc__)
            short_desc = (
                doc.short_description if doc.short_description is not None else ""
            )
            summary += f"\n- {fname}: {short_desc}"
        return summary

    @classmethod
    def get_full_funcs_for_openai(cls, tool_list: list[str]) -> list[dict]:
        """
        Return a list of function objects which can be sent to OpenAI for
        the function calling feature.

        Args:
            tool_list (List[str]): The available tools to generate doc for.
        """
        tool_template = {
            "type": "function",
            "function": {
                "name": "",
                "description": "",
                "parameters": {
                    "type": "object",
                    "properties": {},  # mapping from para name to type+description
                    "required": [],  # name of required parameters
                },
            },
        }
        all_tool_objs = []

        for fname in tool_list:
            if not hasattr(cls, fname):
                continue
            tool_obj = deepcopy(tool_template)
            tool_obj["function"]["name"] = fname
            func_obj = getattr(cls, fname)
            # UPDATE: we only parse docstring now
            # there are two sources of information:
            # 1. the docstring
            # 2. the function signature
            # Docstring is where we get most of the textual descriptions; for accurate
            # info about parameters (whether optional), we check signature.

            ## parse docstring
            doc = parse(func_obj.__doc__)
            short_desc = (
                doc.short_description if doc.short_description is not None else ""
            )
            long_desc = doc.long_description if doc.long_description is not None else ""
            description = short_desc + "\n" + long_desc
            tool_obj["function"]["description"] = description
            doc_params = doc.params
            for doc_param in doc_params:
                param_name = doc_param.arg_name
                if param_name == "self":
                    continue
                typ = doc_param.type_name
                desc = doc_param.description
                is_optional = doc_param.is_optional
                # now add this param to the tool object
                tool_obj["function"]["parameters"]["properties"][param_name] = {
                    "type": typ,
                    "description": desc,
                }
                if not is_optional:
                    tool_obj["function"]["parameters"]["required"].append(param_name)

            all_tool_objs.append(tool_obj)

        return all_tool_objs

    def dispatch_intent(
        self,
        intent: FunctionCallIntent,
        message_thread: MessageThread,
        print_callback: Callable[[dict], None] | None = None,
    ) -> tuple[str, str, bool]:
        """Dispatch a function call intent to actually perform its action.

        Args:
            intent (FunctionCallIntent): The intent to dispatch.
            message_thread (MessageThread): the current message thread,
                since some tools require it.
        Returns:
            The result of the action.
            Also, a summary that should be communicated to the model.
        """
        if (intent.func_name not in self.api_functions) and (
            intent.func_name not in ["get_class_full_snippet", "propose_locs"]
        ):
            error = f"Unknown function name {intent.func_name}."
            summary = "You called a tool that does not exist. Please only use the tools provided."
            return error, summary, False
        func_obj = getattr(self, intent.func_name)
        try:
            # ready to call a function
            self.curr_tool = intent.func_name
            if intent.func_name in ["write_dockerfile",'write_eval_script','analyze_test_log','web_search']:
                # these two functions require the message thread
                call_res = func_obj(message_thread, print_callback=print_callback)
            else:
                call_res = func_obj(**intent.arg_values)
        except Exception as e:
            # TypeError can happen when the function is called with wrong parameters
            # we just return the error message as the call result
            log_exception(e)
            error = str(e)
            summary = "The tool returned error message."
            call_res = (error, summary, False)

        logger.debug("Result of dispatch_intent: {}", call_res)

        # record this call and its result separately
        _, _, call_is_ok = call_res
        self.tool_call_sequence.append(intent.to_dict_with_result(call_is_ok))

        if not self.tool_call_layers:
            self.tool_call_layers.append([])
        self.tool_call_layers[-1].append(intent.to_dict_with_result(call_is_ok))

        return call_res

    def start_new_tool_call_layer(self):
        self.tool_call_layers.append([])

    def dump_tool_call_sequence_to_file(self):
        """Dump the sequence of tool calls to a file."""
        tool_call_file = pjoin(self.output_dir, "tool_call_sequence.json")
        with open(tool_call_file, "w") as f:
            json.dump(self.tool_call_sequence, f, indent=4)

    def dump_tool_call_layers_to_file(self):
        """Dump the layers of tool calls to a file."""
        tool_call_file = pjoin(self.output_dir, "tool_call_layers.json")
        with open(tool_call_file, "w") as f:
            json.dump(self.tool_call_layers, f, indent=4)

    ###################################################################
    ########################## API functions ##########################
    ###################################################################


    def browse_folder(self, path: str, depth: str) -> str:
        """
        Browse and return the folder structure for a given path in the repository.

        Args:
            path: The folder path to browse, relative to the project root.
            depth: The number of folder levels to include in the output (depth>0). 

        Returns:
            A string representation of the folder structure.   
            Example output for path='src' and depth='2':
            src/
                main.py
                utils/
                    helper.py
                    constants.py
        """
        depth = int(depth)
        return self.repo_browse_manager.browse_folder(path, depth)

    def search_files_by_keyword(self, keyword: str) -> str:
        """Search for files in the repository whose names contain the given keyword.
        
        Args:
            keyword: The keyword to search for in file names
            
        Returns:
            A formatted string showing the matching files (up to 10), or a message if too many files are found.
        """
        return self.repo_browse_manager.search_files_by_keyword(keyword)

    def browse_file_for_environment_info(self, file_path: str) -> str:
        """Browse a file and extract environment setup information.
        
        Args:
            file_path: The path to the file to browse, relative to the project root.
            
        Returns:
            A string containing extracted environment setup info.
        """
        # Ensure file_path is correctly adjusted to be relative to project root
        
        
        try:
            if not file_path.startswith(self.task.project_path):
                file_path = pjoin(self.task.project_path, file_path)
            # Attempt to extract the environment setup information from the file
            extracted_info = self.repo_browse_manager.browse_file_for_environment_info(file_path)

            # Return extracted information (assumed to be a string)
            return extracted_info

        except Exception as e:
            # Log the error message for debugging purposes
            logger.error(f"Error while browsing file {file_path}: {e}")

            # Return an appropriate error message or an empty string
            return "An error occurred while extracting environment info." , 'error', False

    
    def browse_webpage_for_environment_info(self, url: str) -> str:
        """Fetch a web page and extract environment setup information.
        
        Args:
            url: The URL of the web page to fetch and analyze.
            
        Returns:
            A string containing extracted environment setup info.
        """
        try:
 
            
            # Step 2: Use LLM to extract environment information
            extracted_info = self.repo_browse_manager.browse_webpage_run_with_retries(webpage_content)

            # Step 3: Return extracted information
            return extracted_info

        except Exception as e:
            # Log the error message for debugging purposes
            logger.error(f"Error while browsing file {file_path}: {e}")

            # Return an appropriate error message or an empty string
            return "An error occurred while extracting environment info." , 'error', False
    

    def get_run_test_agent_status(self):
        return not (self.is_context_retrieval or self.is_web_search or self.is_write_dockerfile or self.is_write_eval_script)   
    
    def get_context_retrieval_agent_status(self):
        return self.is_context_retrieval
    
    def get_write_dockerfile_agent_status(self):
        return not self.is_context_retrieval and not self.is_web_search and self.is_write_dockerfile

    def get_write_eval_script_agent_status(self):
        return not (self.is_context_retrieval or self.is_write_dockerfile or self.is_web_search) and self.is_write_eval_script

    def get_web_search_agent_status(self):
        return self.enable_web_search and self.is_web_search
    


    
    def write_dockerfile(
        self,
        message_thread: MessageThread,
        print_callback: Callable[[dict], None] | None = None
    ) -> tuple[str, str, bool]:
        """Based on the current context, ask another agent to write a patch.

        When you think the current information is sufficient to write a patch, invoke this tool.

        The tool returns a patch based on the current available information.
        """
        
        prev_output_path = os.path.join(self.output_dir, f'output_dockerfile_{self.write_dockerfile_num}')
        prev_file_path = os.path.join(prev_output_path, 'Dockerfile')

        # 检查文件是否存在，并设置 mode
        mode = 'modify' if os.path.exists(prev_file_path) else 'create'  
        if mode == 'modify':
            modify_prompt = """Please modify current dockerfile according to colelcted information. 
            Return modified dockerfile in defined format. Wrap results in <dockerfile></dockerfile>.
            """
            msg_prev_dockerfile = f'Previous dockerfile:\n{self.get_latest_dockerfile()}\n\n'
            message_thread.add_user(msg_prev_dockerfile)
            message_thread.add_user(modify_prompt)
        else:
            message_thread.add_user(agent_write_dockerfile.get_user_prompt_init_dockerfile())
        
        self.write_dockerfile_num += 1
        # output_path = f'{self.output_dir}/output_dockerfile_{self.write_dockerfile_num}'
        output_path = self.get_latest_write_dockerfile_output_dir()
        tool_output = agent_write_dockerfile.write_dockerfile_with_retries(
            message_thread,
            output_path,
            self.task,
            # self.validator,
            print_callback=print_callback
        )
        
        summary = "The tool returned the patch written by another agent."
        file_path = f'{output_path}/Dockerfile'

        if os.path.isfile(file_path):
            # 如果当前 eval.sh 存在，标记为无需再写脚本
            self.is_write_dockerfile = False
        else:
            
            self.is_write_dockerfile = True
            if self.write_dockerfile_num > 1:
                # 获取上一轮的文件路径
                if os.path.isfile(prev_file_path):
                    # 如果上一轮的 eval.sh 存在，复制到当前路径
                    shutil.copy(prev_file_path, file_path)
                    print(f"Copied {prev_file_path} to {file_path}")
                    self.is_write_dockerfile = False  # 复制成功后无需再写
                else:
                    print(f"Previous file {prev_file_path} does not exist, no copy performed.")
            else:
                print("No previous Dockerfile available. ")
        # The return status of write_patch doe
        return tool_output, summary, True

    def write_eval_script(
        self,
        message_thread: MessageThread,
        print_callback: Callable[[dict], None] | None = None,
    ) -> tuple[str, str, bool]:
        """Based on the current context, ask another agent to write a patch.

        When you think the current information is sufficient to write a patch, invoke this tool.

        The tool returns a patch based on the current available information.
        """
        prev_output_path = os.path.join(self.output_dir, f'output_eval_script_{self.write_eval_script_num}')
        prev_file_path = os.path.join(prev_output_path, 'eval.sh')

        # 检查文件是否存在，并设置 mode
        mode = 'modify' if os.path.exists(prev_file_path) else 'create'  
        
        dockerfile_msg = f'The dockerfile environment you are running tests on:\n{self.get_latest_dockerfile()}\n\n'
        if mode=='modify':
            logger.info("modify previous eval_script")
            modify_prompt = """Please modify current eval script according to colelcted information. 
            Return modified eval script in defined format. Wrap results in <script></script>.
            """
            message_thread.add_user(dockerfile_msg)
            msg_prev_eval_script = f'Previous generated eval script (Test patch omitted because of its long length):\n{self.get_latest_eval_script_skeleton()}\n\n'
            message_thread.add_user(msg_prev_eval_script)
            message_thread.add_user(modify_prompt)
            
        else:
            logger.info("create a eval_script")
            message_thread.add_user(dockerfile_msg)
            message_thread.add_user(agent_write_dockerfile.get_user_prompt_init_eval_script(self.eval_script_skeleton))
        # output_path = f'{self.output_dir}/output_eval_script_{self.write_eval_script_num}'
        self.write_eval_script_num += 1
        output_path = self.get_latest_write_eval_script_output_dir()
        logger.info("start writing eval_script")
        tool_output = agent_write_dockerfile.write_eval_script_with_retries(
            message_thread,
            output_path,
            self.test_patch,
            self.task,
            # self.validator,
            print_callback=print_callback
        )
        
        summary = "The tool returned the eval script written by another agent."
        file_path = f'{output_path}/eval.sh'

        if os.path.isfile(file_path):
            # 如果当前 eval.sh 存在，标记为无需再写脚本
            self.is_write_eval_script = False
        else:
            # 如果当前 eval.sh 不存在，标记为需要写脚本
            self.is_write_eval_script = True
            if self.write_eval_script_num > 1:
                # 获取上一轮的文件路径
       
                if os.path.isfile(prev_file_path):
                    # 如果上一轮的 eval.sh 存在，复制到当前路径
                    shutil.copy(prev_file_path, file_path)
                    print(f"Copied {prev_file_path} to {file_path}")
                    self.is_write_eval_script = False  # 复制成功后无需再写
                else:
                    print(f"Previous file {prev_file_path} does not exist, no copy performed.")
            else:
                print("No previous eval script available.")
                
               
        # The return status of write_patch doe
        return tool_output, summary, True

    def write_patch(
        self,
        message_thread: MessageThread,
        print_callback: Callable[[dict], None] | None = None,
    ) -> tuple[str, str, bool]:
        """Based on the current context, ask another agent to write a patch.

        When you think the current information is sufficient to write a patch, invoke this tool.

        The tool returns a patch based on the current available information.
        """
        tool_output = agent_write_patch.run_with_retries(
            message_thread,
            self.output_dir,
            self.task,
            # self.validator,
            print_callback=print_callback
        )
        summary = "The tool returned the patch written by another agent."
        # The return status of write_patch does not really matter, so we just use True here
        return tool_output, summary, True


    def proxy_apis(self, text: str) -> tuple[str | None, str, list[MessageThread]]:
        """Proxy APIs to another agent."""
        tool_output, new_thread = agent_proxy.run_with_retries(
            text
        )  # FIXME: type of `text`
        if tool_output is None:
            summary = "The tool returned nothing. The main agent probably did not provide enough clues."
        else:
            summary = "The tool returned the selected search APIs in json format generated by another agent."
        return tool_output, summary, new_thread



        

    def build_docker_image(
        self,
        dockerfile,
        cur_build_image_dir,
        setup_dockerfile_num,
        task_id,
        image_name,
        build_image_logger,
        client
    ):
        """Build Docker image with detailed logging and error handling."""
    
        
        build_image_logger.info(
            f"Building image {task_id}\n"
            f"Using dockerfile:\n{dockerfile}\n"
        )

    
        
        
        # 删除旧镜像，确保不影响主逻辑
        if setup_dockerfile_num > 1:
            # prev_image_name = f"{task_id}:latest_{setup_dockerfile_num - 1}"
            prev_image_name = f"{self.task_id}-dockerfile{self.setup_dockerfile_num-1}:latest"
            try:
                client.images.remove(prev_image_name, force=True)
                build_image_logger.info(f"Deleted previous image: {prev_image_name}")

            except docker.errors.ImageNotFound:
                build_image_logger.info(f"Do not find previous image, images list is clean.")
            except Exception as e:  # 捕获所有异常，确保继续执行
                build_image_logger.error(f"Failed to delete previous image {prev_image_name}: {str(e)}")
        
        

        dockerfile_path = f'{cur_build_image_dir}/Dockerfile'
        with open(dockerfile_path, "w") as f:
            f.write(dockerfile)

        last_command = None  # 最近的命令
        command_output = []  # 存储最近命令后的所有输出
        capturing = False   # 是否开始捕获日志
        response = client.api.build(
            path=cur_build_image_dir,
            tag=image_name,
            rm=True,
            forcerm=True,
            decode=True,
            platform="linux/x86_64",
            nocache=True,
        )

        # 处理构建响应
        for chunk in response:
            if "stream" in chunk:
                # 移除 ANSI 转义序列并记录日志
                chunk_stream = ansi_escape.sub("", chunk["stream"]).strip()

                build_image_logger.info(chunk_stream)
                lines = chunk_stream.split('\n')  # 关键点：拆分为多行
            
                for line in lines:
                    if not line:
                        continue  # 跳过空行
                    
                    if line.startswith("Step "):
                        # 新步骤：重置记录器
                        last_command = line
                        command_output = [line]  # 步骤行作为第1行
                        capturing = True
                    elif capturing:
                        # 严格行数控制：仅在前2000行内记录
                        if len(command_output) < MAX_LINE_NUM:
                            command_output.append(line)
                
                # # 如果是 Step 开头的行，开始新的命令捕获
                # if chunk_stream.startswith("Step "):
                #     last_command = chunk_stream
                #     command_output = [chunk_stream]  # 重置并记录命令
                #     capturing = True
                # elif capturing:
                #     # 如果已经在捕获，追加输出
                #     command_output.append(chunk_stream)
                    
            elif "errorDetail" in chunk and capturing:
                # 处理错误信息
                error_msg = ansi_escape.sub("", chunk["errorDetail"]["message"])
                command_output.append(f"Error: {error_msg}")
                build_image_logger.error(f"Error: {error_msg}")
                
                # 将 last_command 之后的输出组合成 tool_output
                
                raise docker.errors.BuildError(error_msg, build_log=command_output)  # 不需要整个 buildlog

        # 构建成功
        build_image_logger.info("Image built successfully!")
 

    def get_latest_test_log(self):
        cur_test_dir = f'{self.run_test_dir}_{self.run_test_num}'
        test_output_path =  f"{cur_test_dir}/test_output.txt"
        with open(test_output_path, 'r') as file:
            test_log = file.read()
        return test_log

    def get_latest_dockerfile(self):
        dockerfile = None
        try:
            dockerfile_path = f'{self.get_latest_write_dockerfile_output_dir()}/Dockerfile'
            with open(dockerfile_path, 'r') as file:
                dockerfile = file.read()
        except Exception as e:
            logger.error(e)
        return dockerfile

    def get_latest_eval_script(self):
        eval_script = None
        try:
            eval_script_path = f'{self.get_latest_write_eval_script_output_dir()}/eval.sh'
            with open(eval_script_path, 'r') as file:
                eval_script = file.read()
        except Exception as e:
            logger.error(e)
        return eval_script

    def get_latest_eval_script_skeleton(self):
        eval_script_skeleton = None
        try:
            eval_script_path = f'{self.get_latest_write_eval_script_output_dir()}/eval_skeleton.sh'
            with open(eval_script_path, 'r') as file:
                eval_script_skeleton = file.read()
        except Exception as e:
            logger.error(e)
        return eval_script_skeleton

    def get_latest_run_test_output_dir(self):
        output_dir = f'{self.run_test_dir}_{self.run_test_num}'
        return output_dir

    def get_latest_build_image_output_dir(self):
        output_dir = f'{self.build_image_dir}_{self.setup_dockerfile_num}'
        return output_dir

    def get_latest_context_retrieval_output_dir(self):
        output_dir = f'{self.output_dir}/output_context_retrieval_{self.context_retrieval_num}'
        return output_dir

    def get_latest_write_dockerfile_output_dir(self):
        output_dir = f'{self.output_dir}/output_dockerfile_{self.write_dockerfile_num}'
        return output_dir

    def get_latest_write_eval_script_output_dir(self):
        output_dir = f'{self.output_dir}/output_eval_script_{self.write_eval_script_num}'
        return output_dir
    
    def get_latest_web_search_output_dir(self):
        output_dir = f'{self.output_dir}/output_web_search_{self.web_search_num}'
        return output_dir

    def get_latest_test_log_analysis_output_dir(self):
        output_dir = f'{self.output_dir}/test_log_analysis_{self.test_log_analysis_num}'
        return output_dir

    def run_test(self, eval_script: str) -> tuple[str | None, str, list[MessageThread]]:
        tool_output = ""
        summary = ""
        success = False
        patch = self.task.patch
        self.run_test_num += 1
        cur_test_dir = f'{self.run_test_dir}_{self.run_test_num}'
        os.makedirs(cur_test_dir, exist_ok=True)
        run_test_logger = setup_logger(self.task_id, Path(f'{cur_test_dir}/run_test.log'))
        # test_image_name = f"{self.task_id}:latest_{self.setup_dockerfile_num}"
        test_image_name = f"{self.task_id}-dockerfile{self.setup_dockerfile_num}:latest"
        # test_container_name =  f"{self.task_id}:test_{self.run_test_num}"
        test_container_name = f"{self.task_id}-test{self.run_test_num}"
        instance_id = self.task_id
        container = None
        test_output_path = f'{cur_test_dir}/test_output.txt'
        try:
            container = build_container(self.client,test_image_name,test_container_name,instance_id,run_test_logger)

            container.start()
            run_test_logger.info(f"Container for {instance_id} started: {container.id}")
            tool_output += f"Container {container.id} started.\n"
            summary += "Container started.\n"
            # Copy model prediction as patch file to container
            patch_file = Path(f"{cur_test_dir}/patch.diff")
            patch_file.write_text(patch or "")
            run_test_logger.info(
                f"Intermediate patch for {instance_id} written to {patch_file}, now applying to container..."
            )
            copy_to_container(container, patch_file, Path("/tmp/patch.diff"))

        
            # Attempt to apply patch to container
            val = container.exec_run(
                "git apply --allow-empty -v /tmp/patch.diff",
                workdir="/testbed",
                user="root",
            )
            if val.exit_code != 0:
                run_test_logger.info(f"Failed to apply patch to container, trying again...")
                
                # try "patch --batch --fuzz=5 -p1 -i {patch_path}" to try again
                val = container.exec_run(
                    "patch --batch --fuzz=5 -p1 -i /tmp/patch.diff",
                    workdir="/testbed",
                    user="root",
                )
                if val.exit_code != 0:
                    run_test_logger.info(f"Apply patch fail:\n{val.output.decode('utf-8')}")
                    raise EvaluationError(
                        instance_id,
                        f"Apply patch fail:\n{val.output.decode('utf-8')}. Check if you apply patch in incorrect directories.",
                        run_test_logger,
                    )
                else:
                    run_test_logger.info(f"Apply patch success:\n{val.output.decode('utf-8')}")
            else:
                run_test_logger.info(f"Apply patch success:\n{val.output.decode('utf-8')}")
            tool_output += "Patch applied successfully.\n"
            summary += "Patch applied.\n"
                    # Get git diff before running eval script
            git_diff_output_before = (
                container.exec_run("git diff", workdir="/testbed").output.decode("utf-8").strip()
            )
            run_test_logger.info(f"Git diff before:\n{git_diff_output_before}")

            eval_file = Path(f"{self.get_latest_run_test_output_dir()}/eval.sh")
            eval_file.write_text(eval_script)
            run_test_logger.info(
                f"Eval script for {instance_id} written to {patch_file}, now applying to container..."
            )
            copy_to_container(container, eval_file, Path("/eval.sh"))

            # Run eval script, write output to logs
            result = exec_run_with_timeout(container, "/bin/bash /eval.sh", timeout=self.timeout)
            test_output = result.decode("utf-8")
            
            with open(test_output_path, "w") as f:
                f.write(test_output)
            run_test_logger.info(f"Test output for {instance_id} written to {test_output_path}")

            # Get git diff after running eval script
            git_diff_output_after = (
                container.exec_run("git diff", workdir="/testbed").output.decode("utf-8").strip()
            )

            # Check if git diff changed after running eval script
            run_test_logger.info(f"Git diff after:\n{git_diff_output_after}")
            if git_diff_output_after != git_diff_output_before:
                run_test_logger.info(f"Git diff changed after running eval script")
                tool_output += "Note: Git diff changed after test execution.\n"
                summary += "Git diff changed.\n"

        except EvaluationError as e:
            error_msg = (f"EvaluationError {instance_id}: {e}\n"
                        f"{traceback.format_exc()}\n"
                        f"Check ({run_test_logger.log_file}) for more information.")
            run_test_logger.info(error_msg)
            tool_output += error_msg + "\n"
            summary += "Evaluation error occurred.\n"
            success = False
           
        except Exception as e:
            error_msg = (f"Error in evaluating model for {instance_id}: {e}\n"
                        f"{traceback.format_exc()}\n"
                        f"Check ({run_test_logger.log_file}) for more information.")
            run_test_logger.info(error_msg)
            tool_output += error_msg + "\n"
            summary += "Unexpected error occurred.\n"
            success = False
        else:
            if not os.path.exists(test_output_path):
                tool_output += "Do not generate test_output.txt. Please check the correctness of dockerfile and eval script.\n"
                summary += 'Fail to obtain test results.'
                success = False
            else:
                tool_output += f"Find test_output.txt! Waiting for analysis. "
                summary += 'Obtain test results successfully.'
                success = True

        finally:
           
            # Remove instance container + image, close logger
            cleanup_container(self.client, container,run_test_logger)
            
            remove_image(self.client, test_image_name, run_test_logger)
            close_logger(run_test_logger)

        return tool_output, summary, success


    def setup_docker_and_run_test(
        self
    ) -> tuple[str, str, bool]:
        # building docker image first
       
        dockerfile = self.get_latest_dockerfile()
        
        eval_script = self.get_latest_eval_script()
        tool_output = ""
        summary = ""
        success = False
        self.setup_dockerfile_num += 1
        cur_build_image_dir = f'{self.build_image_dir}_{self.setup_dockerfile_num}'
        os.makedirs(cur_build_image_dir, exist_ok=True)
        build_image_logger = setup_logger(self.task_id, Path(f'{cur_build_image_dir}/build_image.log'))
        # image_name = f"{self.task_id}:latest_{self.setup_dockerfile_num}"
        image_name = f"{self.task_id}-dockerfile{self.setup_dockerfile_num}:latest"
        container = None
        try:
            self.build_docker_image(dockerfile,
                                    cur_build_image_dir,
                                    self.setup_dockerfile_num,
                                    self.task_id, 
                                    image_name,
                                    build_image_logger,
                                    self.client) 
            tool_output += "Image built successfully!\n"
            summary += f"Docker image {image_name} built successfully.\n"
        except docker.errors.BuildError as e:
            # 捕获构建错误，返回 last_command 后的所有信息

            #truncate log
            tool_output += "\n".join(e.build_log)
            build_image_logger.error(e)
            summary += f"Failed to build Docker image."
            success = False
            return tool_output, summary, success
        except Exception as e:
            # 捕获其他意外错误
            build_image_logger.error(f"Unexpected error: {str(e)}")
            tool_output += f'str(e)\n'
            summary += f"Unexpected error when building images."
            success = False
            return tool_output, summary, success
        finally:
            close_logger(build_image_logger)

        test_output, test_summary, test_success = self.run_test(eval_script)
        tool_output += test_output
        summary += test_summary
        success = test_success

        return tool_output, summary, success
    
    # def planning_from


    def get_repository_basic_info(self) -> str:
        repo_name = self.task.repo_name
        commit_sha = self.task.commit
        version = self.task.version
        repo_basic_info = f"Target repository name: {repo_name} Commit Sha: {commit_sha} Version: {version}\nTarget Test files to run:\n {'\n'.join(self.test_files)}\n\n"
        return repo_basic_info

    def init_write_dockerfile_agent_msg_thread(self) -> str:
        self.write_dockerfile_agent_msg_thread = MessageThread()
        self.write_dockerfile_agent_msg_thread.add_system(agent_write_dockerfile.get_system_prompt_dockerfile())
        self.write_dockerfile_agent_msg_thread.add_user(self.repo_basic_info)
        if self.reference_setup:
            reference_version = self.reference_setup['version']
            reference_dockerfile = self.reference_setup['dockerfile']
            reference_text = (
                f"A Dockerfile from a closely related version ({reference_version}) of the same repository "
                f"successfully set up the environment. You can refer to it as guidance:\n\n"
                f"Dockerfile:\n{reference_dockerfile}"
            )
            self.write_dockerfile_agent_msg_thread.add_user(reference_text)
       # msg_thread.add_user(retrieved_context_summary)

    def init_write_eval_script_agent_msg_thread(self) -> str:
        self.write_eval_script_agent_msg_thread = MessageThread()
        self.write_eval_script_agent_msg_thread.add_system(agent_write_dockerfile.get_system_prompt_eval_script())
        self.write_eval_script_agent_msg_thread.add_user(self.repo_basic_info)
        if self.reference_setup:
            reference_version = self.reference_setup['version']
            reference_eval_script = self.reference_setup['eval_script']
            reference_text = (
                f"A Eval script from a closely related version ({reference_version}) of the same repository "
                f"successfully run the target tests. You can refer to it as guidance:\n\n"
                f"Dockerfile:\n{reference_eval_script}"
            )
            self.write_eval_script_agent_msg_thread.add_user(reference_text)
        # msg_thread.add_user(retrieved_context_summary)

    

    def init_context_retrieval_agent_msg_thread(self):
        self.context_retrieval_agent_msg_thread = MessageThread()
#         SYSTEM_PROMPT = """You are a software developer who is skilled at setting up the environment for a repository.
# Your ultimate goal is to generate a Dockerfile that first clones the repository locally, and then sets up an environment that ensures tests can be run.
# First, you should gather enough information about how to set up the environment for the current repository.
# You are currently in the root directory of this repository."""
        SYSTEM_PROMPT=SYSTEM_PROMPT = """You are a repository maintainer responsible for ensuring that new pull requests can be properly tested.  
A developer has submitted a pull request containing a **test patch**, which introduces or modifies test cases.  
Before running the tests, we need to ensure that the environment is correctly set up.  

Your role is to **gather all necessary information** about the repository's environment and testing setup.  
This information will be used to generate:
- A **Dockerfile** that correctly configures the environment.
- An **evaluation script** that executes the provided test files.

### What you need to do:
1. **Understand the environment setup**  
   - Identify required dependencies (e.g., `pip`, `conda`, `npm`, `apt`, `yum`).  
   - Find out language versions (e.g., Python 3.9, Node.js 18, Java 17).  
   - Look for configuration files (`.env`, environment variables, setup scripts).  
   - Review existing setup files (`pow.xml`, `setup.py`, CI/CD config, etc.).  
   - Check OS requirements (We use Linux here, pay more attention to ).  

2. **Determine how to execute the tests**  
   - You will be provided with a list of test files that need to be run.  
   - Find out the correct way to execute these tests (e.g., `pytest tests/test_x.py`, `npm test`, `make test`).  
   - Identify any required setup steps before running the tests (e.g., database migrations, service startups).  

3. **Provide structured information**  
   - Organize your findings so that other agents can use them to generate the Dockerfile and evaluation script.  

### Important Notes:
- The repository has already been **cloned locally**; you are working within the local repository directory.  
- **The pull request has NOT been applied yet**, so you should analyze the repository in its current state.  
- **Focus on what is needed to set up the environment and run the tests successfully.**  

Start by checking the repository structure, configuration files, and dependency manifests.  
Your objective is to ensure that the necessary environment is in place and that the test files can be executed reliably in an isolated container."""

        self.context_retrieval_agent_msg_thread.add_system(SYSTEM_PROMPT)
        self.context_retrieval_agent_msg_thread.add_user(self.repo_basic_info)
        prompt = (
        "Your task is to gather sufficient context from the repository and external sources to understand how to set up the project's environment. To achieve this, you can use the following APIs to browse and extract relevant information:"
        "\n- browse_folder(path: str, depth: str): Browse and return the folder structure for a given path in the repository.  The depth is a string representing a number of folder levels to include in the output such as ``1''. "
        "\n- browse_file_for_environment_info(file_path: str): Browse a file such as README or CONTRIBUTING.md and extract environment setup information."
        "\n- browse_webpage_for_environment_info(url: str): Fetch a web page and extract environment setup information."
        "\n- search_files_by_keyword(keyword: str): Search for files in the repository whose names contain the given keyword."
        "\n\nYou may invoke multiple APIs in one round as needed to gather the required information."
        "\n\nNow analyze the repository and use the necessary APIs to gather the information required to understand and set up the environment. Ensure each API call has concrete arguments as inputs."
        )
        self.context_retrieval_agent_msg_thread.add_user(prompt)
        root_structure_info = f'Structure of root directory: {self.root_structure}\n\n'
        self.context_retrieval_agent_msg_thread.add_user(root_structure_info)
        self.context_retrieval_num += 1
        context_retrieval_output_dir = self.get_latest_context_retrieval_output_dir()
        os.makedirs(context_retrieval_output_dir, exist_ok=True)



    def init_test_log_analysis_agent_msg_thread(self):
        self.test_log_analysis_agent_msg_thread = MessageThread()
        # SYSTEM_PROMPT = """You are an expert on analyzing test log. 
        # Now a devloper wants to run some tests in the repository, 
        # he write a dockerfile to set up the environment and a eval script to run given tests. 
        # First, Your goal is to analyze whether tagert tests are run correctly. 
        # Second, if the tests are not run correctly, 
        # please analyze whether there is some problem with the dockerfile and eval script. 
        # After that, you can call others experts to modify the dockerfile and eval script.
        # This expert includes: (1) context retrieval agent, (2) write dockerfile agent, (3) write eval script agent
        # """
#         SYSTEM_PROMPT = """You are an expert in analyzing test logs and debugging test execution.  
# Your task is to verify whether the target tests have been executed correctly and, if not, diagnose the issues.

# You will:
# 1. Analyze the test log to check if the target tests were executed and whether they passed.
# 2. If the tests did not run correctly, determine whether the issue is related to:
#    - The **Dockerfile** (environment setup issues)
#    - The **evaluation script** (test execution issues)
#    - Missing information that needs to be collected
# 3. Based on your analysis, provide **clear guidance** to the appropriate agent:
#    - `write_dockerfile_agent`
#    - `write_eval_script_agent`
#    - `context_retrieval_agent`
#    - `web_search_agent`

# Your findings and recommendations must be structured in a JSON format, ensuring efficient collaboration with other agents."""
        if self.enable_web_search:
            self.test_log_analysis_agent_msg_thread.add_system(agent_analyze_test_log.SYSTEM_PROMPT_WIT_WEB_SEARCH)
        else:
            self.test_log_analysis_agent_msg_thread.add_system(agent_analyze_test_log.SYSTEM_PROMPT)
        self.test_log_analysis_agent_msg_thread.add_user(self.repo_basic_info)
        # test_log = display_test_log_with_line_numbers(test_log)
        self.test_log_analysis_agent_msg_thread.add_user(f'Target test files:\n{self.test_files}\n\n')
        
        # msg_thread.add_user(f'Test log:\n{test_log}\n\n')
    
    def init_web_search_agent_msg_thread(self):
        self.web_search_agent_msg_thread = MessageThread()
        self.web_search_agent_msg_thread.add_system(agent_web_search.SYSTEM_PROMPT)
        self.web_search_num += 1


    def get_test_log_with_line_numbers(self):
        test_log = self.get_latest_test_log()
        lines = test_log.splitlines()
        
        # 先生成完整的带行号日志
        width = len(str(len(lines)))
        full_formatted = [f"{i + 1:>{width}}   {line}" for i, line in enumerate(lines)]
        
        if len(full_formatted) <= MAX_LINE_NUM:
            return f'Test log:\n{"\n".join(full_formatted)}\n\n'
        
        head_size = MAX_LINE_NUM // 2
        tail_size = MAX_LINE_NUM - head_size
        
        head = full_formatted[:head_size]
        tail = full_formatted[-tail_size:]
        
        # 更详细的省略提示（保持对齐）
        omission = " " * width + "   [..., {} lines omitted ...]".format(
            len(full_formatted) - head_size - tail_size)
        
        truncated_log = "\n".join(head + [omission] + tail)
        
        return f'Test log (showing first {head_size} & last {tail_size} lines):\n{truncated_log}\n\n'


    def analyze_test_log(
        self,
        message_thread: MessageThread,
        print_callback: Callable[[dict], None] | None = None,
    ) -> tuple[str, str, bool]:
        """
        """
        success =False
        tool_output = agent_analyze_test_log.run_with_retries(message_thread,print_callback=print_callback)
        # tool_output = agent_write_patch.run_with_retries(
        #     message_thread,
        #     self.output_dir,
        #     self.task,
        #     # self.validator,
        #     print_callback=print_callback,
        # )
        if tool_output is None:
            summary = "The tool returned nothing. The main agent probably did not provide enough clues."
            success = False
        else:
            summary = "The tool returned the selected search APIs in json format generated by another agent."
            success = True
        # The return status of write_patch does not really matter, so we just use True here
        return tool_output, summary, success

    
    def web_search(
        self,
        message_thread: MessageThread,
        print_callback: Callable[[dict], None] | None = None,
    ) -> tuple[str, str, bool]:
        success =False
        web_search_results = ''
        initial_messages = deepcopy(message_thread.messages)

        query = agent_web_search.run_query_generation(message_thread)
        if not query:
            return "Search failure", 'Search failure', False
        search_results = agent_web_search.search_query(message_thread)
        message_thread.add_user(search_results)

        chosen_links = agent_web_search.run_choose_webpage(message_thread)

        
        web_search_output_dir = self.get_latest_web_search_output_dir()
        for idx,link in enumerate(chosen_links):
            try:
                
                messages = deepcopy(initial_messages)
                webpage_browse_message_thread: MessageThread = MessageThread(messages=messages)
                webpage_browse_message_thread = agent_common.replace_system_prompt(webpage_browse_message_thread, agent_web_search.WEBPAGE_SYSTEM_PROMPT)
                
                webpage_content = agent_web_search.get_jinaai_content(link)
                webpage_browse_message_thread.add_user(webpage_content)
                collected_information = agent_web_search.run_browse_webpage(webpage_browse_message_thread)
                web_search_results += f'Collected information: {collected_information}\n\n'
                conversation_file = pjoin(web_search_output_dir, f"conversation_browse_webpage_{idx}.json")
                webpage_browse_message_thread.save_to_file(conversation_file)
            except Exception as e:
                logger.error(e)
                continue 
        
        # tool_output = agent_write_patch.run_with_retries(
        #     message_thread,
        #     self.output_dir,
        #     self.task,
        #     # self.validator,
        #     print_callback=print_callback,
        # )
        if web_search_results is None:
            summary = "The tool returned nothing. The main agent probably did not provide enough clues."
            success = False
        else:
            summary = "The tool returned the selected search APIs in json format generated by another agent."
            success = True
        # The return status of write_patch does not really matter, so we just use True here
        return web_search_results, summary, success
    
    def dump_cost(
        self
    ):
        start_time = self.start_time
        end_time = datetime.now()
        task_output_dir = self.output_dir
        project_path  = self.task.project_path
        # with apputils.cd(project_path):
        #     commit_hash = apputils.get_current_commit_hash()
        model_stats = common.SELECTED_MODEL.get_overall_exec_stats()
        stats = {
            # "commit": commit_hash,
            "start_epoch": start_time.timestamp(),
            "end_epoch": end_time.timestamp(),
            "elapsed_seconds": (end_time - start_time).total_seconds(),
        }
        stats.update(model_stats)

        with open(pjoin(task_output_dir, "cost.json"), "w") as f:
            json.dump(stats, f, indent=4)