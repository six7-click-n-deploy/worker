import json
import os
from typing import Any

from .celery_app import celery_app
from .config import settings
from .services import (
    OpenStackService,
    PackerBuildLock,
    PackerExecutor,
    PerTaskCloudsConfig,
    TerraformExecutor,
    git_service,
)
from .utils.logger import LogCategory, get_logger

logger = get_logger(__name__)


def _tfstate_schema_name(deployment_id: str) -> str:
    """Postgres schema name for one deployment's Terraform state.

    UUIDs contain hyphens, which would force every reference to be
    double-quoted. Replacing hyphens with underscores keeps the schema
    a plain unquoted identifier and avoids escaping hazards in any
    backend-config plumbing.
    """
    return f"deployment_{deployment_id.replace('-', '_')}"


class Failure(Exception):
    """Custom exception that carries deployment details for Celery.

    The full failure payload is serialised once into ``args[0]`` as a JSON
    string. The backend's celery event listener parses that JSON back via
    a ``Failure\\('<json>'\\)`` regex over the traceback.

    Pickling notes: Celery pickles exceptions to ship them through
    ``task-failed`` events. Because the public ``__init__`` takes six
    positional arguments while ``args`` only has the JSON string,
    ``Exception.__reduce__`` couldn't round-trip — Celery wrapped us in
    ``UnpickleableExceptionWrapper``. We override ``__reduce__`` to
    reconstruct via the dedicated classmethod ``_from_payload`` which
    accepts the single JSON string directly.
    """

    def __init__(
        self,
        message: str,
        deployment_id: str,
        logs_dict: list[dict[str, Any]] | dict[str, Any],
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

    @classmethod
    def _from_payload(cls, payload: str) -> "Failure":
        """Reconstruct a Failure from the JSON payload it serialised itself into.

        Used by ``__reduce__`` so pickle can round-trip the exception
        without re-wrapping the JSON in a second ``json.dumps`` call.
        """
        data = json.loads(payload)
        instance = cls.__new__(cls)
        instance.deployment_id = data.get("deployment_id", "")
        instance.logs_dict = data.get("logs")
        instance.tf_state = data.get("tf_state")
        instance.commit_info = data.get("commit_info")
        instance.terraform_outputs = data.get("terraform_outputs")
        Exception.__init__(instance, payload)
        return instance

    def __reduce__(self):
        # The single-arg constructor here is ``_from_payload``; args[0] is
        # the JSON string we built in __init__.
        return (Failure._from_payload, (self.args[0] if self.args else "{}",))

    def __repr__(self) -> str:
        # Pin the repr format that the backend's celery event listener
        # relies on (regex ``Failure\('(.+)'\)``). Python's default repr
        # for a single-arg exception already matches, but spelling it out
        # makes the contract explicit and decouples us from interpreter
        # changes that affect the default formatting.
        return f"Failure({self.args[0]!r})" if self.args else "Failure()"

    def to_dict(self) -> dict[str, Any]:
        """Convert exception data to dict for serialization"""
        return json.loads(str(self))


# --- Variable encoding for Packer/Terraform CLI ----------------------------
#
# The previous helper (`flatten_vars_to_strings`) called `s.replace("\\", "")`
# on every value, which silently destroyed escaped quotes inside JSON-encoded
# nested structures (e.g. `users={"Team-1":[{"email":"foo"}]}`). HCL then
# rejected the malformed value during `terraform plan`, but the failure
# surfaced only as the opaque message "Terraform plan failed" because we did
# not forward the plan's stderr. Both bugs are fixed here and at the call
# sites below.


def encode_terraform_vars(d: dict[str, Any]) -> dict[str, str]:
    """Encode variables for ``terraform -var key=value`` CLI args.

    Terraform reads complex types (objects, tuples) when the value is a
    valid JSON literal. We JSON-encode dicts/lists once and pass them
    through verbatim — no string normalisation that could damage escape
    sequences.
    """
    result: dict[str, str] = {}
    for k, v in d.items():
        if v is None:
            continue
        if isinstance(v, bool):
            # HCL accepts lowercase only; ``str(True)`` would emit "True".
            result[k] = "true" if v else "false"
        elif isinstance(v, (dict, list)):
            result[k] = json.dumps(v, ensure_ascii=False)
        else:
            result[k] = str(v)
    return result


def encode_packer_vars(d: dict[str, Any]) -> dict[str, str]:
    """Encode variables for ``packer -var key=value`` CLI args.

    Mirrors the historical Packer behaviour: lists are joined as
    comma-separated strings (the project's Packer templates split them
    again internally). The destructive backslash-stripping the old helper
    performed is dropped — string values are passed through verbatim.
    """
    result: dict[str, str] = {}
    for k, v in d.items():
        if v is None:
            continue
        if isinstance(v, list):
            result[k] = ",".join(str(x) for x in v if x is not None)
        elif isinstance(v, dict):
            # No Packer template currently expects nested objects; JSON-encode
            # defensively so a future template that does parse them works.
            result[k] = json.dumps(v, ensure_ascii=False)
        elif isinstance(v, bool):
            result[k] = "true" if v else "false"
        else:
            result[k] = str(v)
    return result


# Back-compat alias for any external import. Defaults to the Packer
# semantics (lists → comma-joined) which matches the old helper's intent
# but no longer strips backslashes.
flatten_vars_to_strings = encode_packer_vars


@celery_app.task(bind=True, name="tasks.deploy_application")
def deploy_application(
    self,
    deployment_id: str,
    app_id: str,
    app_git_link: str,
    release: str,
    user_vars: dict[str, Any],
    teams: dict[str, list] = None,
    openstack_envelope: dict[str, Any] | None = None,
):
    """
    Deploy an application using Terraform and Packer

    Args:
        deployment_id: UUID of the deployment
        app_git_link: Git repo URL
        release: Tag/Release to checkout
        user_vars: User variables for Packer/Terraform
        teams: Teams mit User-Emails {"team_name": [{"email": "user@example.com"}]}
        openstack_envelope: Encrypted per-user OpenStack credential envelope
            shipped from the backend. Required for new deploys; the optional
            default exists only so older queued messages don't crash the
            worker on rollout (we raise immediately if it's missing).

    Returns:
        dict: status, logs, tf_state, commit_info, terraform_outputs
    """
    task_logger = get_logger(f"deploy:{deployment_id}")
    repo_path = None
    tf_state = None
    outputs = None
    commit_info = None
    terraform_dir = None
    openstack_env: dict[str, str] = {}
    clouds_config: PerTaskCloudsConfig | None = None

    # Terraform's pg backend lives in a worker-only Postgres. Configured
    # at deploy/destroy time by writing a `pg_backend_override.tf` next
    # to the cloned repo's terraform/ directory. One schema per
    # deployment isolates state and locks.
    tfstate_conn_str = settings.TFSTATE_DATABASE_URL or None
    tfstate_schema = _tfstate_schema_name(deployment_id)

    # Default teams to empty dict if not provided
    if teams is None:
        teams = {}

    def collect_terraform_state():
        """Snapshot terraform state for the task row.

        With the pg backend the canonical state lives in Postgres; this
        snapshot is best-effort and used for debugging only. Falls back
        to reading the local `terraform.tfstate` file for legacy/test
        modes that don't configure a remote backend.
        """
        if not (terraform_dir and os.path.exists(terraform_dir)):
            return None
        try:
            terraform = TerraformExecutor(
                terraform_dir,
                env_vars=openstack_env,
                backend_conn_str=tfstate_conn_str,
                backend_schema_name=tfstate_schema,
            )
            pulled = terraform.state_pull()
            if pulled:
                return pulled
        except Exception as e:
            task_logger.warning(f"Could not pull terraform state: {e}", category=LogCategory.WARNING)

        # Legacy fallback — only relevant when no pg backend is configured.
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
                terraform = TerraformExecutor(
                    terraform_dir,
                    env_vars=openstack_env,
                    backend_conn_str=tfstate_conn_str,
                    backend_schema_name=tfstate_schema,
                )
                return terraform.output()
            except Exception as e:
                task_logger.warning(f"Could not read terraform outputs: {e}", category=LogCategory.WARNING)
        return None

    try:
        task_logger.phase("Starting Deployment")
        task_logger.resource_info(
            "deployment",
            deployment_id,
            app_id=app_id,
            git_url=app_git_link,
            release=release,
            user_vars_keys=list(user_vars.keys()),
            teams_keys=list(teams.keys()),
        )

        # Phase 1: OpenStack credentials (envelope only — materialised after
        # the git clone so the per-task clouds.yaml lives inside repo_path).
        task_logger.phase("OpenStack Setup")
        if not openstack_envelope:
            raise Exception("OpenStack credential envelope missing — user must upload " "credentials before deploying")
        task_logger.success(
            "OpenStack credential envelope received",
            category=LogCategory.STATUS,
        )

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

        # Materialise the per-task clouds.yaml inside repo_path with mode 0600.
        # Lives only for the duration of this task; shredded by __exit__.
        task_logger.operation_start("openstack_credentials_materialise")
        clouds_config = PerTaskCloudsConfig(openstack_envelope, work_dir=repo_path)
        openstack_env = clouds_config.__enter__()
        task_logger.operation_end("openstack_credentials_materialise", success=True)
        task_logger.success(
            "Per-task clouds.yaml written",
            category=LogCategory.STATUS,
        )

        # Cache the built image by commit SHA, not by release tag.
        # `release` is often a moving ref (e.g. "main", "latest") — caching by tag
        # silently serves stale images when the underlying commit changes. The
        # short SHA is content-addressed: a new commit always misses the cache.
        if commit_info and commit_info.get("hash"):
            image_tag = commit_info["hash"][:8]
        else:
            # Commit lookup failed above (warning logged); fall back to release tag.
            image_tag = release
        image_name = f"{app_id}-{image_tag}"
        # Phase 3: Packer (optional) — guarded by a Redis lock keyed on
        # (project_id, image_name) so two parallel workers can't both kick
        # off a build for the same image and end up with duplicate Glance
        # entries plus wasted compute.
        packer_file = os.path.join(repo_path, "packer", "template.pkr.hcl")
        if os.path.exists(packer_file):
            task_logger.phase("Packer Image Build")
            project_id = openstack_envelope.get("project_id") or openstack_envelope.get("project_name") or "default"
            build_lock = PackerBuildLock(project_id, image_name)
            openstack_service = OpenStackService(env_vars=openstack_env)
            try:
                while True:
                    # If the image already exists, skip the build and the lock.
                    exists, image_id = openstack_service.check_image_exists(image_name)
                    if exists:
                        task_logger.success(
                            f"Image '{image_name}' already exists (ID: {image_id}). Skipping Packer build.",
                            category=LogCategory.STATUS,
                        )
                        break

                    held = build_lock.acquire_or_wait()
                    if not held:
                        # We slept inside acquire_or_wait; re-check Glance.
                        continue

                    # Re-check after acquiring: another worker may have
                    # finished its build between our last check and our lock
                    # acquisition.
                    exists, image_id = openstack_service.check_image_exists(image_name)
                    if exists:
                        task_logger.success(
                            f"Image '{image_name}' built by another worker (ID: {image_id}). Skipping.",
                            category=LogCategory.STATUS,
                        )
                        break

                    task_logger.info(
                        f"Image '{image_name}' does not exist. Building...", category=LogCategory.OPERATION
                    )
                    packer = PackerExecutor(os.path.join(repo_path, "packer"), env_vars=openstack_env)

                    task_logger.info("Initializing Packer plugins...", category=LogCategory.OPERATION)
                    success, stdout, stderr = packer.init()
                    if stdout:
                        task_logger.command_output("packer_init_stdout", stdout, success=success)
                    if stderr and not success:
                        task_logger.warning(f"Packer init stderr:\n{stderr}", category=LogCategory.WARNING)
                    if not success:
                        raise Exception("Packer init failed")

                    # Merge user_vars with teams for Packer
                    packer_vars = {**user_vars["packer"]} if "packer" in user_vars else {}
                    packer_vars["image_name"] = image_name
                    packer_vars = encode_packer_vars(packer_vars)

                    task_logger.info(
                        "Packer variable keys",
                        category=LogCategory.OPERATION,
                        keys=list(packer_vars.keys()),
                    )

                    task_logger.info("Validating Packer template...", category=LogCategory.OPERATION)
                    success, stdout, stderr = packer.validate("template.pkr.hcl", packer_vars)
                    if not success:
                        raise Exception(f"Packer validation failed: {stderr}")

                    task_logger.info(
                        "Building Docker image (this may take several minutes)...", category=LogCategory.STATUS
                    )

                    success, output = packer.build("template.pkr.hcl", packer_vars)
                    if not success:
                        raise Exception(f"Packer build failed: {output}")

                    task_logger.success("Packer image built successfully", category=LogCategory.STATUS)
                    break
            except Exception as e:
                raise Exception(f"Packer error: {str(e)}")
            finally:
                build_lock.release()
        else:
            task_logger.info("No Packer template found, skipping image build", category=LogCategory.SYSTEM)

        # Phase 4: Terraform
        task_logger.phase("Terraform Deployment")
        terraform_dir = os.path.join(repo_path, "terraform")
        if not os.path.exists(terraform_dir):
            raise Exception(f"Terraform directory not found at {terraform_dir}")

        terraform = None
        terraform_vars: dict[str, Any] = {}
        try:
            terraform = TerraformExecutor(
                terraform_dir,
                env_vars=openstack_env,
                backend_conn_str=tfstate_conn_str,
                backend_schema_name=tfstate_schema,
            )

            task_logger.info("Initializing Terraform...", category=LogCategory.OPERATION)
            success, stdout, stderr = terraform.init()
            if not success:
                # Surface the real reason in the per-deployment log; the
                # module-level logger only writes to worker stdout, which the
                # frontend never sees.
                if stdout:
                    task_logger.command_output("terraform_init_stdout", stdout, returncode=1)
                if stderr:
                    task_logger.command_output("terraform_init_stderr", stderr, returncode=1)
                task_logger.error("Terraform init failed", category=LogCategory.ERROR)
                raise Exception("Terraform init failed")
            task_logger.success("Terraform initialization completed", category=LogCategory.STATUS)

            task_logger.info("Planning Terraform deployment...", category=LogCategory.OPERATION)

            # Merge user_vars with teams for Terraform. Pass nested
            # structures through encode_terraform_vars unchanged — the
            # previous implementation stripped backslashes and corrupted
            # escaped quotes inside the JSON for ``users``, which is what
            # caused the silent ``terraform plan`` failure.
            terraform_vars = {**user_vars["terraform"]} if "terraform" in user_vars else {}
            terraform_vars["image_name"] = image_name
            if teams:
                terraform_vars["users"] = teams
            terraform_vars = encode_terraform_vars(terraform_vars)

            task_logger.info(
                "Terraform variable keys",
                category=LogCategory.OPERATION,
                keys=list(terraform_vars.keys()),
            )

            success, stdout, stderr = terraform.plan(variables=terraform_vars)
            if not success:
                if stdout:
                    task_logger.command_output("terraform_plan_stdout", stdout, returncode=1)
                if stderr:
                    task_logger.command_output("terraform_plan_stderr", stderr, returncode=1)
                task_logger.error("Terraform plan failed", category=LogCategory.ERROR)
                raise Exception("Terraform plan failed")
            task_logger.success("Terraform plan completed successfully", category=LogCategory.STATUS)

            task_logger.info(
                "Applying Terraform configuration (this may take several minutes)...", category=LogCategory.STATUS
            )
            success, stdout, stderr = terraform.apply(variables=terraform_vars)
            if not success:
                if stdout:
                    task_logger.command_output("terraform_apply_stdout", stdout, returncode=1)
                if stderr:
                    task_logger.command_output("terraform_apply_stderr", stderr, returncode=1)
                task_logger.error("Terraform apply failed", category=LogCategory.ERROR)
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

            # Best-effort cleanup: a half-finished `terraform apply` typically
            # leaves orphaned OpenStack resources (networks, ports, volumes)
            # that quietly eat the project's quota. Run destroy with the same
            # variables so the apply graph can be reversed; ignore failures
            # here — we're already in the error path and re-raising below.
            if terraform is not None and terraform_dir and os.path.exists(terraform_dir):
                try:
                    task_logger.info(
                        "Running terraform destroy to clean up partially-applied resources",
                        category=LogCategory.OPERATION,
                    )
                    terraform.destroy(variables=terraform_vars)
                    # Refresh state after destroy so the persisted record reflects cleanup.
                    tf_state = collect_terraform_state()
                except Exception as cleanup_error:
                    task_logger.warning(
                        f"Terraform cleanup failed: {cleanup_error}",
                        category=LogCategory.WARNING,
                    )

            raise Exception(f"Terraform error: {str(e)}")

        task_logger.phase("Deployment Complete")
        task_logger.success(f"Deployment {deployment_id} completed successfully", category=LogCategory.STATUS)

        # Log summary
        summary = task_logger.get_summary()
        task_logger.info("Deployment summary", category=LogCategory.SYSTEM, **summary)

        task_logger.info("Terraform deployment output", category=LogCategory.SYSTEM, **outputs)

        result = {
            "status": "success",
            "deployment_id": deployment_id,
            "logs": task_logger.get_logs_dict(),
            "tf_state": tf_state,
            "commit_info": commit_info,
            "terraform_outputs": outputs,
        }

        # Return result (sent via task-succeeded event)
        return result

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
        # Shred the per-task clouds.yaml first so the credential file is gone
        # even if the repository cleanup below fails or hangs.
        if clouds_config is not None:
            try:
                clouds_config.__exit__(None, None, None)
            except Exception as e:
                task_logger.warning(
                    f"Per-task clouds.yaml cleanup failed: {e}",
                    category=LogCategory.WARNING,
                )
        if repo_path:
            try:
                git_service.cleanup_repository(repo_path)
                task_logger.success("Repository cleanup completed", category=LogCategory.SYSTEM)
            except Exception as e:
                task_logger.warning(f"Repository cleanup failed: {e}", category=LogCategory.WARNING)
