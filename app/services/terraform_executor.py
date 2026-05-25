"""
Terraform execution utilities with comprehensive structured logging
"""

import json
import os
import signal
import subprocess
from typing import Any

from ..config import settings
from ..utils.logger import LogCategory, get_logger

logger = get_logger(__name__)


class TerraformExecutor:
    """Executor for Terraform operations with detailed logging"""

    def __init__(self, working_dir: str, env_vars: dict[str, str] | None = None):
        self.working_dir = working_dir
        self.terraform_path = settings.TERRAFORM_PATH
        self.env_vars = env_vars or {}
        self.current_process: subprocess.Popen | None = None

    def _get_env(self, extra_env: dict[str, str] | None = None) -> dict[str, str]:
        """Get environment variables including OpenStack credentials and Terraform debug logging"""
        env = os.environ.copy()
        env.update(self.env_vars)
        if extra_env:
            env.update(extra_env)
        env["TF_LOG"] = "DEBUG"
        # Ensure clouds.yaml is found by Terraform/OpenStack
        if "OS_CLIENT_CONFIG_FILE" not in env:
            env["OS_CLIENT_CONFIG_FILE"] = settings.OPENSTACK_CLOUDS_YAML
        return env

    def terminate(self) -> None:
        """Kill the currently running Terraform subprocess, if any."""
        proc = self.current_process
        if proc and proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                proc.terminate()

    def _run(self, cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
        """Run a command via Popen so we can track and kill the process."""
        proc = subprocess.Popen(
            cmd,
            cwd=self.working_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=self._get_env(),
            start_new_session=True,
        )
        self.current_process = proc
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        finally:
            self.current_process = None
        return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)

    def init(self) -> tuple[bool, str, str]:
        """
        Initialize Terraform in the working directory
        Returns:
            tuple: (success, stdout, stderr)
        """
        logger.operation_start("terraform_init", working_dir=self.working_dir)
        logger.debug(f"[TF] Init: working_dir={self.working_dir}, terraform_path={self.terraform_path}")
        try:
            cmd = [self.terraform_path, "init", "-input=false"]
            logger.debug(f"[TF] Running command: {' '.join(cmd)}")
            result = self._run(cmd, timeout=300)
            logger.debug(f"[TF] Init stdout: {result.stdout}")
            logger.debug(f"[TF] Init stderr: {result.stderr}")
            success = result.returncode == 0

            if result.stdout:
                logger.command_output("terraform_init", result.stdout, result.returncode)

            if not success:
                logger.error(
                    "Terraform init failed",
                    category=LogCategory.ERROR,
                    returncode=result.returncode,
                    stderr=result.stderr[:1000],
                )
            else:
                logger.success("Terraform init completed", category=LogCategory.STATUS)

            logger.operation_end("terraform_init", success)
            return success, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            logger.error("Terraform init timed out after 5 minutes", category=LogCategory.ERROR)
            logger.operation_end("terraform_init", success=False)
            return False, "", "Timeout"
        except Exception as e:
            logger.exception("Terraform init failed with exception", exception=e)
            logger.operation_end("terraform_init", success=False)
            return False, "", str(e)

    def plan(self, var_file: str | None = None, variables: dict[str, Any] | None = None) -> tuple[bool, str, str]:
        """
        Run terraform plan
        Args:
            var_file: Path to tfvars file
            variables: Dictionary of variables to pass
        Returns:
            tuple: (success, stdout, stderr)
        """
        logger.operation_start("terraform_plan", var_file=var_file, var_count=len(variables or {}))
        try:
            cmd = [self.terraform_path, "plan", "-input=false"]
            logger.debug(f"[TF] Running command: {' '.join(cmd)}")
            if var_file:
                cmd.extend(["-var-file", var_file])
            if variables:
                for key, value in variables.items():
                    cmd.extend(["-var", f"{key}={value}"])

            logger.info("Analyzing Terraform configuration...", category=LogCategory.STATUS)

            print(variables)

            result = self._run(cmd, timeout=300)
            logger.debug(f"[TF] Plan stdout: {result.stdout}")
            logger.debug(f"[TF] Plan stderr: {result.stderr}")
            success = result.returncode == 0

            if result.stdout:
                logger.command_output("terraform_plan", result.stdout, result.returncode)

            if not success:
                logger.error(
                    f"Terraform plan failed\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}",
                    category=LogCategory.ERROR,
                    returncode=result.returncode,
                )
            else:
                logger.success("Terraform plan completed", category=LogCategory.STATUS)

            logger.operation_end("terraform_plan", success)
            return success, result.stdout, result.stderr
        except Exception as e:
            logger.exception("Terraform plan failed with exception", exception=e)
            logger.operation_end("terraform_plan", success=False)
            return False, "", str(e)

    def apply(self, var_file: str | None = None, variables: dict[str, Any] | None = None) -> tuple[bool, str, str]:
        """
        Run terraform apply
        Args:
            var_file: Path to tfvars file
            variables: Dictionary of variables to pass
        Returns:
            tuple: (success, stdout, stderr)
        """
        logger.operation_start("terraform_apply", var_file=var_file, var_count=len(variables or {}))
        try:
            cmd = [self.terraform_path, "apply", "-auto-approve", "-input=false"]
            logger.debug(f"[TF] Running command: {' '.join(cmd)}")
            if var_file:
                cmd.extend(["-var-file", var_file])
            if variables:
                for key, value in variables.items():
                    cmd.extend(["-var", f"{key}={value}"])

            logger.info("Applying Terraform configuration (this may take minutes)...", category=LogCategory.STATUS)

            result = self._run(cmd, timeout=1800)
            logger.debug(f"[TF] Apply finished: command={' '.join(cmd)}, returncode={result.returncode}")
            logger.debug(f"[TF] Apply stdout: {result.stdout}")
            logger.debug(f"[TF] Apply stderr: {result.stderr}")
            success = result.returncode == 0

            if result.stdout:
                logger.command_output("terraform_apply", result.stdout, result.returncode)

            if not success:
                logger.error(
                    f"Terraform apply failed\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}",
                    category=LogCategory.ERROR,
                    returncode=result.returncode,
                )
            else:
                logger.success("Terraform apply completed successfully", category=LogCategory.STATUS)

            logger.operation_end("terraform_apply", success)
            return success, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            logger.error("Terraform apply timed out after 30 minutes", category=LogCategory.ERROR)
            logger.operation_end("terraform_apply", success=False)
            return False, "", "Timeout"
        except Exception as e:
            logger.exception("Terraform apply failed with exception", exception=e)
            logger.operation_end("terraform_apply", success=False)
            return False, "", str(e)

    def destroy(self, var_file: str | None = None, variables: dict[str, Any] | None = None) -> tuple[bool, str, str]:
        """
        Run terraform destroy
        Args:
            var_file: Path to tfvars file
            variables: Dictionary of variables to pass
        Returns:
            tuple: (success, stdout, stderr)
        """
        logger.operation_start("terraform_destroy", var_file=var_file, var_count=len(variables or {}))
        try:
            cmd = [self.terraform_path, "destroy", "-auto-approve", "-input=false"]
            logger.debug(f"[TF] Running command: {' '.join(cmd)}")
            if var_file:
                cmd.extend(["-var-file", var_file])
            if variables:
                for key, value in variables.items():
                    cmd.extend(["-var", f"{key}={value}"])

            logger.info("Destroying Terraform resources (this may take minutes)...", category=LogCategory.STATUS)

            result = self._run(cmd, timeout=1800)
            logger.debug(f"[TF] Destroy stdout: {result.stdout}")
            logger.debug(f"[TF] Destroy stderr: {result.stderr}")
            success = result.returncode == 0

            if result.stdout:
                logger.command_output("terraform_destroy", result.stdout, result.returncode)

            if not success:
                logger.error("Terraform destroy failed", category=LogCategory.ERROR, returncode=result.returncode)
            else:
                logger.success("Terraform destroy completed successfully", category=LogCategory.STATUS)

            logger.operation_end("terraform_destroy", success)
            return success, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            logger.error("Terraform destroy timed out after 30 minutes", category=LogCategory.ERROR)
            logger.operation_end("terraform_destroy", success=False)
            return False, "", "Timeout"
        except Exception as e:
            logger.exception("Terraform destroy failed with exception", exception=e)
            logger.operation_end("terraform_destroy", success=False)
            return False, "", str(e)

    def output(self) -> dict[str, Any] | None:
        """
        Get terraform outputs as JSON
        Returns:
            dict: Terraform outputs or None if failed
        """
        logger.operation_start("terraform_output")
        try:
            cmd = [self.terraform_path, "output", "-json"]
            logger.debug(f"[TF] Running command: {' '.join(cmd)}")
            result = self._run(cmd, timeout=60)
            logger.debug(f"[TF] Output stdout: {result.stdout}")
            logger.debug(f"[TF] Output stderr: {result.stderr}")
            if result.returncode != 0:
                logger.warning(
                    "Terraform output retrieval failed", category=LogCategory.WARNING, returncode=result.returncode
                )
                logger.operation_end("terraform_output", success=False)
                return None

            outputs = json.loads(result.stdout)
            logger.success(f"Terraform outputs retrieved ({len(outputs)} outputs)", category=LogCategory.STATUS)
            logger.operation_end("terraform_output", success=True)
            return outputs
        except Exception as e:
            logger.exception("Terraform output extraction failed with exception", exception=e)
            logger.operation_end("terraform_output", success=False)
            return None
