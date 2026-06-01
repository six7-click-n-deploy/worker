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


# File written next to the cloned repo's terraform/ directory to force the
# `pg` backend regardless of what the upstream module declares. Terraform
# treats files ending in `_override.tf` as overrides and replaces the
# `terraform { backend ... }` block in base config with this one.
_PG_BACKEND_OVERRIDE_FILENAME = "pg_backend_override.tf"


def _pg_backend_override_hcl(schema_name: str) -> str:
    # schema_name is interpolated, not user-controlled (we generate it from
    # the deployment UUID). Quoted as an HCL string literal.
    safe = schema_name.replace('"', '\\"')
    return "terraform {\n" '  backend "pg" {\n' f'    schema_name = "{safe}"\n' "  }\n" "}\n"


class TerraformExecutor:
    """Executor for Terraform operations with detailed logging.

    When `backend_conn_str` and `backend_schema_name` are provided the
    executor configures Terraform's `pg` backend so state is persisted in
    a remote Postgres. The conn string lives only in the per-process env
    (`PG_CONN_STR`), never on the command line.
    """

    def __init__(
        self,
        working_dir: str,
        env_vars: dict[str, str] | None = None,
        backend_conn_str: str | None = None,
        backend_schema_name: str | None = None,
    ):
        self.working_dir = working_dir
        self.terraform_path = settings.TERRAFORM_PATH
        self.env_vars = env_vars or {}
        self.backend_conn_str = backend_conn_str
        self.backend_schema_name = backend_schema_name

    def _get_env(self, extra_env: dict[str, str] | None = None) -> dict[str, str]:
        """Get environment variables including OpenStack credentials and Terraform debug logging"""
        env = os.environ.copy()
        env.update(self.env_vars)
        if extra_env:
            env.update(extra_env)
        env["TF_LOG"] = "DEBUG"
        # PG_CONN_STR is read by Terraform's pg backend. Putting it in env
        # (not -backend-config="conn_str=...") keeps the password out of
        # the process listing and command logs.
        if self.backend_conn_str:
            env["PG_CONN_STR"] = self.backend_conn_str
        return env

    def _write_pg_backend_override(self) -> None:
        """Write `pg_backend_override.tf` so init configures the pg backend.

        No-op if no schema is configured (legacy local-state mode).
        """
        if not self.backend_schema_name:
            return
        override_path = os.path.join(self.working_dir, _PG_BACKEND_OVERRIDE_FILENAME)
        with open(override_path, "w") as f:
            f.write(_pg_backend_override_hcl(self.backend_schema_name))
        logger.debug(f"[TF] Wrote pg backend override at {override_path} " f"(schema_name={self.backend_schema_name})")

    def init(self) -> tuple[bool, str, str]:
        """
        Initialize Terraform in the working directory
        Returns:
            tuple: (success, stdout, stderr)
        """
        logger.operation_start("terraform_init", working_dir=self.working_dir)
        logger.debug(f"[TF] Init: working_dir={self.working_dir}, terraform_path={self.terraform_path}")
        try:
            # Write the backend override BEFORE init runs. If we leave it
            # to a later step Terraform will already have committed to
            # whatever backend the upstream module declares (or to `local`).
            self._write_pg_backend_override()

            cmd = [self.terraform_path, "init", "-input=false"]
            # `-reconfigure` makes init idempotent across deploy/update/destroy
            # runs on the same dir — the schema_name comes from the override
            # file we just wrote, not from a stale `.terraform/terraform.tfstate`.
            if self.backend_schema_name:
                cmd.append("-reconfigure")
            logger.debug(f"[TF] Running command: {' '.join(cmd)}")
            result = subprocess.run(
                cmd, cwd=self.working_dir, capture_output=True, text=True, timeout=300, env=self._get_env()
            )
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
            logger.debug("plan variable keys", category=LogCategory.OPERATION, keys=list((variables or {}).keys()))

            result = subprocess.run(
                cmd, cwd=self.working_dir, capture_output=True, text=True, timeout=300, env=self._get_env()
            )
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

            result = subprocess.run(
                cmd, cwd=self.working_dir, capture_output=True, text=True, timeout=1800, env=self._get_env()
            )
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

            result = subprocess.run(
                cmd, cwd=self.working_dir, capture_output=True, text=True, timeout=1800, env=self._get_env()
            )
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
            result = subprocess.run(
                cmd, cwd=self.working_dir, capture_output=True, text=True, timeout=60, env=self._get_env()
            )
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

    def state_pull(self) -> str | None:
        """Return the current state JSON via `terraform state pull`.

        Works for both the local backend (reads `terraform.tfstate`) and
        the pg backend (reads from Postgres). Used by the worker to
        snapshot state onto the task row for debugging — the canonical
        copy lives in the pg backend.
        """
        try:
            cmd = [self.terraform_path, "state", "pull"]
            result = subprocess.run(
                cmd,
                cwd=self.working_dir,
                capture_output=True,
                text=True,
                timeout=60,
                env=self._get_env(),
            )
            if result.returncode != 0:
                logger.warning(
                    "Terraform state pull failed",
                    category=LogCategory.WARNING,
                    returncode=result.returncode,
                )
                return None
            return result.stdout
        except Exception as e:
            logger.warning(f"Terraform state pull raised: {e}", category=LogCategory.WARNING)
            return None
