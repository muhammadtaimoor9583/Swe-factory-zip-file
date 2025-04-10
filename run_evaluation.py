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
ansi_escape = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")

def build_docker_image(
    # self,
    dockerfile,
    cur_build_image_dir,
    # setup_dockerfile_num,
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
    # if setup_dockerfile_num > 1:
        # prev_image_name = f"{task_id}:latest_{setup_dockerfile_num - 1}"
        # prev_image_name = f"{self.task_id}-dockerfile{self.setup_dockerfile_num-1}:latest"
    try:
        client.images.remove(image_name, force=True)
        build_image_logger.info(f"Deleted previous image: {image_name}")

    except docker.errors.ImageNotFound:
        build_image_logger.info(f"Do not find previous image, images list is clean.")
    except Exception as e:  # 捕获所有异常，确保继续执行
        build_image_logger.error(f"Failed to delete previous image {image_name}: {str(e)}")

    

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



def run_test(eval_script: str,cur_test_dir:str,task_id:str,patch:str,client) -> tuple[str | None, str, list[MessageThread]]:
    # tool_output = ""
    # summary = ""
    # success = False
    # patch = self.task.patch
    # self.run_test_num += 1
    # cur_test_dir = f'{self.run_test_dir}_{self.run_test_num}'
    os.makedirs(cur_test_dir, exist_ok=True)
    run_test_logger = setup_logger(task_id, Path(f'{cur_test_dir}/run_test.log'))
    # test_image_name = f"{self.task_id}:latest_{self.setup_dockerfile_num}"
    test_image_name = f"{task_id}-dockerfile:latest"
    # test_container_name =  f"{self.task_id}:test_{self.run_test_num}"
    test_container_name = f"{task_id}-test"
    instance_id = task_id
    container = None
    test_output_path = f'{cur_test_dir}/test_output.txt'
    try:
        container = build_container(client,test_image_name,test_container_name,instance_id,run_test_logger)

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
    
                # Get git diff before running eval script
        git_diff_output_before = (
            container.exec_run("git diff", workdir="/testbed").output.decode("utf-8").strip()
        )
        run_test_logger.info(f"Git diff before:\n{git_diff_output_before}")

        eval_file = Path(f"{cur_test_dir}/eval.sh")
        eval_file.write_text(eval_script)
        run_test_logger.info(
            f"Eval script for {instance_id} written to {patch_file}, now applying to container..."
        )
        copy_to_container(container, eval_file, Path("/eval.sh"))

        # Run eval script, write output to logs
        result = exec_run_with_timeout(container, "/bin/bash /eval.sh", timeout=3600)
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
            

    except EvaluationError as e:
        error_msg = (f"EvaluationError {instance_id}: {e}\n"
                    f"{traceback.format_exc()}\n"
                    f"Check ({run_test_logger.log_file}) for more information.")
        run_test_logger.info(error_msg)
        
        
    except Exception as e:
        error_msg = (f"Error in evaluating model for {instance_id}: {e}\n"
                    f"{traceback.format_exc()}\n"
                    f"Check ({run_test_logger.log_file}) for more information.")
        run_test_logger.info(error_msg)
        
    

    finally:
        
        # Remove instance container + image, close logger
        cleanup_container(client, container,run_test_logger)
        
        remove_image(client, test_image_name, run_test_logger)
        close_logger(run_test_logger)



def setup_docker_and_run_test(
    task_id:str,dockerfile:str,eval_script:str,output_path:str,client
) -> tuple[str, str, bool]:
    # building docker image first
    


    output_dir = f'{output_dir}/{task_id}'
    os.makedirs(output_dir , exist_ok=True)
    build_image_logger = setup_logger(task_id, Path(f'{output_dir}/build_image.log'))
    # image_name = f"{self.task_id}:latest_{self.setup_dockerfile_num}"
    image_name = f"{task_id}-dockerfile:latest"
    container = None
    try:
        build_docker_image(dockerfile,
                                output_dir,
                                # self.setup_dockerfile_num,
                                task_id, 
                                image_name,
                                build_image_logger,
                                client) 
        
    except docker.errors.BuildError as e:
        # 捕获构建错误，返回 last_command 后的所有信息

        #truncate log
    
        build_image_logger.error(e)

    except Exception as e:
        # 捕获其他意外错误
        build_image_logger.error(f"Unexpected error: {str(e)}")
        
    finally:
        close_logger(build_image_logger)

    test_output, test_summary, test_success = run_test(eval_script)
    tool_output += test_output
    summary += test_summary
    success = test_success

    return tool_output, summary, success





