"""
Terraform execution utilities with comprehensive structured logging
"""

import json
import os
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

    def _get_env(self, extra_env: dict[str, str] | None = None) -> dict[str, str]:
        """Get environment variables including OpenStack credentials and Terraform debug logging"""
        env = os.environ.copy()
        env.update(self.env_vars)
        if extra_env:
            env.update(extra_env)
        env["TF_LOG"] = "INFO"  # Changed from DEBUG for cleaner logs
        return env

    def init(self) -> tuple[bool, str, str]:
        """
        Initialize Terraform in the working directory
        Returns:
            tuple: (success, stdout, stderr)
        """
        logger.operation_start("terraform_init", working_dir=self.working_dir)
        try:
            cmd = [self.terraform_path, "init", "-input=false"]
            result = subprocess.run(
                cmd, cwd=self.working_dir, capture_output=True, text=True, timeout=300, env=self._get_env()
            )
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
            if var_file:
                cmd.extend(["-var-file", var_file])
            if variables:
                for key, value in variables.items():
                    cmd.extend(["-var", f"{key}={value}"])

            logger.info("Analyzing Terraform configuration...", category=LogCategory.STATUS)

            result = subprocess.run(
                cmd, cwd=self.working_dir, capture_output=True, text=True, timeout=300, env=self._get_env()
            )
            success = result.returncode == 0

            if result.stdout:
                logger.command_output("terraform_plan", result.stdout, result.returncode)

            if not success:
                logger.error("Terraform plan failed", category=LogCategory.ERROR, returncode=result.returncode)
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
            if var_file:
                cmd.extend(["-var-file", var_file])
            if variables:
                for key, value in variables.items():
                    cmd.extend(["-var", f"{key}={value}"])

            logger.info("Applying Terraform configuration (this may take minutes)...", category=LogCategory.STATUS)

            result = subprocess.run(
                cmd, cwd=self.working_dir, capture_output=True, text=True, timeout=1800, env=self._get_env()
            )
            success = result.returncode == 0

            if result.stdout:
                logger.command_output("terraform_apply", result.stdout, result.returncode)

            if not success:
                logger.error("Terraform apply failed", category=LogCategory.ERROR, returncode=result.returncode)
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
            if var_file:
                cmd.extend(["-var-file", var_file])
            if variables:
                for key, value in variables.items():
                    cmd.extend(["-var", f"{key}={value}"])

            logger.info("Destroying Terraform resources (this may take minutes)...", category=LogCategory.STATUS)

            result = subprocess.run(
                cmd, cwd=self.working_dir, capture_output=True, text=True, timeout=1800, env=self._get_env()
            )
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
            result = subprocess.run(
                cmd, cwd=self.working_dir, capture_output=True, text=True, timeout=60, env=self._get_env()
            )
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
