import json
import os
from typing import Any

from .celery_app import celery_app
from .services import OpenStackService, PackerExecutor, TerraformExecutor, git_service, openstack_auth_service
from .utils.logger import LogCategory, get_logger

logger = get_logger(__name__)


class Failure(Exception):
    """Custom exception that carries deployment details for Celery"""

    def __init__(
        self,
        message: str,
        deployment_id: str,
        logs_dict: dict[str, Any],
        tf_state: str | None = None,
        commit_info: dict[str, Any] | None = None,
        terraform_outputs: dict[str, Any] | None = None,
    ):
        self.deployment_id = deployment_id
        self.logs_dict = logs_dict
        self.tf_state = tf_state
        self.commit_info = commit_info
        self.terraform_outputs = terraform_outputs

        # Encode all data as JSON in the exception message
        data = {
            "error": message,
            "deployment_id": deployment_id,
            "logs": logs_dict,
            "tf_state": tf_state,
            "commit_info": commit_info,
            "terraform_outputs": terraform_outputs,
        }
        super().__init__(json.dumps(data))

    def to_dict(self) -> dict[str, Any]:
        """Convert exception data to dict for serialization"""
        return json.loads(str(self))


@celery_app.task(bind=True, name="tasks.deploy_application")
def deploy_application(
    self,
    deployment_id: str,
    app_id: str,
    app_git_link: str,
    release: str,
    user_vars: dict[str, Any],
    teams: dict[str, list] = None,
):
    """
    Deploy an application using Terraform and Packer

    Args:
        deployment_id: UUID of the deployment
        app_git_link: Git repo URL
        release: Tag/Release to checkout
        user_vars: User variables for Packer/Terraform
        teams: Teams mit User-Emails {"team_name": [{"email": "user@example.com"}]}

    Returns:
        dict: status, logs, tf_state, commit_info, terraform_outputs
    """
    task_logger = get_logger(f"deploy:{deployment_id}")
    repo_path = None
    tf_state = None
    outputs = None
    commit_info = None
    terraform_dir = None

    # Default teams to empty dict if not provided
    if teams is None:
        teams = {}

    def collect_terraform_state():
        """Try to collect terraform state even on failure"""
        if terraform_dir and os.path.exists(terraform_dir):
            tfstate_path = os.path.join(terraform_dir, "terraform.tfstate")
            if os.path.exists(tfstate_path):
                try:
                    with open(tfstate_path) as f:
                        return f.read()
                except Exception as e:
                    task_logger.warning(f"Could not read terraform state: {e}", category=LogCategory.WARNING)
        return None

    def collect_terraform_outputs():
        """Try to collect terraform outputs even on partial success"""
        if terraform_dir and os.path.exists(terraform_dir):
            try:
                terraform = TerraformExecutor(terraform_dir, env_vars=openstack_env)
                return terraform.output()
            except Exception as e:
                task_logger.warning(f"Could not read terraform outputs: {e}", category=LogCategory.WARNING)
        return None

    try:
        task_logger.phase("Starting Deployment")
        task_logger.resource_info("deployment", deployment_id, release=release, git_url=app_git_link)

        # Phase 1: OpenStack credentials
        task_logger.phase("OpenStack Setup")
        task_logger.operation_start("openstack_auth")
        try:
            openstack_env = openstack_auth_service.get_environment_variables()
            if not openstack_env or not openstack_env.get("OS_AUTH_URL"):
                raise Exception("OpenStack credentials not configured")
            task_logger.operation_end("openstack_auth", success=True)
            task_logger.success("OpenStack credentials loaded", category=LogCategory.STATUS)
        except Exception as e:
            task_logger.operation_end("openstack_auth", success=False)
            raise Exception(f"OpenStack error: {str(e)}")

        # Phase 2: Git clone
        task_logger.phase("Git Repository Setup")
        task_logger.info(f"Cloning repository: {app_git_link}", category=LogCategory.OPERATION)
        try:
            repo_path = git_service.clone_release(git_url=app_git_link, deployment_id=deployment_id, tag=release)

            # Get commit info
            try:
                import git

                repo = git.Repo(repo_path)
                commit = repo.head.commit
                commit_info = {
                    "hash": commit.hexsha,
                    "message": commit.message.strip(),
                    "author": str(commit.author),
                    "date": commit.committed_datetime.isoformat(),
                }
                task_logger.resource_info(
                    "git_commit",
                    commit.hexsha[:8],
                    hash=commit.hexsha,
                    message=commit.message.strip(),
                    author=str(commit.author),
                )
                task_logger.success(f"Repository cloned at commit {commit.hexsha[:8]}", category=LogCategory.STATUS)
            except Exception as e:
                task_logger.warning(f"Could not extract commit info: {e}", category=LogCategory.WARNING)

        except Exception as e:
            raise Exception(f"Git clone failed: {str(e)}")

        image_name = app_id + "-" + release
        # Phase 3: Packer (optional)
        packer_file = os.path.join(repo_path, "packer", "template.pkr.hcl")
        if os.path.exists(packer_file):
            task_logger.phase("Packer Image Build")
            try:
                # Check if image already exists on OpenStack
                task_logger.info(f"Checking if image '{image_name}' already exists...", category=LogCategory.OPERATION)
                openstack_service = OpenStackService(env_vars=openstack_env)
                exists, image_id = openstack_service.check_image_exists(image_name)

                if exists:
                    task_logger.success(
                        f"Image '{image_name}' already exists (ID: {image_id}). Skipping Packer build.",
                        category=LogCategory.STATUS,
                    )
                    # Skip Packer build, continue with Terraform
                else:
                    task_logger.info(
                        f"Image '{image_name}' does not exist. Building...", category=LogCategory.OPERATION
                    )
                    # Perform Packer build
                    packer = PackerExecutor(os.path.join(repo_path, "packer"), env_vars=openstack_env)

                    task_logger.info("Initializing Packer plugins...", category=LogCategory.OPERATION)
                    success, stdout, stderr = packer.init()
                    if stdout:
                        task_logger.command_output("packer_init_stdout", stdout, success=success)
                    if stderr and not success:
                        task_logger.warning(f"Packer init stderr:\n{stderr}", category=LogCategory.WARNING)
                    if not success:
                        raise Exception("Packer init failed")

                    task_logger.info("Validating Packer template...", category=LogCategory.OPERATION)
                    success, stdout, stderr = packer.validate("template.pkr.hcl", {})
                    if not success:
                        raise Exception(f"Packer validation failed: {stderr}")

                    task_logger.info(
                        "Building Docker image (this may take several minutes)...", category=LogCategory.STATUS
                    )
                    # Merge user_vars with teams for Packer
                    packer_vars = {**user_vars["packer"]} if "packer" in user_vars else {}
                    packer_vars["image_name"] = image_name

                    success, output = packer.build("template.pkr.hcl", packer_vars)
                    if not success:
                        raise Exception(f"Packer build failed: {output}")

                    task_logger.success("Packer image built successfully", category=LogCategory.STATUS)
            except Exception as e:
                raise Exception(f"Packer error: {str(e)}")
        else:
            task_logger.info("No Packer template found, skipping image build", category=LogCategory.SYSTEM)

        # Phase 4: Terraform
        task_logger.phase("Terraform Deployment")
        terraform_dir = os.path.join(repo_path, "terraform")
        if not os.path.exists(terraform_dir):
            raise Exception(f"Terraform directory not found at {terraform_dir}")

        try:
            terraform = TerraformExecutor(terraform_dir, env_vars=openstack_env)

            task_logger.info("Initializing Terraform...", category=LogCategory.OPERATION)
            success, stdout, stderr = terraform.init()
            if not success:
                raise Exception("Terraform init failed")
            task_logger.success("Terraform initialization completed", category=LogCategory.STATUS)

            task_logger.info("Planning Terraform deployment...", category=LogCategory.OPERATION)
            # Merge user_vars with teams for Terraform
            terraform_vars = {**user_vars["terraform"]} if "terraform" in user_vars else {}
            terraform_vars["image_name"] = image_name
            if teams:
                terraform_vars["users"] = teams

            success, stdout, stderr = terraform.plan(variables=terraform_vars)
            if not success:
                raise Exception("Terraform plan failed")
            task_logger.success("Terraform plan completed successfully", category=LogCategory.STATUS)

            task_logger.info(
                "Applying Terraform configuration (this may take several minutes)...", category=LogCategory.STATUS
            )
            success, stdout, stderr = terraform.apply(variables=terraform_vars)
            if not success:
                raise Exception("Terraform apply failed")
            task_logger.success("Terraform resources created", category=LogCategory.STATUS)

            # Collect outputs and state
            outputs = collect_terraform_outputs()
            tf_state = collect_terraform_state()

            if outputs:
                task_logger.info(
                    "Terraform deployment outputs collected", category=LogCategory.OPERATION, output_count=len(outputs)
                )

        except Exception as e:
            # Try to collect partial results even on failure
            tf_state = collect_terraform_state()
            outputs = collect_terraform_outputs()
            raise Exception(f"Terraform error: {str(e)}")

        task_logger.phase("Deployment Complete")
        task_logger.success(f"Deployment {deployment_id} completed successfully", category=LogCategory.STATUS)

        # Log summary
        summary = task_logger.get_summary()
        task_logger.info("Deployment summary", category=LogCategory.SYSTEM, **summary)

        # Return result (sent via task-succeeded event)
        return {
            "status": "success",
            "deployment_id": deployment_id,
            "logs": task_logger.get_logs_dict(),
            "tf_state": tf_state,
            "commit_info": commit_info,
            "terraform_outputs": outputs,
        }

    except Exception as e:
        task_logger.exception(f"Deployment failed: {str(e)}", exception=e, deployment_id=deployment_id)

        # Try to collect any available state/outputs even on failure
        if not tf_state:
            tf_state = collect_terraform_state()
        if not outputs:
            outputs = collect_terraform_outputs()

        # Raise custom exception with all details
        raise Failure(
            message=str(e),
            deployment_id=deployment_id,
            logs_dict=task_logger.get_logs_dict(),
            tf_state=tf_state,
            commit_info=commit_info,
            terraform_outputs=outputs,
        )

    finally:
        if repo_path:
            try:
                git_service.cleanup_repository(repo_path)
                task_logger.success("Repository cleanup completed", category=LogCategory.SYSTEM)
            except Exception as e:
                task_logger.warning(f"Repository cleanup failed: {e}", category=LogCategory.WARNING)
