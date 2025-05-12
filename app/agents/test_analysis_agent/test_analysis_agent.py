from pathlib import Path
import os
from loguru import logger
from app.agents.agent import Agent
from app.data_structures import FunctionCallIntent, MessageThread
from app.task import Task
from app.agents.test_analysis_agent import test_analysis_utils
from app.api.docker_utils import (
    cleanup_container,
    remove_image,
    copy_to_container,
    exec_run_with_timeout,
    BuildImageError,
    build_container,
    EvaluationError)
import docker
import re
from app.log import log_exception,setup_logger,close_logger
from app.log import (
    print_acr,
    print_banner,
    print_retrieval,
)
from os.path import join as pjoin
import traceback
MAX_LINE_NUM = 2000
ansi_escape = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
class TestAnalysisAgent(Agent):
    """
    Agent responsible for:
      1. Loading the latest test_output.txt
      2. Formatting it with line numbers and truncation
      3. Sending it to the test-log-analysis utility (agent_analyze_test_log)
    """
    api_functions = ["setup_docker_and_run_test"]

    def __init__(self, task: Task, output_dir: str, repo_basic_info: str, client:docker.DockerClient):
        super().__init__(agent_id=self.__class__.__name__)
        self.msg_thread  = MessageThread()
        self.task = task
        self.output_dir = os.path.abspath(output_dir)
        self.analysis_count = 0
        self.run_test_num = 0
        self.setup_dockerfile_num = 0
        self.repo_basic_info = repo_basic_info
        self.task_id = task.task_id.lower()
        self.client = client
        self.build_image_dir = os.path.join(self.output_dir, "build_image") 
        self.run_test_dir = os.path.join(self.output_dir, "run_test") 
        self.eval_script_skeleton = None
        self.dockerfile = None
        self.eval_script = None
        self.timeout = 3600
        self.init_msg_thread()



    def init_msg_thread(self) -> None:
        """
        Reset the message thread and inject the base prompt before each run_task.
        """
        self.msg_thread = MessageThread()
        # Choose the appropriate system prompt
        # if getattr(agent_analyze_test_log, "SYSTEM_PROMPT_WIT_WEB_SEARCH", None) and getattr(self.task, 'enable_web_search', False):
        #     self.msg_thread.add_system(test_analysis_utils.SYSTEM_PROMPT_WIT_WEB_SEARCH)
        # else:
        self.add_system_message(test_analysis_utils.SYSTEM_PROMPT)
        # Inject repository basic information
        self.add_user_message(self.repo_basic_info)

    def get_latest_run_test_output_dir(self):
        output_dir = f'{self.run_test_dir}_{self.run_test_num}'
        return output_dir

    def get_latest_test_log(self) -> str:
        """Read the latest test_output.txt produced by run_test."""
        test_dir = self.get_latest_run_test_output_dir()
        path = os.path.join(test_dir, "test_output.txt")
        try:
            return Path(path).read_text()
        except FileNotFoundError:
            return ""
    

    def get_test_log_with_line_numbers(self) -> str:
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

    def run_task(self, print_callback=None) -> tuple[str, str, bool]:
        """
        2. Read and format the test log
        3. Add formatted log to the message thread
        4. Invoke agent_analyze_test_log.run_with_retries(...)
        5. Return (tool_output, summary, ok)
        """
        print_banner(f"Iteration ROUND {self.iteration_num} Try to setup docker and run tests ")
        self.init_msg_thread()
        self.analysis_count += 1
        intent = FunctionCallIntent("setup_docker_and_run_test", {}, None)
        tool_output, _, success = self.dispatch_intent(intent)
        if 'Image built successfully!' not in tool_output:
            # fail to build image. go to dockefile refine.
            print_acr(
                'Build Image Failure!',
                f"Iteration ROUND {self.iteration_num}  test analysis",
                print_callback=print_callback,
            )
            error_in_building_dockerfile = f'We can not run tests successfully, cause we encounter some errors when building dockerfile. As follows:\n{tool_output}\n\n'
            self.add_user_message(error_in_building_dockerfile)
        elif success:
            print_acr(
                'Build Image Successfully!',
                f"Iteration ROUND {self.iteration_num}  test analysis ",
                print_callback=print_callback,
            )
            test_log = self.get_test_log_with_line_numbers()
            self.add_user_message(f'Eval script (We omit details of test patch):\n{self.eval_script_skeleton}\n\n')
            self.add_user_message(test_log)
        else:
            logger.error(tool_output)
            logger.error('some problem in running tests')
            return None, f'{self.agent_id} fails, somt error happens', False
        # if we judge that we achieve the goal, terminate the process
        # if test log show that it fails, we go to plan for futrure directions
        # judge whether achieve the goal, if not planning for the work in the next stage.
        print_acr(
                f'Iteration ROUND {self.iteration_num}  Try to analyze the test log ',
                f"Iteration ROUND {self.iteration_num}  test analysis round ",
                print_callback=print_callback,
            )
            
        success =False
        analysis = test_analysis_utils.run_with_retries(self.msg_thread,print_callback=print_callback)
        task_output = analysis
        if task_output is None:
            summary = "The tool returned nothing. The main agent probably did not provide enough clues."
            success = False
        else:
            summary = "The tool returned the selected search APIs in json format generated by another agent."
            success = True

        test_log_output_dir = self.get_latest_run_test_output_dir()
        os.makedirs(test_log_output_dir,exist_ok=True)
        #need to save analysis into json file
        conversation_file = pjoin(test_log_output_dir, f"conversation.json")
        self.msg_thread.save_to_file(conversation_file)
        return task_output, summary, success 

    
       

    def build_docker_image(
        self,
        dockerfile,
        cur_build_image_dir,
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
        if self.setup_dockerfile_num > 1:
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

    def setup_docker_and_run_test(
        self
    ) -> tuple[str, str, bool]:
        # building docker image first
       
        dockerfile = self.dockerfile
        
        eval_script = self.eval_script
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
            tool_output += f'{str(e)}\n'
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

    def run_test(self, eval_script: str) -> (str, str, bool):
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