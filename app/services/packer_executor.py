"""
Packer execution utilities with comprehensive structured logging
"""

import json
import os
import subprocess
from typing import Any

from ..config import settings
from ..utils.logger import LogCategory, get_logger

logger = get_logger(__name__)


class PackerExecutor:
    """Executor for Packer operations with detailed logging"""

    def __init__(self, working_dir: str, env_vars: dict[str, str] | None = None):
        self.working_dir = working_dir
        self.packer_path = settings.PACKER_PATH
        self.env_vars = env_vars or {}

    def _get_env(self, extra_env: dict[str, str] | None = None) -> dict[str, str]:
        """Get environment variables including OpenStack credentials and Packer debug logging"""
        env = os.environ.copy()
        env.update(self.env_vars)
        if extra_env:
            env.update(extra_env)
        env["PACKER_LOG"] = "1"
        # Ensure clouds.yaml is found by Packer/OpenStack
        if "OS_CLIENT_CONFIG_FILE" not in env:
            env["OS_CLIENT_CONFIG_FILE"] = settings.OPENSTACK_CLOUDS_YAML
        return env

    def init(self) -> tuple[bool, str, str]:
        """
        Initialize Packer (install required plugins)
        Returns:
            tuple: (success, stdout, stderr)
        """
        logger.operation_start("packer_init", working_dir=self.working_dir)
        try:
            cmd = [self.packer_path, "init", "."]
            result = subprocess.run(
                cmd, cwd=self.working_dir, capture_output=True, text=True, timeout=300, env=self._get_env()
            )
            success = result.returncode == 0

            if result.stdout:
                logger.command_output("packer_init", result.stdout, result.returncode)

            if not success:
                logger.error(
                    "Packer init failed",
                    category=LogCategory.ERROR,
                    returncode=result.returncode,
                    stderr=result.stderr[:1000],  # First 1000 chars
                )
            else:
                logger.success("Packer init completed", category=LogCategory.STATUS)

            logger.operation_end("packer_init", success)
            return success, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            logger.error("Packer init timed out after 5 minutes", category=LogCategory.ERROR)
            logger.operation_end("packer_init", success=False)
            return False, "", "Timeout"
        except Exception as e:
            logger.exception("Packer init failed with exception", exception=e)
            logger.operation_end("packer_init", success=False)
            return False, "", str(e)

    def validate(self, template_file: str, variables: dict[str, Any] | None = None) -> tuple[bool, str, str]:
        """
        Validate a Packer template
        Args:
            template_file: Path to the Packer template file
            variables: Dictionary of variables to pass via -var flags
        Returns:
            tuple: (success, stdout, stderr)
        """
        logger.operation_start("packer_validate", template=template_file)
        logger.info(f"Packer working directory: {self.working_dir}", category=LogCategory.SYSTEM)
        logger.info(f"Files in working directory: {os.listdir(self.working_dir)}", category=LogCategory.SYSTEM)
        try:
            cmd = [self.packer_path, "validate"]

            if variables:
                for key, value in variables.items():
                    value_str = json.dumps(value) if isinstance(value, (list, dict)) else str(value)
                    cmd.extend(["-var", f"{key}={value_str}"])

            cmd.append(".")

            result = subprocess.run(
                cmd, cwd=self.working_dir, capture_output=True, text=True, timeout=60, env=self._get_env()
            )
            success = result.returncode == 0

            if result.stdout:
                logger.command_output("packer_validate", result.stdout, result.returncode)

            if not success:
                error_msg = self._extract_error_from_packer(result.stderr)
                logger.error(
                    f"Packer validation failed: {error_msg}", category=LogCategory.ERROR, returncode=result.returncode
                )
            else:
                logger.success("Packer template validated", category=LogCategory.STATUS)

            logger.operation_end("packer_validate", success)
            return success, result.stdout, result.stderr
        except Exception as e:
            logger.exception("Packer validation failed with exception", exception=e)
            logger.operation_end("packer_validate", success=False)
            return False, "", str(e)

    def build(
        self,
        template_file: str,
        variables: dict[str, Any] | None = None,
        extra_env: dict[str, str] | None = None,
        force: bool = True,
    ) -> tuple[bool, str]:
        """
        Build a Packer image
        Args:
            template_file: Path to the Packer template file
            variables: Dictionary of variables to pass via -var flags
            extra_env: Additional environment variables for the build process
            force: Force overwrite of existing images (default: True)
        Returns:
            tuple: (success, output)
        """
        logger.operation_start("packer_build", template=template_file, force=force, var_count=len(variables or {}))
        try:
            cmd = [self.packer_path, "build"]
            if force:
                cmd.append("-force")

            if variables:
                for key, value in variables.items():
                    value_str = json.dumps(value) if isinstance(value, (list, dict)) else str(value)
                    cmd.extend(["-var", f"{key}={value_str}"])
            cmd.append(template_file)  # Use absolute path

            logger.info("Starting Packer build process (this may take several minutes)...", category=LogCategory.STATUS)

            process = subprocess.Popen(
                cmd,
                cwd=self.working_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=self._get_env(extra_env),
            )
            output_lines = []
            for line in process.stdout:
                line = line.rstrip()
                if line:
                    logger.info(line, category=LogCategory.OUTPUT)
                    output_lines.append(line)

            process.wait(timeout=3600)
            success = process.returncode == 0

            if not success:
                logger.error(
                    "Packer build failed",
                    category=LogCategory.ERROR,
                    returncode=process.returncode,
                    output_lines=len(output_lines),
                )
            else:
                logger.success(
                    "Packer build completed successfully", category=LogCategory.STATUS, output_lines=len(output_lines)
                )

            logger.operation_end("packer_build", success)
            return success, "\n".join(output_lines)
        except subprocess.TimeoutExpired:
            logger.error("Packer build timed out after 1 hour", category=LogCategory.ERROR)
            logger.operation_end("packer_build", success=False)
            return False, "Build timed out"
        except Exception as e:
            logger.exception("Packer build failed with exception", exception=e)
            logger.operation_end("packer_build", success=False)
            return False, str(e)

    def _extract_error_from_packer(self, stderr: str) -> str:
        """Extract meaningful error message from Packer stderr"""

        # Look for actual error messages (lines with * Get or error patterns)
        lines = stderr.split("\n")
        errors = []
        for line in lines:
            # Skip verbose TRACE/DEBUG lines
            if "[TRACE]" in line or "[DEBUG]" in line or "plugingetter" in line:
                continue
            # Look for actual errors
            if "* Get" in line or "Error" in line or "error" in line or line.strip().startswith("*"):
                errors.append(line.strip())

        if errors:
            return " | ".join(errors[:3])  # First 3 errors

        # Fallback: return last non-empty line
        for line in reversed(lines):
            if line.strip() and "[TRACE]" not in line and "[DEBUG]" not in line:
                return line.strip()

        return "Unknown error"
