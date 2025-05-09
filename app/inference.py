import inspect
import json
import re
from collections.abc import Callable
from os.path import join as pjoin
from pathlib import Path

from loguru import logger
from termcolor import colored
import os
from app import globals
from app.api.manage import ProjectApiManager
from app.data_structures import FunctionCallIntent, MessageThread
from app.log import (
    log_and_cprint,
    log_and_print,
    print_acr,
    print_banner,
    print_issue,
    print_retrieval,
)
from app.model import common, ollama
from app.repoBrowse.repo_browse_manage import RepoBrowseManager
from app.utils import parse_function_invocation

# FIXME: the system prompt should be different for stratified/state machine.
SYSTEM_PROMPT = """You are a software developer who is skilled at setting up the environment for a repository.
Your ultimate goal is to generate a Dockerfile that first clones the repository locally, and then sets up an environment that ensures tests can be run.
First, you should gather enough information about how to set up the environment for the current repository.
You are currently in the root directory of this repository."""



def prepare_issue_prompt(problem_stmt: str) -> str:
    """
    Given the raw problem statement, sanitize it and prepare the issue prompt.
    Args:
        problem_stmt (str): The raw problem statement.
            Assumption: the problem statement is the content of a markdown file.
    Returns:
        str: The issue prompt.
    """
    # remove markdown comments
    problem_wo_comments = re.sub(r"<!--.*?-->", "", problem_stmt, flags=re.DOTALL)
    content_lines = problem_wo_comments.split("\n")
    # remove spaces and empty lines
    content_lines = [x.strip() for x in content_lines]
    content_lines = [x for x in content_lines if x != ""]
    problem_stripped = "\n".join(content_lines)
    # add tags
    result = "<issue>" + problem_stripped + "\n</issue>"
    return result


def add_step_trigger(orig_prompt: str, is_first: bool = False) -> str:
    """
    Given the original prompt, add the trigger question for the next step.
    Args:
        orig_prompt (str): The original prompt.
        is_first (bool): Whether the trigger is for the first step.
    Returns:
        str: The prompt with trigger question.
    """
    if is_first:
        trigger = "What is the first step?"
    else:
        trigger = "What's the next step to complete the task? Be reminded that you are solving the initial issue."
    return orig_prompt + "\n" + trigger


