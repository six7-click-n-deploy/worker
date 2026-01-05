"""
Terraform execution utilities
"""
import os
import subprocess
import logging
import json
from typing import Optional, Dict, Any
from ..config import settings

logger = logging.getLogger(__name__)


class TerraformExecutor:
    """Executor for Terraform operations"""

    def __init__(self, working_dir: str, env_vars: Optional[Dict[str, str]] = None):
        self.working_dir = working_dir
        self.terraform_path = settings.TERRAFORM_PATH
        self.env_vars = env_vars or {}

    def _get_env(self, extra_env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """Get environment variables including OpenStack credentials and Terraform debug logging"""
        env = os.environ.copy()
        env.update(self.env_vars)
        if extra_env:
            env.update(extra_env)
        env['TF_LOG'] = 'DEBUG'  # Always enable Terraform debug logging
        return env

    def init(self) -> tuple[bool, str, str]:
        """
        Initialize Terraform in the working directory
        Returns:
            tuple: (success, stdout, stderr)
        """
        logger.info(f"[TerraformExecutor] INIT: working_dir={self.working_dir}, terraform_path={self.terraform_path}")
        try:
            env = self._get_env()
            logger.debug(f"[TerraformExecutor] ENV: {json.dumps(env, indent=2)}")
            cmd = [self.terraform_path, "init", "-input=false"]
            logger.info(f"[TerraformExecutor] CMD: {' '.join(cmd)}")
            result = subprocess.run(
                cmd,
                cwd=self.working_dir,
                capture_output=True,
                text=True,
                timeout=300,
                env=env
            )
            logger.info(f"[TerraformExecutor] INIT STDOUT: {result.stdout}")
            logger.info(f"[TerraformExecutor] INIT STDERR: {result.stderr}")
            success = result.returncode == 0
            if not success:
                logger.error(f"[TerraformExecutor] INIT FAILED: returncode={result.returncode}")
            else:
                logger.info("[TerraformExecutor] INIT SUCCESSFUL")
            return success, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            logger.error("[TerraformExecutor] INIT TIMEOUT")
            return False, "", "Terraform init timed out after 5 minutes"
        except Exception as e:
            logger.error(f"[TerraformExecutor] INIT ERROR: {e}")
            return False, "", str(e)

    def plan(self, var_file: Optional[str] = None, variables: Optional[Dict[str, Any]] = None) -> tuple[bool, str, str]:
        """
        Run terraform plan
        Args:
            var_file: Path to tfvars file
            variables: Dictionary of variables to pass
        Returns:
            tuple: (success, stdout, stderr)
        """
        logger.info(f"[TerraformExecutor] PLAN: working_dir={self.working_dir}, var_file={var_file}")
        try:
            cmd = [self.terraform_path, "plan", "-input=false"]
            if var_file:
                cmd.extend(["-var-file", var_file])
            if variables:
                logger.info(f"[TerraformExecutor] PLAN VARIABLES: {json.dumps(variables, indent=2)}")
                for key, value in variables.items():
                    cmd.extend(["-var", f"{key}={value}"])
            env = self._get_env()
            logger.debug(f"[TerraformExecutor] ENV: {json.dumps(env, indent=2)}")
            logger.info(f"[TerraformExecutor] CMD: {' '.join(cmd)}")
            result = subprocess.run(
                cmd,
                cwd=self.working_dir,
                capture_output=True,
                text=True,
                timeout=300,
                env=env
            )
            logger.info(f"[TerraformExecutor] PLAN STDOUT: {result.stdout}")
            logger.info(f"[TerraformExecutor] PLAN STDERR: {result.stderr}")
            success = result.returncode == 0
            if not success:
                logger.error(f"[TerraformExecutor] PLAN FAILED: returncode={result.returncode}")
            else:
                logger.info("[TerraformExecutor] PLAN SUCCESSFUL")
            return success, result.stdout, result.stderr
        except Exception as e:
            logger.error(f"[TerraformExecutor] PLAN ERROR: {e}")
            return False, "", str(e)

    def apply(self, var_file: Optional[str] = None, variables: Optional[Dict[str, Any]] = None) -> tuple[bool, str, str]:
        """
        Run terraform apply
        Args:
            var_file: Path to tfvars file
            variables: Dictionary of variables to pass
        Returns:
            tuple: (success, stdout, stderr)
        """
        logger.info(f"[TerraformExecutor] APPLY: working_dir={self.working_dir}, var_file={var_file}")
        try:
            cmd = [self.terraform_path, "apply", "-auto-approve", "-input=false"]
            if var_file:
                cmd.extend(["-var-file", var_file])
            if variables:
                logger.info(f"[TerraformExecutor] APPLY VARIABLES: {json.dumps(variables, indent=2)}")
                for key, value in variables.items():
                    cmd.extend(["-var", f"{key}={value}"])
            env = self._get_env()
            logger.debug(f"[TerraformExecutor] ENV: {json.dumps(env, indent=2)}")
            logger.info(f"[TerraformExecutor] CMD: {' '.join(cmd)}")
            result = subprocess.run(
                cmd,
                cwd=self.working_dir,
                capture_output=True,
                text=True,
                timeout=1800,
                env=env
            )
            logger.info(f"[TerraformExecutor] APPLY STDOUT: {result.stdout}")
            logger.info(f"[TerraformExecutor] APPLY STDERR: {result.stderr}")
            success = result.returncode == 0
            if not success:
                logger.error(f"[TerraformExecutor] APPLY FAILED: returncode={result.returncode}")
            else:
                logger.info("[TerraformExecutor] APPLY SUCCESSFUL")
            return success, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            logger.error("[TerraformExecutor] APPLY TIMEOUT")
            return False, "", "Terraform apply timed out after 30 minutes"
        except Exception as e:
            logger.error(f"[TerraformExecutor] APPLY ERROR: {e}")
            return False, "", str(e)

    def destroy(self, var_file: Optional[str] = None, variables: Optional[Dict[str, Any]] = None) -> tuple[bool, str, str]:
        """
        Run terraform destroy
        Args:
            var_file: Path to tfvars file
            variables: Dictionary of variables to pass
        Returns:
            tuple: (success, stdout, stderr)
        """
        logger.info(f"[TerraformExecutor] DESTROY: working_dir={self.working_dir}, var_file={var_file}")
        try:
            cmd = [self.terraform_path, "destroy", "-auto-approve", "-input=false"]
            if var_file:
                cmd.extend(["-var-file", var_file])
            if variables:
                logger.info(f"[TerraformExecutor] DESTROY VARIABLES: {json.dumps(variables, indent=2)}")
                for key, value in variables.items():
                    cmd.extend(["-var", f"{key}={value}"])
            env = self._get_env()
            logger.debug(f"[TerraformExecutor] ENV: {json.dumps(env, indent=2)}")
            logger.info(f"[TerraformExecutor] CMD: {' '.join(cmd)}")
            result = subprocess.run(
                cmd,
                cwd=self.working_dir,
                capture_output=True,
                text=True,
                timeout=1800,
                env=env
            )
            logger.info(f"[TerraformExecutor] DESTROY STDOUT: {result.stdout}")
            logger.info(f"[TerraformExecutor] DESTROY STDERR: {result.stderr}")
            success = result.returncode == 0
            if not success:
                logger.error(f"[TerraformExecutor] DESTROY FAILED: returncode={result.returncode}")
            else:
                logger.info("[TerraformExecutor] DESTROY SUCCESSFUL")
            return success, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            logger.error("[TerraformExecutor] DESTROY TIMEOUT")
            return False, "", "Terraform destroy timed out after 30 minutes"
        except Exception as e:
            logger.error(f"[TerraformExecutor] DESTROY ERROR: {e}")
            return False, "", str(e)

    def output(self) -> Optional[Dict[str, Any]]:
        """
        Get terraform outputs as JSON
        Returns:
            dict: Terraform outputs or None if failed
        """
        logger.info(f"[TerraformExecutor] OUTPUT: working_dir={self.working_dir}")
        try:
            cmd = [self.terraform_path, "output", "-json"]
            env = self._get_env()
            logger.debug(f"[TerraformExecutor] ENV: {json.dumps(env, indent=2)}")
            logger.info(f"[TerraformExecutor] CMD: {' '.join(cmd)}")
            result = subprocess.run(
                cmd,
                cwd=self.working_dir,
                capture_output=True,
                text=True,
                timeout=60,
                env=env
            )
            logger.info(f"[TerraformExecutor] OUTPUT STDOUT: {result.stdout}")
            logger.info(f"[TerraformExecutor] OUTPUT STDERR: {result.stderr}")
            if result.returncode != 0:
                logger.error(f"[TerraformExecutor] OUTPUT FAILED: returncode={result.returncode}")
                return None
            logger.info("[TerraformExecutor] OUTPUT SUCCESSFUL")
            return json.loads(result.stdout)
        except Exception as e:
            logger.error(f"[TerraformExecutor] OUTPUT ERROR: {e}")
            return None