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


def _looks_like_file_var_value(value: Any) -> bool:
    """True if ``value`` matches the file-upload shape produced by
    the backend's ``_attach_files_to_user_input``: a non-empty
    mapping whose entries each carry a ``content_b64`` field plus
    the metadata triplet (name, size, content_type) — i.e. exactly
    the ``map(object(...))`` HCL contract.

    Used by :func:`_strip_file_vars` so destroy / cleanup-after-
    failure can drop ``@openstack:file:*``-marked variables before
    passing the var-set to ``terraform destroy``. Terraform
    validates *all* declared variables on every command — including
    destroy — so a half-filled or apply-only file-var would
    otherwise block the cleanup with the same schema error that
    killed the deploy.

    No legacy-shape compatibility — rows from the earlier wrapped
    format have to be cleaned up by hand (operator removes the
    deployment row + tfstate schema). Keeping the detector strict
    keeps the surface tight and makes future refactors easier to
    reason about.
    """
    if not isinstance(value, dict) or not value:
        return False
    for slot in value.values():
        if not isinstance(slot, dict):
            return False
        if "content_b64" not in slot:
            return False
    return True


def _strip_file_vars(terraform_vars: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``terraform_vars`` with file-shape entries removed.

    Pure function — never mutates the input. Used by destroy and the
    deploy cleanup-after-failure branches; deploy itself keeps the
    file vars because ``apply`` consumes them via cloud-init.
    """
    return {k: v for k, v in terraform_vars.items() if not _looks_like_file_var_value(v)}


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


# --- Phase tracking ----------------------------------------------------------
#
# Phases are pinned by name (a string the frontend renders as a stepper) and
# by index (1-based, used for the percent bar). The list is split in two so
# the worker can collapse the Packer block when a deployment doesn't need a
# Packer build — that decision is made after the git clone has finished and
# we can see whether ``packer/template.pkr.hcl`` exists.

PHASE_STARTING = "STARTING"
PHASE_OPENSTACK_SETUP = "OPENSTACK_SETUP"
PHASE_GIT_CLONE = "GIT_CLONE"
PHASE_CREDS_MATERIALISE = "CREDS_MATERIALISE"
PHASE_PACKER_INIT = "PACKER_INIT"
PHASE_PACKER_VALIDATE = "PACKER_VALIDATE"
PHASE_PACKER_BUILD = "PACKER_BUILD"
PHASE_TERRAFORM_INIT = "TERRAFORM_INIT"
PHASE_TERRAFORM_PLAN = "TERRAFORM_PLAN"
PHASE_TERRAFORM_APPLY = "TERRAFORM_APPLY"
PHASE_OUTPUTS_AND_CLEANUP = "OUTPUTS_AND_CLEANUP"
PHASE_TERRAFORM_DESTROY = "TERRAFORM_DESTROY"
PHASE_CLEANUP = "CLEANUP"
# Pause/resume share the deploy/destroy preamble (clone → clouds.yaml →
# terraform init for the pg-backed state pull) but their hot phase is
# a CLI-driven server stop/start, not a terraform apply or destroy.
# Naming the phase distinctly so the frontend stepper renders an honest
# label instead of reusing TERRAFORM_DESTROY for an action that doesn't
# touch terraform at all.
PHASE_SERVER_STOP = "SERVER_STOP"
PHASE_SERVER_START = "SERVER_START"

_PHASES_WITH_PACKER = (
    PHASE_STARTING,
    PHASE_OPENSTACK_SETUP,
    PHASE_GIT_CLONE,
    PHASE_CREDS_MATERIALISE,
    PHASE_PACKER_INIT,
    PHASE_PACKER_VALIDATE,
    PHASE_PACKER_BUILD,
    PHASE_TERRAFORM_INIT,
    PHASE_TERRAFORM_PLAN,
    PHASE_TERRAFORM_APPLY,
    PHASE_OUTPUTS_AND_CLEANUP,
)
_PHASES_WITHOUT_PACKER = (
    PHASE_STARTING,
    PHASE_OPENSTACK_SETUP,
    PHASE_GIT_CLONE,
    PHASE_CREDS_MATERIALISE,
    PHASE_TERRAFORM_INIT,
    PHASE_TERRAFORM_PLAN,
    PHASE_TERRAFORM_APPLY,
    PHASE_OUTPUTS_AND_CLEANUP,
)
# Destroy uses a shorter pipeline — no Packer (we don't need a fresh
# image to tear things down) and no plan (terraform destroy has its own
# planning step internally that we don't surface as its own progress
# phase).
_PHASES_DESTROY = (
    PHASE_STARTING,
    PHASE_OPENSTACK_SETUP,
    PHASE_GIT_CLONE,
    PHASE_CREDS_MATERIALISE,
    PHASE_TERRAFORM_INIT,
    PHASE_TERRAFORM_DESTROY,
    PHASE_CLEANUP,
)
# Pause / resume share the destroy preamble — git clone at the same
# release tag, materialise clouds.yaml, terraform init so we can pull
# the canonical state from the pg backend. The hot phase is the
# server stop / start loop. CLEANUP runs the repo shred and (for the
# log) a final state pull, mirroring destroy's tail.
_PHASES_PAUSE = (
    PHASE_STARTING,
    PHASE_OPENSTACK_SETUP,
    PHASE_GIT_CLONE,
    PHASE_CREDS_MATERIALISE,
    PHASE_TERRAFORM_INIT,
    PHASE_SERVER_STOP,
    PHASE_CLEANUP,
)
_PHASES_RESUME = (
    PHASE_STARTING,
    PHASE_OPENSTACK_SETUP,
    PHASE_GIT_CLONE,
    PHASE_CREDS_MATERIALISE,
    PHASE_TERRAFORM_INIT,
    PHASE_SERVER_START,
    PHASE_CLEANUP,
)


class _PhaseTracker:
    """Drives ``StructuredLogger.progress`` calls.

    The set of phases is fixed at construction time so the percent bar
    monotonically advances; ``mark()`` looks up the index of the named
    phase and sends a progress event with the correct ``idx/total``.
    """

    def __init__(self, logger: Any, phases: tuple[str, ...]):
        self._logger = logger
        self._phases = phases
        self._index_by_name = {name: i for i, name in enumerate(phases, start=1)}

    @property
    def total(self) -> int:
        return len(self._phases)

    def mark(self, phase_name: str, message: str = "") -> None:
        idx = self._index_by_name.get(phase_name)
        if idx is None:
            # Unknown phase — emit a transcript marker but no progress
            # update so the bar doesn't reset.
            self._logger.phase(phase_name)
            return
        # Buffer the readable phase header AND emit the live progress event.
        self._logger.phase(phase_name)
        self._logger.progress(phase_name, idx, self.total, message)


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
    task_logger = get_logger(f"deploy:{deployment_id}", correlation_id=deployment_id)

    # Wire the per-deployment logger to Celery's event bus. Every buffered
    # log entry now becomes a ``task-log`` event, and ``task_logger.progress``
    # emits ``task-progress``. The backend's listener picks both up and
    # forwards them via the in-process pubsub to any open SSE subscriber.
    bound_task = self

    def _emit(event_name: str, payload: dict[str, Any]) -> None:
        # ``deployment_id`` is duplicated into every event so the backend
        # listener doesn't need a DB lookup to figure out which deployment
        # the event belongs to.
        bound_task.send_event(event_name, deployment_id=deployment_id, **payload)

    task_logger.set_event_emitter(_emit)

    # Pessimistic phase set — assumes Packer. Demoted after git clone if
    # the cloned repo turns out to have no Packer template.
    phase_tracker = _PhaseTracker(task_logger, _PHASES_WITH_PACKER)

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
        phase_tracker.mark(PHASE_STARTING, "Starting deployment")
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
        phase_tracker.mark(PHASE_OPENSTACK_SETUP, "Validating OpenStack credentials")
        if not openstack_envelope:
            raise Exception("OpenStack credential envelope missing — user must upload credentials before deploying")
        task_logger.success(
            "OpenStack credential envelope received",
            category=LogCategory.STATUS,
        )

        # Phase 2: Git clone
        phase_tracker.mark(PHASE_GIT_CLONE, "Cloning repository")
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
        phase_tracker.mark(PHASE_CREDS_MATERIALISE, "Writing per-task clouds.yaml")
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
        image_tag = commit_info["hash"][:8] if commit_info and commit_info.get("hash") else release
        image_name = f"{app_id}-{image_tag}"

        # Decide once whether this deployment needs a Packer build, and
        # adapt the phase total accordingly so the percent bar is honest.
        # The pessimistic default (assume Packer) was set at task start;
        # if the cloned repo has no Packer template we drop those three
        # phases now so the next progress event lands on the right index.
        packer_file = os.path.join(repo_path, "packer", "template.pkr.hcl")
        if not os.path.exists(packer_file):
            phase_tracker = _PhaseTracker(task_logger, _PHASES_WITHOUT_PACKER)

        # The output callback feeds each line of subprocess output into the
        # task logger as a streaming entry, which then ships it via the
        # event emitter as a ``task-log`` event. Same callback for Packer
        # and Terraform — the tool name distinguishes them on the receiver.
        def _stream_line(tool: str, line: str) -> None:
            task_logger.tool_output_line(tool, line)

        # Phase 3: Packer (optional) — guarded by a Redis lock keyed on
        # (project_id, image_name) so two parallel workers can't both kick
        # off a build for the same image and end up with duplicate Glance
        # entries plus wasted compute.
        if os.path.exists(packer_file):
            project_id = openstack_envelope.get("project_id") or openstack_envelope.get("project_name") or "default"
            build_lock = PackerBuildLock(project_id, image_name)
            openstack_service = OpenStackService(env_vars=openstack_env)
            wait_announced = False
            try:
                while True:
                    # If the image already exists, skip the build and the lock.
                    exists, image_id = openstack_service.check_image_exists(image_name)
                    if exists:
                        task_logger.success(
                            f"Image '{image_name}' already exists (ID: {image_id}). Skipping Packer build.",
                            category=LogCategory.STATUS,
                        )
                        # Skip Packer phases on the progress bar — jump
                        # directly past Build, the next mark() call is for
                        # Terraform.
                        break

                    held = build_lock.acquire_or_wait()
                    if not held:
                        # Another worker is still building the same image.
                        # Surface this in the per-deployment log once so
                        # the frontend's live tail shows *something*
                        # during the 5-second poll cycles — without it the
                        # browser sees no events and looks frozen.
                        if not wait_announced:
                            task_logger.info(
                                f"Another worker is currently building image '{image_name}'. Waiting…",
                                category=LogCategory.STATUS,
                            )
                            wait_announced = True
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
                    packer = PackerExecutor(
                        os.path.join(repo_path, "packer"),
                        env_vars=openstack_env,
                        output_callback=_stream_line,
                    )

                    phase_tracker.mark(PHASE_PACKER_INIT, "Initializing Packer plugins")
                    success, stdout, stderr = packer.init()
                    if not success:
                        if stdout:
                            task_logger.command_output("packer_init_stdout", stdout, returncode=1)
                        if stderr:
                            task_logger.command_output("packer_init_stderr", stderr, returncode=1)
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

                    phase_tracker.mark(PHASE_PACKER_VALIDATE, "Validating Packer template")
                    success, stdout, stderr = packer.validate("template.pkr.hcl", packer_vars)
                    if not success:
                        raise Exception(f"Packer validation failed: {stderr}")

                    phase_tracker.mark(PHASE_PACKER_BUILD, "Building image (this may take minutes)")
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
                output_callback=_stream_line,
            )

            phase_tracker.mark(PHASE_TERRAFORM_INIT, "Initializing Terraform")
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

            # File-upload variables can balloon the JSON-encoded value
            # of a single -var to several hundred KB. The Nova metadata
            # service caps cloud-init user_data at ~64 KB compressed
            # (~150-200 KB raw) — beyond that, the boot fails after
            # apply with an opaque message. We can't know exactly how
            # the app's template fans the data into user_data, but a
            # per-variable warning at >120 KB lands the heads-up in
            # the worker log so the cause is visible without ssh-ing
            # into a half-broken VM.
            _log_bytes_per_var_warn = 120 * 1024
            for _vname, _vstr in terraform_vars.items():
                if isinstance(_vstr, str) and len(_vstr) > _log_bytes_per_var_warn:
                    task_logger.warning(
                        f"Terraform variable '{_vname}' is "
                        f"{len(_vstr) // 1024} KB encoded — close to the "
                        "cloud-init user_data limit; the VM may fail to "
                        "boot if the template inlines the full value.",
                        category=LogCategory.WARNING,
                    )

            task_logger.info(
                "Terraform variable keys",
                category=LogCategory.OPERATION,
                keys=list(terraform_vars.keys()),
            )

            phase_tracker.mark(PHASE_TERRAFORM_PLAN, "Planning Terraform deployment")
            success, stdout, stderr = terraform.plan(variables=terraform_vars)
            if not success:
                if stdout:
                    task_logger.command_output("terraform_plan_stdout", stdout, returncode=1)
                if stderr:
                    task_logger.command_output("terraform_plan_stderr", stderr, returncode=1)
                task_logger.error("Terraform plan failed", category=LogCategory.ERROR)
                raise Exception("Terraform plan failed")
            task_logger.success("Terraform plan completed successfully", category=LogCategory.STATUS)

            phase_tracker.mark(PHASE_TERRAFORM_APPLY, "Applying configuration (this may take minutes)")
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
            phase_tracker.mark(PHASE_OUTPUTS_AND_CLEANUP, "Collecting outputs")
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
                    # Rebuild the var-set from the raw user_vars,
                    # this time without the file payloads. Destroy
                    # doesn't need the cloud-init bytes but Terraform
                    # still validates every declared var on every
                    # run. A half-filled file-var carried over from
                    # the broken apply would otherwise reject the
                    # cleanup with the same schema error that killed
                    # the apply, leaving orphan OpenStack resources.
                    cleanup_tf_vars = (
                        _strip_file_vars(user_vars.get("terraform") or {})
                    )
                    cleanup_tf_vars["image_name"] = image_name
                    if teams:
                        cleanup_tf_vars["users"] = teams
                    terraform.destroy(variables=encode_terraform_vars(cleanup_tf_vars))
                    # Refresh state after destroy so the persisted record reflects cleanup.
                    tf_state = collect_terraform_state()
                except Exception as cleanup_error:
                    task_logger.warning(
                        f"Terraform cleanup failed: {cleanup_error}",
                        category=LogCategory.WARNING,
                    )

            raise Exception(f"Terraform error: {str(e)}")

        # Final 100% — same phase name as the outputs phase, just with a
        # closing message. The progress event for OUTPUTS_AND_CLEANUP was
        # already emitted above when we collected outputs; this second mark
        # would land on the same index, which is harmless on the bar but
        # would buffer a duplicate phase header. So just log success.
        task_logger.success(f"Deployment {deployment_id} completed successfully", category=LogCategory.STATUS)

        # Log summary
        summary = task_logger.get_summary()
        task_logger.info("Deployment summary", category=LogCategory.SYSTEM, **summary)

        if outputs:
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


@celery_app.task(bind=True, name="tasks.destroy_deployment")
def destroy_deployment(
    self,
    deployment_id: str,
    app_id: str,
    app_git_link: str,
    release: str,
    user_vars: dict[str, Any],
    teams: dict[str, list] = None,
    openstack_envelope: dict[str, Any] | None = None,
):
    """Tear down a deployment via ``terraform destroy``.

    Mirrors ``deploy_application``'s setup (git clone at the same release
    tag, materialise the per-task clouds.yaml, configure the same pg
    backend schema) so Terraform sees the exact same state it built.
    Then runs ``terraform destroy -auto-approve`` instead of
    ``plan + apply``. Same Packer image is left in Glance so a future
    deploy of the same commit doesn't have to rebuild it.

    All progress and log events flow through the same ``StructuredLogger``
    + Celery custom-event pipeline as deploy, so the frontend's live
    SSE stream renders the destroy run identically to a deploy.

    Args mirror ``deploy_application`` so the backend can re-dispatch
    the same persisted values without translation.
    """
    task_logger = get_logger(f"destroy:{deployment_id}", correlation_id=deployment_id)

    bound_task = self

    def _emit(event_name: str, payload: dict[str, Any]) -> None:
        bound_task.send_event(event_name, deployment_id=deployment_id, **payload)

    task_logger.set_event_emitter(_emit)
    phase_tracker = _PhaseTracker(task_logger, _PHASES_DESTROY)

    repo_path = None
    tf_state: str | None = None
    commit_info: dict[str, Any] | None = None
    terraform_dir: str | None = None
    openstack_env: dict[str, str] = {}
    clouds_config: PerTaskCloudsConfig | None = None

    tfstate_conn_str = settings.TFSTATE_DATABASE_URL or None
    tfstate_schema = _tfstate_schema_name(deployment_id)

    if teams is None:
        teams = {}

    def _stream_line(tool: str, line: str) -> None:
        task_logger.tool_output_line(tool, line)

    def collect_terraform_state():
        """Snapshot the post-destroy state for the task row.

        Same shape as in ``deploy_application``. After a successful
        destroy this should report ``resources: []`` — handy for
        debugging if the DB row claims destroyed but Glance still shows
        servers.
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
            return terraform.state_pull()
        except Exception as e:
            task_logger.warning(f"Could not pull terraform state: {e}", category=LogCategory.WARNING)
            return None

    try:
        phase_tracker.mark(PHASE_STARTING, "Starting destroy")
        task_logger.resource_info(
            "deployment",
            deployment_id,
            app_id=app_id,
            git_url=app_git_link,
            release=release,
            user_vars_keys=list(user_vars.keys()),
            teams_keys=list(teams.keys()),
            action="destroy",
        )

        phase_tracker.mark(PHASE_OPENSTACK_SETUP, "Validating OpenStack credentials")
        if not openstack_envelope:
            raise Exception("OpenStack credential envelope missing — cannot destroy without credentials")
        task_logger.success("OpenStack credential envelope received", category=LogCategory.STATUS)

        phase_tracker.mark(PHASE_GIT_CLONE, "Cloning repository at original release tag")
        task_logger.info(
            f"Cloning {app_git_link} at {release} (same ref as the original deploy "
            "so terraform code matches the pg-backend state)",
            category=LogCategory.OPERATION,
        )
        try:
            repo_path = git_service.clone_release(git_url=app_git_link, deployment_id=deployment_id, tag=release)
            try:
                import git as _git

                repo = _git.Repo(repo_path)
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

        phase_tracker.mark(PHASE_CREDS_MATERIALISE, "Writing per-task clouds.yaml")
        clouds_config = PerTaskCloudsConfig(openstack_envelope, work_dir=repo_path)
        openstack_env = clouds_config.__enter__()
        task_logger.success("Per-task clouds.yaml written", category=LogCategory.STATUS)

        # Reconstruct the same image_name the deploy task used so the
        # variables match what terraform's state expects to validate.
        # Glance still has the image, even if we won't be using it; the
        # variable just has to be a non-empty string that satisfies the
        # HCL declaration.
        image_tag = commit_info["hash"][:8] if commit_info and commit_info.get("hash") else release
        image_name = f"{app_id}-{image_tag}"

        terraform_dir = os.path.join(repo_path, "terraform")
        if not os.path.exists(terraform_dir):
            raise Exception(f"Terraform directory not found at {terraform_dir}")

        # Drop ``@openstack:file:*`` variable values before passing
        # the var-set to terraform destroy. Files are only consumed
        # at apply-time (cloud-init write_files); destroy doesn't
        # need them, but Terraform validates every declared var on
        # every run. A half-filled file-var carried over from a
        # broken deploy would otherwise reject destroy with the same
        # schema error that killed the deploy in the first place.
        terraform_vars = {**user_vars["terraform"]} if "terraform" in user_vars else {}
        terraform_vars = _strip_file_vars(terraform_vars)
        terraform_vars["image_name"] = image_name
        if teams:
            terraform_vars["users"] = teams
        terraform_vars = encode_terraform_vars(terraform_vars)

        terraform = TerraformExecutor(
            terraform_dir,
            env_vars=openstack_env,
            backend_conn_str=tfstate_conn_str,
            backend_schema_name=tfstate_schema,
            output_callback=_stream_line,
        )

        phase_tracker.mark(PHASE_TERRAFORM_INIT, "Initializing Terraform")
        success, stdout, stderr = terraform.init()
        if not success:
            if stdout:
                task_logger.command_output("terraform_init_stdout", stdout, returncode=1)
            if stderr:
                task_logger.command_output("terraform_init_stderr", stderr, returncode=1)
            raise Exception("Terraform init failed")
        task_logger.success("Terraform initialization completed", category=LogCategory.STATUS)

        phase_tracker.mark(PHASE_TERRAFORM_DESTROY, "Destroying resources")
        success, stdout, stderr = terraform.destroy(variables=terraform_vars)
        if not success:
            if stdout:
                task_logger.command_output("terraform_destroy_stdout", stdout, returncode=1)
            if stderr:
                task_logger.command_output("terraform_destroy_stderr", stderr, returncode=1)
            raise Exception("Terraform destroy failed")
        task_logger.success("Terraform resources destroyed", category=LogCategory.STATUS)

        phase_tracker.mark(PHASE_CLEANUP, "Pulling final state")
        tf_state = collect_terraform_state()

        task_logger.success(f"Deployment {deployment_id} destroyed successfully", category=LogCategory.STATUS)

        summary = task_logger.get_summary()
        task_logger.info("Destroy summary", category=LogCategory.SYSTEM, **summary)

        return {
            "status": "success",
            "deployment_id": deployment_id,
            "logs": task_logger.get_logs_dict(),
            "tf_state": tf_state,
            "commit_info": commit_info,
            # No outputs — destroy doesn't produce any. Field is kept for
            # event-listener parity with deploy_application's payload.
            "terraform_outputs": {},
        }

    except Exception as e:
        task_logger.exception(f"Destroy failed: {str(e)}", exception=e, deployment_id=deployment_id)
        if not tf_state:
            tf_state = collect_terraform_state()
        raise Failure(
            message=str(e),
            deployment_id=deployment_id,
            logs_dict=task_logger.get_logs_dict(),
            tf_state=tf_state,
            commit_info=commit_info,
            terraform_outputs={},
        )

    finally:
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