def start_conversation_round_stratified(
    output_dir: str,
    # msg_thread: MessageThread,
    api_manager: ProjectApiManager,
    start_round_no: int = 0,
    print_callback: Callable[[dict], None] | None = None,
) -> bool:
    """
    This version uses json data to process API calls, instead of using the OpenAI function calling.
    Advantage is that multiple API calls can be made in a single round.
    """
    # prompt = (
    #     "Your task is to gather sufficient context from the repository and external sources to understand how to set up the project's environment. To achieve this, you can use the following APIs to browse and extract relevant information:"
    #     "\n- browse_folder(path: str, depth: str): Browse and return the folder structure for a given path in the repository.  The depth is a string representing a number of folder levels to include in the output such as ``1''. "
    #     "\n- browse_file_for_environment_info(file_path: str): Browse a file such as README or CONTRIBUTING.md and extract environment setup information."
    #     "\n- browse_webpage_for_environment_info(url: str): Fetch a web page and extract environment setup information."
    #     "\n- search_files_by_keyword(keyword: str): Search for files in the repository whose names contain the given keyword."
    #     "\n\nYou may invoke multiple APIs in one round as needed to gather the required information."
    #     "\n\nNow analyze the repository and use the necessary APIs to gather the information required to understand and set up the environment. Ensure each API call has concrete arguments as inputs."
    # )
    # msg_thread.add_user(prompt)
    # api_manager.context_retrieval_num += 1
    api_manager.init_context_retrieval_agent_msg_thread()
    api_manager.init_web_search_agent_msg_thread()
    api_manager.init_write_eval_script_agent_msg_thread()
    api_manager.init_write_dockerfile_agent_msg_thread()
    round_no = start_round_no

    round_count = range(start_round_no, globals.conv_round_limit )

    try_generate_locs = False
    if globals.disable_patch_generation:
        round_count = range(
            start_round_no, start_round_no + globals.context_generation_limit + 1
        )

    for round_no in round_count:
        if  api_manager.get_web_search_agent_status():
            # api_manager.web_search_num += 1
            web_search_output_dir = api_manager.get_latest_web_search_output_dir()
            os.makedirs(web_search_output_dir)
            web_search_intent = FunctionCallIntent("web_search", {}, None)
            # if api_manager.write_dockerfile_num > 0:
            #     mode = 'modify'
            
            print_banner(f"Web Search ROUND {round_no}")
            web_search_results, _, _ = api_manager.dispatch_intent(web_search_intent, api_manager.web_search_agent_msg_thread)
            
            if web_search_results:
                conversation_file = pjoin(web_search_output_dir, f"conversation_round_{round_no}.json")
                api_manager.web_search_agent_msg_thread.save_to_file(conversation_file)
                web_search_results = f'The web search agent collect some useful information for you:\n{web_search_results}\n\n'
                api_manager.write_dockerfile_agent_msg_thread.add_user(web_search_results)
                api_manager.write_eval_script_agent_msg_thread.add_user(web_search_results)
            logger.info(f"Invoked {web_search_intent.func_name}.")
            api_manager.is_web_search = False
            api_manager.init_web_search_agent_msg_thread()
            api_manager.dump_cost()
        if api_manager.get_context_retrieval_agent_status():
            context_retrieval_round  = -1
            while True:
                context_retrieval_round += 1
                api_manager.start_new_tool_call_layer()
                
                context_retrieval_output_dir = api_manager.get_latest_context_retrieval_output_dir()
                # os.makedirs(context_retrieval_output_dir, exist_ok=True)
                # f'{output_dir}/output_context_retrieval_{api_manager.context_retrieval_num}'
                conversation_file = pjoin(context_retrieval_output_dir, f"conversation_{context_retrieval_round}_round_{round_no}.json")
                # save current state before starting a new round
                api_manager.context_retrieval_agent_msg_thread.save_to_file(conversation_file)

                print_banner(f"CONTEXT RETRIEVAL ROUND {round_no}")

                print_acr(
                    # prompt,
                    'context retrieval',
                    f"context retrieval {context_retrieval_round} round {start_round_no}",
                    print_callback=print_callback,
                )
                # get_action
                res_text, *_ = common.SELECTED_MODEL.call(api_manager.context_retrieval_agent_msg_thread.to_msg())
                api_manager.context_retrieval_agent_msg_thread.add_model(res_text, tools=[])
                print_retrieval(res_text, f"context retrieval {context_retrieval_round} in round {round_no}", print_callback=print_callback)

                # parse acrtion from response
                selected_apis, _, proxy_threads = api_manager.proxy_apis(res_text)

                proxy_log = Path(context_retrieval_output_dir, f"agent_proxy_{context_retrieval_round}_round_{round_no}.json")
                proxy_messages = [thread.to_msg() for thread in proxy_threads]
                proxy_log.write_text(json.dumps(proxy_messages, indent=4))

                if selected_apis is None:
                    msg = "The repo browsing API calls seem invalid. Please check the arguments you give carefully and try again."
                    api_manager.context_retrieval_agent_msg_thread.add_user(msg)
                    print_acr(
                        msg,
                        f"context retrieval {context_retrieval_round} round {round_no}",
                        print_callback=print_callback,
                    )
                    continue

                selected_apis_json = json.loads(selected_apis)

                json_api_calls = selected_apis_json.get("API_calls", [])
                is_termination = selected_apis_json.get("terminate", None)
                summary_of_collected_information = selected_apis_json.get("collected_information", None)
                if is_termination:
                    msg_summary_of_collected_information = f'Collected information from context retireval agent:\n{summary_of_collected_information}\n\n'
                    api_manager.write_dockerfile_agent_msg_thread.add_user(msg_summary_of_collected_information)
                    api_manager.write_eval_script_agent_msg_thread.add_user(msg_summary_of_collected_information)

                    #  update message thread of context retrieval agent.
                    # api_manager.context_retrieval_num += 1
                    api_manager.is_context_retrieval = False
                    api_manager.init_context_retrieval_agent_msg_thread()
                    
                    break
                formatted = []
                if json_api_calls:
                    formatted.append("API calls:")
                    for call in json_api_calls:
                        formatted.extend([f"\n- `{call}`"])

            
                print_acr(
                    "\n".join(formatted),
                    "Agent-selected API calls",
                    print_callback=print_callback,
                )

                # init observation
                # prepare response from tools
                collated_tool_response = ""
                
                for api_call in json_api_calls:
                    func_name, func_args = parse_function_invocation(api_call)
                    try:
                        arg_spec = inspect.getfullargspec(getattr(RepoBrowseManager, func_name))

                        arg_names = arg_spec.args[1:]  # first parameter is self

                        assert len(func_args) == len(
                            arg_names
                        ), f"Number of argument is wrong in API call: {api_call}"

                        kwargs = dict(zip(arg_names, func_args))
                        intent = FunctionCallIntent(func_name, kwargs, None)
                    except Exception as call_api_e:
                        collated_tool_response += f"Exception when calling {api_call}: {call_api_e}\n\n"
                        continue
                    #action -> obeservation
                    tool_output, _, _ = api_manager.dispatch_intent(intent, api_manager.context_retrieval_agent_msg_thread)
                    # merge observation
                    collated_tool_response += f"Result of {api_call}:\n\n"
                    collated_tool_response += f'{tool_output}\n\n'
                
                # observation -> thought
                api_manager.context_retrieval_agent_msg_thread.add_user(collated_tool_response)
                print_acr(
                    collated_tool_response,
                    f"context retrieval {context_retrieval_round} round {round_no}",
                    print_callback=print_callback,
                )
                # thought
                msg = "Let's analyze collected context first"
                api_manager.context_retrieval_agent_msg_thread.add_user(msg)
                print_acr(
                    msg, f"context retrieval {context_retrieval_round} round {round_no}", print_callback=print_callback
                )
                #thought
                res_text, *_ = common.SELECTED_MODEL.call(api_manager.context_retrieval_agent_msg_thread.to_msg())
                api_manager.context_retrieval_agent_msg_thread.add_model(res_text, tools=[])
                print_retrieval(res_text, f"context retrieval {context_retrieval_round} round  {round_no}", print_callback=print_callback)


                # thought -> action
                if context_retrieval_round < 10:
                    msg = (
                        "Based on your analysis, answer below questions:"
                        "\n- Do you think we collect enough information to write a  dockerfile to setup the environment and write a eval script to run given tests? If yes, please give a summary of the collected information.(leave it empty if you don't collect enough information)"
                        "\n- If we do not collect enough information, what repo browsing API calls we use to get more information. (leave it empty if you don't need more context)"
                    )
                    # if isinstance(common.SELECTED_MODEL, ollama.OllamaModel):
                    #     # llama models tend to always output search APIs and buggy locations.
                    #     msg += "\n\nNOTE: If you have already identified the bug locations, do not make any search API calls."
                    api_manager.context_retrieval_agent_msg_thread.add_user(msg)
                    print_acr(
                        msg,
                        f"context retrieval {context_retrieval_round} round {round_no}",
                        print_callback=print_callback,
                    )
                else:
                    break
        api_manager.dump_cost()
        if api_manager.get_write_dockerfile_agent_status():
            
            
            mode = None
            api_manager.start_new_tool_call_layer()
            dockerfile_intent = FunctionCallIntent("write_dockerfile", {}, None)
            # if api_manager.write_dockerfile_num > 0:
            #     mode = 'modify'
             
            print_banner(f"Dockerfile generation ROUND {round_no}")
            api_manager.dispatch_intent(dockerfile_intent, api_manager.write_dockerfile_agent_msg_thread)
            dockerfile_output_dir = api_manager.get_latest_write_dockerfile_output_dir()
            conversation_file = pjoin(dockerfile_output_dir, f"conversation_round_{round_no}.json")
            api_manager.write_dockerfile_agent_msg_thread.save_to_file(conversation_file)
            logger.info(f"Invoked {dockerfile_intent.func_name}.")
            
        api_manager.dump_cost()
        if api_manager.get_write_eval_script_agent_status():
            # mode = None
            api_manager.start_new_tool_call_layer()
            eval_script_intent = FunctionCallIntent("write_eval_script", {}, None)
            # if api_manager.write_eval_script_num > 0:
            #     mode = 'modify'
            
            print_banner(f"Eval script generation ROUND {round_no}")
            api_manager.dispatch_intent(eval_script_intent, api_manager.write_eval_script_agent_msg_thread)
            script_output_dir = api_manager.get_latest_write_eval_script_output_dir()
            conversation_file = pjoin(script_output_dir, f"conversation_round_{round_no}.json")
            api_manager.write_eval_script_agent_msg_thread.save_to_file(conversation_file)
            logger.info(f"Invoked {eval_script_intent.func_name}.")

        api_manager.dump_cost()
        if api_manager.get_run_test_agent_status():
            print_banner(f"Try to setup docker and run tests ROUND {round_no}")
            api_manager.init_test_log_analysis_agent_msg_thread()
            api_manager.test_log_analysis_num += 1
            intent = FunctionCallIntent("setup_docker_and_run_test", {}, None)
            tool_output, _, success = api_manager.dispatch_intent(intent,api_manager.test_log_analysis_agent_msg_thread)
            if 'Image built successfully!' not in tool_output:
                # fail to build image. go to dockefile refine.
                print_acr(
                    'Build Image Failure!',
                    f"test analysis round {round_no}",
                    print_callback=print_callback,
                )
                error_in_building_dockerfile = f'We can not run tests successfully, cause we encounter some errors when building dockerfile. As follows:\n{tool_output}\n\n'
                api_manager.test_log_analysis_agent_msg_thread.add_user(error_in_building_dockerfile)
            elif success:
                print_acr(
                    'Build Image Successfully!',
                    f"test analysis round {round_no}",
                    print_callback=print_callback,
                )
                test_log = api_manager.get_test_log_with_line_numbers()
                api_manager.test_log_analysis_agent_msg_thread.add_user(f'Eval script:\n{api_manager.get_latest_eval_script()}\n\n')
                api_manager.test_log_analysis_agent_msg_thread.add_user(test_log)
            else:
                logger.error(tool_output)
                logger.error('some problem in running tests')
                continue
            # if we judge that we achieve the goal, terminate the process
            # if test log show that it fails, we go to plan for futrure directions
            # judge whether achieve the goal, if not planning for the work in the next stage.
            print_acr(
                    f'Try to analyze the test log {round_no}',
                    f"test analysis round {round_no}",
                    print_callback=print_callback,
                )
            intent = FunctionCallIntent("analyze_test_log", {}, None)
            analysis, _, success = api_manager.dispatch_intent(intent,api_manager.test_log_analysis_agent_msg_thread)
            # analysis = api_manager.analyze_test_log()
            
            test_log_output_dir = api_manager.get_latest_test_log_analysis_output_dir()
            os.makedirs(test_log_output_dir,exist_ok=True)
            conversation_file = pjoin(test_log_output_dir, f"conversation_round_{round_no}.json")
            api_manager.test_log_analysis_agent_msg_thread.save_to_file(conversation_file)

            if analysis is None:
                msg = "The test analyze API calls seem invalid. Please check the arguments you give carefully and try again."
                api_manager.test_log_analysis_agent_msg_thread.add_user(msg)
                print_acr(
                    # msg,
                    f"Get the test log analysis successfully! Plan for the next work.",
                    f"test analysis round {round_no}",
                    print_callback=print_callback,
                )
                continue

            analysis = json.loads(analysis)

            is_finish = analysis.get("is_finish", None)
            if is_finish:
                api_manager.is_finish = True
                break
            
            # write dockerile + eval script + build contaier + run eval script
            # collect feedback (image error + test error)
            # image error: 1. modify dockerfile directly
            #              2. go to context retrieval  agent for more information.
            # test error: 1. go to modfiy dockerfile.
            #             2. or go to collect more information

            # scheduler
            guidance_for_context_retrieval_agent = analysis.get("guidance_for_context_retrieval_agent", None)
            if guidance_for_context_retrieval_agent:
                api_manager.is_context_retrieval = True
                api_manager.context_retrieval_agent_msg_thread.add_user(f'After setting up dockerfile and running tests, the test log analysis agent find that there is other context information need to collect. Here is his analysis:\n{guidance_for_context_retrieval_agent}\n\n')

            if api_manager.enable_web_search:
                guidance_for_web_search_agent = analysis.get("guidance_for_web_search_agent", None)
                if guidance_for_web_search_agent:
                    api_manager.is_web_search= True
                    api_manager.web_search_agent_msg_thread.add_user(f'After setting up dockerfile and running tests, the test log analysis agent find that there is other context information need to collect from the web. Here is his analysis:\n{guidance_for_web_search_agent}\n\n')


            guidance_for_write_dockerfile_agent = analysis.get("guidance_for_write_dockerfile_agent", None)
            if guidance_for_write_dockerfile_agent:
                api_manager.is_write_dockerfile = True
                api_manager.write_dockerfile_agent_msg_thread.add_user(f'After setting up dockerfile and running tests, the test log analysis agent find that there is a problem with dockefile. Here is his analysis:\n{guidance_for_write_dockerfile_agent}\n\n')

            guidance_for_write_eval_script_agent = analysis.get("guidance_for_write_eval_script_agent", None)
            if guidance_for_write_eval_script_agent:
                api_manager.is_write_eval_script = True
                api_manager.write_eval_script_agent_msg_thread.add_user(f'After setting up dockerfile and running tests, the test log analysis agent find that there is a problem with eval script. Here is his analysis:\n{guidance_for_write_eval_script_agent}\n\n')


        api_manager.dump_cost()
    else:
        log_msg = "Exceed largest number of tries.."
        logger.info(f"Too many rounds. {log_msg}")



    dockerfile_content = api_manager.get_latest_dockerfile()
    eval_script_content = api_manager.get_latest_eval_script()

    if dockerfile_content and eval_script_content:
        with open(os.path.join(output_dir, "Dockerfile"), "w") as dockerfile_f:
            dockerfile_f.write(dockerfile_content)

        # 保存 eval.sh
        with open(os.path.join(output_dir, "eval.sh"), "w") as eval_script_f:
            eval_script_f.write(eval_script_content)


    with open(os.path.join(output_dir, "status.json"), "w") as status_file_f:
            json.dump({"is_finish": api_manager.is_finish}, status_file_f)

    # round_no += 1

    # if not globals.disable_patch_generation:
    #     intent = FunctionCallIntent("write_dockerfile", {}, None)
    # # elif try_generate_locs:
    # #     intent = FunctionCallIntent("propose_locs", {}, None)
    # else:
    #     intent = None

    # if intent:
    
    #     api_manager.start_new_tool_call_layer()
        
    #     api_manager.dispatch_intent(intent, msg_thread, print_callback=print_callback)
       
    #     logger.info(f"Invoked {intent.func_name}.")
    # logger.info("Ending workflow.")
    # conversation_file = pjoin(output_dir, f"conversation_round_{round_no}.json")
    # msg_thread.save_to_file(conversation_file)

    return True






