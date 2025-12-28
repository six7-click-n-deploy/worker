"""
Packer execution utilities
"""
import os
import subprocess
import logging
import json
from typing import Optional, Dict, Any
from ..config import settings

logger = logging.getLogger(__name__)


class PackerExecutor:
    """Executor for Packer operations"""

    def __init__(self, working_dir: str, env_vars: Optional[Dict[str, str]] = None):
        self.working_dir = working_dir
        self.packer_path = settings.PACKER_PATH
        self.env_vars = env_vars or {}

    def _get_env(self, extra_env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """Get environment variables including OpenStack credentials and Packer debug logging"""
        env = os.environ.copy()
        env.update(self.env_vars)
        if extra_env:
            env.update(extra_env)
        env['PACKER_LOG'] = '1'  # Always enable Packer debug logging
        return env

    def init(self) -> bool:
        """
        Initialize Packer (install required plugins)
        Returns:
            bool: True if successful
        """
        logger.info(f"[PackerExecutor] INIT: working_dir={self.working_dir}, packer_path={self.packer_path}")
        try:
            env = self._get_env()
            logger.debug(f"[PackerExecutor] ENV: {json.dumps(env, indent=2)}")
            cmd = [self.packer_path, "init", "."]
            logger.info(f"[PackerExecutor] CMD: {' '.join(cmd)}")
            result = subprocess.run(
                cmd,
                cwd=self.working_dir,
                capture_output=True,
                text=True,
                timeout=300,
                env=env
            )
            logger.info(f"[PackerExecutor] INIT STDOUT: {result.stdout}")
            logger.info(f"[PackerExecutor] INIT STDERR: {result.stderr}")
            if result.returncode != 0:
                logger.error(f"[PackerExecutor] INIT FAILED: returncode={result.returncode}")
                return False
            logger.info("[PackerExecutor] INIT SUCCESSFUL")
            return True
        except Exception as e:
            logger.error(f"[PackerExecutor] INIT ERROR: {e}")
            return False

    def validate(self, template_file: str, variables: Optional[Dict[str, Any]] = None) -> bool:
        """
        Validate a Packer template
        Args:
            template_file: Path to the Packer template file
            variables: Dictionary of variables to pass via -var flags
        Returns:
            bool: True if valid
        """
        logger.info(f"[PackerExecutor] VALIDATE: template_file={template_file}, working_dir={self.working_dir}")
        try:
            cmd = [self.packer_path, "validate"]
            if variables:
                logger.info(f"[PackerExecutor] VALIDATE VARIABLES: {json.dumps(variables, indent=2)}")
                for key, value in variables.items():
                    value_str = json.dumps(value) if isinstance(value, (list, dict)) else str(value)
                    cmd.extend(["-var", f"{key}={value_str}"])
            cmd.append(template_file)
            env = self._get_env()
            logger.debug(f"[PackerExecutor] ENV: {json.dumps(env, indent=2)}")
            logger.info(f"[PackerExecutor] CMD: {' '.join(cmd)}")
            result = subprocess.run(
                cmd,
                cwd=self.working_dir,
                capture_output=True,
                text=True,
                timeout=60,
                env=env
            )
            logger.info(f"[PackerExecutor] VALIDATE STDOUT: {result.stdout}")
            logger.info(f"[PackerExecutor] VALIDATE STDERR: {result.stderr}")
            if result.returncode != 0:
                logger.error(f"[PackerExecutor] VALIDATE FAILED: returncode={result.returncode}")
                return False
            logger.info("[PackerExecutor] VALIDATE SUCCESSFUL")
            return True
        except Exception as e:
            logger.error(f"[PackerExecutor] VALIDATE ERROR: {e}")
            return False

    def build(self, template_file: str, variables: Optional[Dict[str, Any]] = None, extra_env: Optional[Dict[str, str]] = None, force: bool = True) -> bool:
        """
        Build a Packer image
        Args:
            template_file: Path to the Packer template file
            variables: Dictionary of variables to pass via -var flags
            extra_env: Additional environment variables for the build process
            force: Force overwrite of existing images (default: True)
        Returns:
            bool: True if successful
        """
        logger.info(f"[PackerExecutor] BUILD: template_file={template_file}, working_dir={self.working_dir}, force={force}")
        try:
            cmd = [self.packer_path, "build"]
            if force:
                cmd.append("-force")
            if variables:
                logger.info(f"[PackerExecutor] BUILD VARIABLES: {json.dumps(variables, indent=2)}")
                for key, value in variables.items():
                    value_str = json.dumps(value) if isinstance(value, (list, dict)) else str(value)
                    cmd.extend(["-var", f"{key}={value_str}"])
            cmd.append(template_file)
            env = self._get_env(extra_env)
            logger.debug(f"[PackerExecutor] ENV: {json.dumps(env, indent=2)}")
            logger.info(f"[PackerExecutor] CMD: {' '.join(cmd)}")
            process = subprocess.Popen(
                cmd,
                cwd=self.working_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=env
            )
            output_lines = []
            for line in process.stdout:
                line = line.rstrip()
                if line:
                    logger.info(f"[PackerExecutor] BUILD OUTPUT: {line}")
                    output_lines.append(line)
            process.wait(timeout=3600)
            if process.returncode != 0:
                logger.error(f"[PackerExecutor] BUILD FAILED: returncode={process.returncode}")
                logger.error(f"[PackerExecutor] BUILD OUTPUT (last 50 lines): {chr(10).join(output_lines[-50:])}")
                return False
            logger.info("[PackerExecutor] BUILD SUCCESSFUL")
            return True
        except subprocess.TimeoutExpired:
            logger.error("[PackerExecutor] BUILD TIMEOUT")
            return False
        except Exception as e:
            logger.error(f"[PackerExecutor] BUILD ERROR: {e}")
            return False