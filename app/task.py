from __future__ import annotations

import os
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from os.path import join as pjoin
from tempfile import mkstemp
import shutil
import app.utils as apputils
from app import globals, log
from app import utils as app_utils

from app.log import log_and_print
from docker import DockerClient

class Task(ABC):
    @property
    @abstractmethod
    def project_path(self) -> str:
        raise NotImplementedError("abstract method")

    @abstractmethod
    def get_issue_statement(self) -> str:
        raise NotImplementedError("abstract method")

    @abstractmethod
    def setup_project(self) -> None:
        """Set up the project before starting to resolve the task."""
        raise NotImplementedError("abstract method")

    @abstractmethod
    def reset_project(self) -> None:
        """Reset project to initial state."""
        raise NotImplementedError("abstract method")



@dataclass(kw_only=True)
class SweTask(Task):
    task_id: str
    problem_statement: str
    repo_path: str
    repo_cache_path: str
    commit: str
    env_name: str
    repo_name: str
    pre_install_cmds: list[str]
    install_cmd: str
    test_cmd: str
    patch: str
    test_patch: str
    testcases_passing: list[str]
    testcases_failing: list[str]
    language: str
    image_urls: list[str]
    # reference_setup: dict
    version: str
    client: DockerClient
    task_info: dict
    @property
    def project_path(self) -> str:
        return self.repo_path
    

    @project_path.setter
    def project_path(self, value: str) -> None:
        self.repo_path = value

    def get_issue_statement(self) -> str:
        return self.problem_statement
    
    def get_image_urls(self) -> str:
        return self.image_urls

    def setup_project(self) -> None:
        # get the correct version of the project and commit-specific pip install
        task = self
        with apputils.cd(task.project_path):
            apputils.repo_reset_and_clean_checkout(task.commit)

        # Install task-specific dependencies
        do_install = (
            globals.enable_sbfl
            or globals.enable_validation
            or globals.only_save_sbfl_result
        )
        if do_install:
            self._do_install()

        # apply the test modifications to this task
        self._apply_test_patch()

        # commit the current changes, so that resetting later do not erase them
        with apputils.cd(task.project_path):
            apputils.repo_commit_current_changes()

    def reset_project(self) -> None:
        with apputils.cd(self.repo_path):
            apputils.repo_reset_and_clean_checkout(self.commit)

    def remove_project(self) -> None:
        """Remove the entire project repository."""
        if os.path.exists(self.repo_path):
            shutil.rmtree(self.repo_path)
            log_and_print(f"Removed project repository at {self.repo_path}")

    def _do_install(self):
        """Do left-over install commands after setting up.
        The commands being run here are 'pre_install' and 'install' defined in
        harness/constants.py file in SWE-bench.
        """
        task = self
        if not task.pre_install_cmds and not task.install_cmd:
            # no command for installation, skip
            return
        with apputils.cd(task.project_path):
            # (0) For matplotlib, qhull tarball download
            # just fails, so we need to pre-install the system version and use it
            if "matplotlib" in task.task_id:
                with open("mplsetup.cfg", "w") as f:
                    f.write("[libs]\nsystem_qhull = true")
            # (1) pre-install
            for cmd in task.pre_install_cmds:
                cp = apputils.run_string_cmd_in_conda(
                    cmd, task.env_name, capture_output=True, text=True
                )
                if cp.returncode != 0:
                    log_and_print(cp.stderr)
                    raise RuntimeError(f"Command {cmd} failed.")

            # (2) install
            cp = apputils.run_string_cmd_in_conda(
                task.install_cmd, task.env_name, capture_output=True, text=True
            )
            if cp.returncode != 0:
                log_and_print(cp.stderr)
                raise RuntimeError(f"Command {task.install_cmd} failed.")
            # (3) xmlrunner for our custom run_test; coverage required for fault localization
            other_install_cmd = (
                "python -m pip install xmlrunner coverage pytest pytest-cov"
            )
            cp = apputils.run_string_cmd_in_conda(
                other_install_cmd, task.env_name, capture_output=True, text=True
            )
            if cp.returncode != 0:
                log_and_print(cp.stderr)
                raise RuntimeError(f"Command {other_install_cmd} failed.")

    def _apply_test_patch(self) -> None:
        """
        Apply the patch to testcases, as supplied by the benchmark.
        This step brings in all the new tests and testcase modifications.
        """
        task = self

        if not task.test_patch:
            # no patches to tests are found
            return
        with apputils.cd(task.project_path):
            # (1) write test_patch to a temp file
            test_patch_path = pjoin(task.project_path, "swe_bench_tests.patch")
            with open(test_patch_path, "w") as f:
                f.write(task.test_patch)
            # (2) apply these patches
            # FIXME: check for failure here
            apply_cmd = ["git", "apply", test_patch_path]
            cp = apputils.run_command(apply_cmd, capture_output=True, text=True)
            if cp.returncode != 0:
                log_and_print(cp.stderr)
                raise RuntimeError(f"Command {apply_cmd} failed.")
            # (3) remove the temp file, which is not so important
            os.remove(test_patch_path)



@dataclass(kw_only=True)
class PlainTask(Task):
    """
    Tasks that only contain a codebase and an issue descripion (no test suite).
    """

    commit_hash: str
    local_path: str
    problem_statement: str

    @property
    def project_path(self) -> str:
        return self.local_path

    def setup_project(self) -> None:
        with apputils.cd(self.project_path):
            apputils.repo_reset_and_clean_checkout(self.commit_hash)

    def reset_project(self) -> None:
        with apputils.cd(self.project_path):
            apputils.repo_reset_and_clean_checkout(self.commit_hash)

    def get_issue_statement(self) -> str:
        return self.problem_statement