def dump_tool_call_layers_to_file(
    tool_call_layers: list[dict], output_dir: str
) -> None:
    """Dump the layers of tool calls to a file."""
    tool_call_file = pjoin(output_dir, "tool_call_layers.json")
    with open(tool_call_file, "w") as f:
        json.dump(tool_call_layers, f, indent=4)


def start_conversation_round_state_machine(
    output_dir: str,
    msg_thread: MessageThread,
    api_manager: ProjectApiManager,
    start_round_no: int = 0,
) -> bool:
    """
    Start the actual rounds of conversations with model.

    Args:
        output_dir (str): Path to the output directory.
        msg_thread (MessageThread): The message thread to be used.
        api_manager (ProjectApiManager): The API manager to be used.
        start_round_no (int): The round number to start with.
    """
    round_no = start_round_no
    for round_no in range(start_round_no, globals.conv_round_limit + 1):
        conversation_file = pjoin(output_dir, f"conversation_round_{round_no}.json")
        # save current state before starting a new round
        msg_thread.save_to_file(conversation_file)
        log_and_cprint(
            f"\n========== Conversation Round {round_no} ==========", style="red bold"
        )
        log_and_print(f"{colored('Current message thread:', 'green')}\n{msg_thread}")

        allowed_tools = api_manager.next_tools()
        # TODO: configure the list of tools based on state machine
        tools = ProjectApiManager.get_full_funcs_for_openai(allowed_tools)

        log_and_cprint(f"Current tool state: {api_manager.curr_tool}", style="yellow")
        log_and_cprint(f"Allowed next tool states: {allowed_tools}", style="yellow")

        # create a new iteration of conversation
        res_text, raw_tool_calls, func_call_intents, *_ = common.SELECTED_MODEL.call(
            msg_thread.to_msg(), tools=tools
        )
        log_and_print(
            f"{colored('This roud model response (text):', 'blue')} {res_text}"
        )
        # model can decide whether to create a function call
        if len(func_call_intents) == 1:
            # good case in which we can check function call
            func_call_intent: FunctionCallIntent = func_call_intents[0]
            log_and_print(
                f"{colored('This round model response (function call):', 'blue')} {func_call_intent}"
            )
            # dispatch this function call
            this_model_response = res_text
            this_model_tools = raw_tool_calls
            # add previous call information to user message
            tool_output, summary, _ = api_manager.dispatch_intent(
                func_call_intent, msg_thread
            )
        else:
            # no function call, let's force the model to make one
            this_model_tools = []
            this_model_response = res_text
            tool_output = ""
            summary = "There is no function call in your previous response. Make sure you include one function call. "

        next_user_message = add_step_trigger(summary)

        # form message thread for next round. should include what the model said as well
        msg_thread.add_model(this_model_response, this_model_tools)
        if this_model_tools:
            tool_call_id = this_model_tools[0].id
            msg_thread.add_tool(tool_output, tool_call_id)
            msg_thread.add_user(next_user_message)
        else:
            msg_thread.add_user(next_user_message)

        if len(func_call_intents) == 1:
            func_call_name = func_call_intents[0].func_name
            if func_call_name == "write_patch":
                log_and_print("Ending workflow. write_patch has been invoked.")
                break

        log_and_print("Going to next round ..........")
    else:
        log_and_print("Too many rounds. Try writing patch anyway.")
        write_patch_intent = FunctionCallIntent("write_patch", {}, None)
        api_manager.dispatch_intent(write_patch_intent, msg_thread)

    round_no += 1

    # if we end the workflow normally, there is one more round of conversation to store
    conversation_file = pjoin(output_dir, f"conversation_round_{round_no}.json")
    msg_thread.save_to_file(conversation_file)
    return True