# ----------------------------------------------------------------
# PAUSE / RESUME — compute-instance-only lifecycle
# ----------------------------------------------------------------
#
# Both tasks share the destroy preamble (git clone at the same release
# tag → per-task clouds.yaml → terraform init pointed at the
# pg backend) so we can pull the canonical terraform state and read
# back which compute instances belong to this deployment. The hot
# phase is then a CLI-driven stop/start loop — terraform itself is
# untouched, the state file is left as-is, and the next deploy/destroy
# can resume from exactly the same point.
#
# Why state pull and not server tagging?
#   * No app template needs to be modified — the template's existing
#     ``openstack_compute_instance_v2`` resources are the source of
#     truth. Tag-based discovery would require every app to set a
#     specific tag, easy to forget.
#   * The pg backend already holds the canonical state, so the pull
#     is local-Postgres-fast.
#
# CLI idempotency means the loop can re-run on retry without us
# tracking which servers already stopped/started.


def _extract_compute_instance_ids(state_json: str | None) -> list[str]:
    """Return server IDs from a terraform pg-backend state dump.

    Terraform's serialised state shape is
    ``{"resources": [{"type": "...", "instances": [{"attributes": {"id": "..."}}]}]}``.
    Filtered to ``openstack_compute_instance_v2`` so we only stop/start
    Nova servers, not volumes / networks / security groups.

    Returns an empty list on any parsing trouble — the caller can then
    decide whether "no servers found" is a hard error (deploy never
    actually ran) or a no-op success (everything already torn down).
    """
    if not state_json:
        return []
    try:
        state = json.loads(state_json) if isinstance(state_json, str) else state_json
    except (TypeError, json.JSONDecodeError):
        return []

    ids: list[str] = []
    for resource in state.get("resources", []):
        if resource.get("type") != "openstack_compute_instance_v2":
            continue
        for instance in resource.get("instances", []):
            attrs = instance.get("attributes") or {}
            sid = attrs.get("id")
            if sid:
                ids.append(sid)
    return ids