def run_one_task(
    output_dir: str,
    api_manager: ProjectApiManager,
    repo_name: str,
    project_path: str,
    commit: str,
    # problem_stmt: str,
    image_urls: list[str],
    print_callback: Callable[[dict], None] | None = None, 
    
) -> bool:
    """
    Main entry point to run inference on one task.
    Args:
        output_dir (str): Path to the output directory.
        api_manager (ProjectApiManager): The already-initialized API manager.
        problem_stmt (str): The original problem statement submitted to the task issue.
    """
    # print_banner("Starting AutoCodeRover on the following issue")
    # print_issue(problem_stmt)
    # msg_thread = MessageThread()

    # system_prompt = SYSTEM_PROMPT
    # if (not globals.enable_layered) and common.SELECTED_MODEL.parallel_tool_call:
    #     # these models support parallel tool calls, let's try to make them not do it
    #     system_prompt += " In your response, DO NOT make more than one tool call."
    # if len(image_urls) != 0:
    #     if 'gpt' in output_dir:
    #         system_prompt += " The problem statement will include image links, and the actual images will be provided below. Please understand these images to resolve the issues."
    #     else:
    #         system_prompt += " The problem statement will include image links, and the actual images will be provided below. Please make sure generated patch can be applied to codebase."
    
    # msg_thread.add_system(system_prompt)
    # original_prompt = f"""Basic information about target repository:
    # - repository name: {repo_name}
    # - commit: {commit}
    # - project path: {project_path}
    # """
    # # original_prompt = prepare_issue_prompt(problem_stmt)
    # msg_thread.add_user(original_prompt)
    # if len(image_urls) != 0:
    #     msg_thread.add_image(image_urls)

    # # Add another user message about fault localization
    # if globals.enable_sbfl:
    #     localization_result, _, _ = api_manager.fault_localization()
    #     localization_prompt = "An external analysis tool has been deployed to identify the suspicious code to be fixed. You can choose to use the results from this tool, if you think they are useful."
    #     localization_prompt += "The tool output is as follows:\n"
    #     localization_prompt += localization_result
    #     msg_thread.add_user(localization_prompt)
    

    if globals.enable_layered:
        return start_conversation_round_stratified(
            output_dir,  api_manager, print_callback=print_callback
        )
    else:
        return start_conversation_round_state_machine(
            output_dir, msg_thread, api_manager
        )


# NOTE: deprecated
def continue_task_from_cache(
    cache_path: str, output_dir: str, api_manager: ProjectApiManager
) -> bool:
    """
    Run inference on one task, but load conversation history from cache.
    Args:
        cache_path (str): Path to the old conversation history file.
        output_dir (str): Path to the output directory.
        api_manager (ProjectApiManager): The already-initialized API manager.
    """
    # (1) load the existing message thread
    msg_thread = MessageThread.load_from_file(cache_path)
    completed_round_no = msg_thread.get_round_number()

    # (2) start the actual workflow
    return start_conversation_round_state_machine(
        output_dir, msg_thread, api_manager, start_round_no=completed_round_no
    )