def _run_compute_lifecycle(
    self,
    deployment_id: str,
    app_id: str,
    app_git_link: str,
    release: str,
    user_vars: dict[str, Any],
    teams: dict[str, list] | None,
    openstack_envelope: dict[str, Any] | None,
    *,
    action: str,  # "pause" | "resume" — only for log/error labels
    phases: tuple[str, ...],
    server_phase: str,
    server_op: str,  # "stop" | "start"
):
    """Shared body for ``pause_deployment`` / ``resume_deployment``.

    Mirrors :func:`destroy_deployment`'s preamble exactly so the two
    paths stay easy to reason about. Diverges only at the hot phase:
    instead of ``terraform destroy``, we pull the state, extract
    every compute instance's ID, and shell out to
    ``openstack server stop|start`` for each.

    Failures during the per-server loop are accumulated and re-raised
    once with a list of which servers failed — so the user sees
    "stopped 4/5; failed: web-1: locked task" instead of just "pause
    failed" without any pointer to which instance is stuck.
    """
    label = f"{action}:{deployment_id}"
    task_logger = get_logger(label, correlation_id=deployment_id)

    bound_task = self

    def _emit(event_name: str, payload: dict[str, Any]) -> None:
        bound_task.send_event(event_name, deployment_id=deployment_id, **payload)

    task_logger.set_event_emitter(_emit)
    phase_tracker = _PhaseTracker(task_logger, phases)

    repo_path = None
    terraform_dir: str | None = None
    openstack_env: dict[str, str] = {}
    clouds_config: PerTaskCloudsConfig | None = None

    tfstate_conn_str = settings.TFSTATE_DATABASE_URL or None
    tfstate_schema = _tfstate_schema_name(deployment_id)

    if teams is None:
        teams = {}

    def _stream_line(tool: str, line: str) -> None:
        task_logger.tool_output_line(tool, line)

    try:
        phase_tracker.mark(PHASE_STARTING, f"Starting {action}")
        task_logger.resource_info(
            "deployment",
            deployment_id,
            app_id=app_id,
            git_url=app_git_link,
            release=release,
            action=action,
        )

        phase_tracker.mark(PHASE_OPENSTACK_SETUP, "Validating OpenStack credentials")
        if not openstack_envelope:
            raise Exception(f"OpenStack credential envelope missing — cannot {action} without credentials")
        task_logger.success("OpenStack credential envelope received", category=LogCategory.STATUS)

        phase_tracker.mark(PHASE_GIT_CLONE, "Cloning repository at original release tag")
        try:
            repo_path = git_service.clone_release(
                git_url=app_git_link,
                deployment_id=deployment_id,
                tag=release,
            )
            task_logger.success("Repository cloned", category=LogCategory.STATUS)
        except Exception as e:
            raise Exception(f"Git clone failed: {str(e)}")

        phase_tracker.mark(PHASE_CREDS_MATERIALISE, "Writing per-task clouds.yaml")
        clouds_config = PerTaskCloudsConfig(openstack_envelope, work_dir=repo_path)
        openstack_env = clouds_config.__enter__()
        task_logger.success("Per-task clouds.yaml written", category=LogCategory.STATUS)

        terraform_dir = os.path.join(repo_path, "terraform")
        if not os.path.exists(terraform_dir):
            raise Exception(f"Terraform directory not found at {terraform_dir}")

        terraform = TerraformExecutor(
            terraform_dir,
            env_vars=openstack_env,
            backend_conn_str=tfstate_conn_str,
            backend_schema_name=tfstate_schema,
            output_callback=_stream_line,
        )

        phase_tracker.mark(PHASE_TERRAFORM_INIT, "Initializing Terraform")
        success, stdout, stderr = terraform.init()
        if not success:
            if stdout:
                task_logger.command_output("terraform_init_stdout", stdout, returncode=1)
            if stderr:
                task_logger.command_output("terraform_init_stderr", stderr, returncode=1)
            raise Exception("Terraform init failed")
        task_logger.success("Terraform initialization completed", category=LogCategory.STATUS)

        # Pull the canonical state from the pg backend, then walk it
        # to find every compute instance attached to this deployment.
        # An empty list usually means the deployment never reached a
        # successful apply — surface that as a hard error rather than
        # a silent no-op so the user doesn't think pause "worked" on
        # an empty deployment.
        state_dump = terraform.state_pull()
        server_ids = _extract_compute_instance_ids(state_dump)
        if not server_ids:
            raise Exception(
                "No compute instances found in terraform state — "
                f"nothing to {action}. The deployment may have been "
                "torn down already or never reached a successful apply."
            )
        task_logger.info(
            f"{len(server_ids)} compute instance(s) found",
            category=LogCategory.OPERATION,
            server_ids=server_ids,
        )

        phase_tracker.mark(
            server_phase,
            f"{'Stopping' if server_op == 'stop' else 'Starting'} {len(server_ids)} server(s)",
        )

        openstack_service = OpenStackService(env_vars=openstack_env)
        op_method = openstack_service.server_stop if server_op == "stop" else openstack_service.server_start

        failures: list[tuple[str, str]] = []
        for sid in server_ids:
            # Optional pre-flight: log the human name + power state so
            # the per-deployment log is readable. We never fail on
            # show() — it's purely cosmetic.
            info = openstack_service.server_show(sid)
            label_str = f"{info.get('name', sid)}" if info else sid
            current = info.get("status") if info else None
            task_logger.info(
                f"{server_op} {label_str} (status: {current or 'unknown'})",
                category=LogCategory.OPERATION,
                server_id=sid,
            )

            ok, err = op_method(sid)
            if ok:
                task_logger.success(
                    f"{label_str}: {server_op} OK",
                    category=LogCategory.STATUS,
                )
            else:
                task_logger.error(
                    f"{label_str}: {server_op} failed: {err}",
                    category=LogCategory.ERROR,
                    server_id=sid,
                )
                failures.append((sid, err or "unknown error"))

        if failures:
            joined = "; ".join(f"{sid}: {err}" for sid, err in failures)
            raise Exception(f"{action} failed for {len(failures)}/{len(server_ids)} server(s): {joined}")

        phase_tracker.mark(PHASE_CLEANUP, "Pulling final state snapshot")
        # State doesn't change for pause/resume (the resources still
        # exist, just in a different power state), but we pull it
        # again so the task row gets a fresh snapshot for debugging.
        try:
            tf_state_post = terraform.state_pull()
        except Exception as e:
            task_logger.warning(
                f"Could not pull terraform state post-{action}: {e}",
                category=LogCategory.WARNING,
            )
            tf_state_post = state_dump

        task_logger.success(
            f"Deployment {deployment_id} {action}d successfully",
            category=LogCategory.STATUS,
        )

        return {
            "status": "success",
            "deployment_id": deployment_id,
            "logs": task_logger.get_logs_dict(),
            "tf_state": tf_state_post,
            "commit_info": None,
            # Pause/resume don't generate or change terraform outputs —
            # field is kept for event-listener parity with the deploy
            # / destroy payload shape.
            "terraform_outputs": {},
        }

    except Exception as e:
        task_logger.exception(f"{action} failed: {str(e)}", exception=e, deployment_id=deployment_id)
        raise Failure(
            message=str(e),
            deployment_id=deployment_id,
            logs_dict=task_logger.get_logs_dict(),
            tf_state=None,
            commit_info=None,
            terraform_outputs={},
        )

    finally:
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


@celery_app.task(bind=True, name="tasks.pause_deployment")
def pause_deployment(
    self,
    deployment_id: str,
    app_id: str,
    app_git_link: str,
    release: str,
    user_vars: dict[str, Any],
    teams: dict[str, list] = None,
    openstack_envelope: dict[str, Any] | None = None,
):
    """Halt a deployment by stopping all of its compute instances.

    Volumes and networks are untouched, so resume restores the same
    instances byte-for-byte. The terraform state is also untouched,
    so a subsequent destroy proceeds normally (terraform destroy is
    happy to tear down SHUTOFF instances).
    """
    return _run_compute_lifecycle(
        self,
        deployment_id,
        app_id,
        app_git_link,
        release,
        user_vars,
        teams,
        openstack_envelope,
        action="pause",
        phases=_PHASES_PAUSE,
        server_phase=PHASE_SERVER_STOP,
        server_op="stop",
    )


@celery_app.task(bind=True, name="tasks.resume_deployment")
def resume_deployment(
    self,
    deployment_id: str,
    app_id: str,
    app_git_link: str,
    release: str,
    user_vars: dict[str, Any],
    teams: dict[str, list] = None,
    openstack_envelope: dict[str, Any] | None = None,
):
    """Resume a paused deployment by starting all of its compute instances.

    Mirrors :func:`pause_deployment`'s preamble exactly so the two
    code paths stay symmetric and easy to compare side-by-side.
    """
    return _run_compute_lifecycle(
        self,
        deployment_id,
        app_id,
        app_git_link,
        release,
        user_vars,
        teams,
        openstack_envelope,
        action="resume",
        phases=_PHASES_RESUME,
        server_phase=PHASE_SERVER_START,
        server_op="start",
    )
