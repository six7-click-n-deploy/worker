import os
import logging
import json
from typing import Dict, Any, Optional, List
from datetime import datetime
from .celery_app import celery_app
from .services import git_service, TerraformExecutor, PackerExecutor, openstack_auth_service

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class DeploymentFailure(Exception):
    """Custom exception that carries deployment details for Celery"""
    def __init__(
        self,
        message: str,
        deployment_id: str,
        logs: List[str],
        tf_state: Optional[str] = None,
        commit_info: Optional[str] = None,
        terraform_outputs: Optional[Dict[str, Any]] = None
    ):
        self.deployment_id = deployment_id
        self.logs = logs
        self.tf_state = tf_state
        self.commit_info = commit_info
        self.terraform_outputs = terraform_outputs
        
        # Encode all data as JSON in the exception message
        data = {
            "error": message,
            "deployment_id": deployment_id,
            "logs": logs,
            "tf_state": tf_state,
            "commit_info": commit_info,
            "terraform_outputs": terraform_outputs
        }
        super().__init__(json.dumps(data))
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert exception data to dict for serialization"""
        return json.loads(str(self))

@celery_app.task(bind=True, name="tasks.deploy_application")
def deploy_application(self, deployment_id: str, app_git_link: str, release: str, user_vars: Dict[str, Any]):
    """
    Deploy an application using Terraform and Packer
    
    Args:
        deployment_id: UUID of the deployment
        app_git_link: Git repo URL
        release: Tag/Release to checkout
        user_vars: User variables for Packer/Terraform
        
    Returns:
        dict: status, logs, tf_state, commit_info, terraform_outputs
    """
    logs = []
    repo_path = None
    tf_state = None
    outputs = None
    commit_info = None
    status = "unknown"
    terraform_dir = None

    def log(msg):
        """Log message"""
        timestamp = datetime.utcnow().isoformat()
        logger.info(msg)
        logs.append({"timestamp": timestamp, "message": msg})
    
    def collect_terraform_state():
        """Try to collect terraform state even on failure"""
        if terraform_dir:
            tfstate_path = os.path.join(terraform_dir, "terraform.tfstate")
            if os.path.exists(tfstate_path):
                try:
                    with open(tfstate_path, "r") as f:
                        return f.read()
                except Exception as e:
                    log(f"Warning: Could not read terraform state: {e}")
        return None
    
    def collect_terraform_outputs():
        """Try to collect terraform outputs even on partial success"""
        if terraform_dir:
            try:
                terraform = TerraformExecutor(terraform_dir, env_vars=openstack_env)
                return terraform.output()
            except Exception as e:
                log(f"Warning: Could not read terraform outputs: {e}")
        return None

    try:
        log(f"Starting deployment {deployment_id} for release {release}")
        
        # Phase 1: OpenStack credentials
        log("Setting up OpenStack credentials...")
        try:
            openstack_env = openstack_auth_service.get_environment_variables()
            if not openstack_env or not openstack_env.get('OS_AUTH_URL'):
                raise Exception("OpenStack credentials not configured or missing OS_AUTH_URL")
            log("✓ OpenStack credentials loaded successfully")
        except Exception as e:
            raise Exception(f"OpenStack credentials error: {str(e)}")
        
        # Phase 2: Git clone
        log(f"Cloning repository {app_git_link} (release: {release})")
        try:
            repo_path = git_service.clone_release(
                git_url=app_git_link,
                deployment_id=deployment_id,
                tag=release
            )
            
            # Get commit info
            try:
                import git
                repo = git.Repo(repo_path)
                commit = repo.head.commit
                commit_info = {
                    "hash": commit.hexsha,
                    "message": commit.message.strip(),
                    "author": str(commit.author),
                    "date": commit.committed_datetime.isoformat()
                }
                log(f"✓ Repository cloned at commit {commit.hexsha[:8]}")
            except Exception as e:
                log(f"Warning: Could not extract commit info: {e}")
                
        except Exception as e:
            raise Exception(f"Git clone failed: {str(e)}")
        
        # Phase 3: Packer (optional)
        packer_file = os.path.join(repo_path, "packer", "template.pkr.hcl")
        if os.path.exists(packer_file):
            log("Found Packer template, building image...")
            try:
                packer = PackerExecutor(os.path.join(repo_path, "packer"), env_vars=openstack_env)
                
                log("Running packer init...")
                success, stdout, stderr = packer.init()
                if stdout:
                    log(f"Packer init output:\\n{stdout}")
                if stderr:
                    log(f"Packer init errors:\\n{stderr}")
                if not success:
                    raise Exception("Packer init failed")
                
                packer_vars = {}  # user_vars can be filtered for packer
                log(f"Packer variables: {packer_vars}")
                
                log("Validating packer template...")
                success, stdout, stderr = packer.validate("template.pkr.hcl", packer_vars)
                if stdout:
                    log(f"Packer validate output:\\n{stdout}")
                if stderr:
                    log(f"Packer validate errors:\\n{stderr}")
                if not success:
                    raise Exception("Packer template validation failed")
                
                log("Building image with packer (this may take several minutes)...")
                success, output = packer.build("template.pkr.hcl", packer_vars)
                if output:
                    # Log only last 100 lines to avoid huge logs
                    lines = output.split("\\n")
                    if len(lines) > 100:
                        log(f"Packer build output (last 100 lines):\\n{chr(10).join(lines[-100:])}")
                    else:
                        log(f"Packer build output:\\n{output}")
                if not success:
                    raise Exception("Packer build failed")
                
                log("✓ Packer image built successfully")
            except Exception as e:
                raise Exception(f"Packer error: {str(e)}")
        else:
            log("No Packer template found, skipping image build")
        
        # Phase 4: Terraform
        terraform_dir = os.path.join(repo_path, "terraform")
        if not os.path.exists(terraform_dir):
            raise Exception(f"Terraform directory not found at {terraform_dir}")
        
        log("Running Terraform deployment...")
        try:
            terraform = TerraformExecutor(terraform_dir, env_vars=openstack_env)
            
            log("Running terraform init...")
            success, stdout, stderr = terraform.init()
            if stdout:
                # Truncate long outputs
                lines = stdout.split("\n")
                if len(lines) > 50:
                    log(f"Terraform init output (last 50 lines):\n{chr(10).join(lines[-50:])}")
                else:
                    log(f"Terraform init output:\n{stdout}")
            if stderr:
                log(f"Terraform init stderr:\n{stderr}")
            if not success:
                raise Exception("Terraform init failed")
            
            log("Running terraform plan...")
            success, stdout, stderr = terraform.plan(variables=user_vars)
            if stdout:
                # Plan output can be huge, truncate intelligently
                lines = stdout.split("\n")
                if len(lines) > 100:
                    log(f"Terraform plan output (last 100 lines):\n{chr(10).join(lines[-100:])}")
                else:
                    log(f"Terraform plan output:\n{stdout}")
            if stderr:
                log(f"Terraform plan stderr:\n{stderr}")
            if not success:
                raise Exception("Terraform plan failed")
            
            log("Running terraform apply (this may take several minutes)...")
            success, stdout, stderr = terraform.apply(variables=user_vars)
            if stdout:
                # Apply output shows resource creation, keep more of it
                lines = stdout.split("\n")
                if len(lines) > 200:
                    log(f"Terraform apply output (last 200 lines):\n{chr(10).join(lines[-200:])}")
                else:
                    log(f"Terraform apply output:\n{stdout}")
            if stderr:
                log(f"Terraform apply stderr:\n{stderr}")
            if not success:
                raise Exception("Terraform apply failed")
            
            log("✓ Terraform apply completed")
            
            # Collect outputs and state
            outputs = collect_terraform_outputs()
            tf_state = collect_terraform_state()
            
            if outputs:
                log(f"Terraform outputs: {outputs}")
            
        except Exception as e:
            # Try to collect partial results even on failure
            tf_state = collect_terraform_state()
            outputs = collect_terraform_outputs()
            raise Exception(f"Terraform error: {str(e)}")
        
        status = "success"
        log(f"✓ Deployment {deployment_id} completed successfully")
        
        # Return result (sent via task-succeeded event)
        return {
            "status": status,
            "deployment_id": deployment_id,
            "logs": logs,
            "tf_state": tf_state,
            "commit_info": commit_info,
            "terraform_outputs": outputs
        }
        
    except Exception as e:
        status = "failed"
        error_msg = str(e)
        log(f"✗ Deployment {deployment_id} failed: {error_msg}")
        
        # Try to collect any available state/outputs even on failure
        if not tf_state:
            tf_state = collect_terraform_state()
        if not outputs:
            outputs = collect_terraform_outputs()
        
        # Raise custom exception with all details - Celery will send task-failed event
        raise DeploymentFailure(
            message=error_msg,
            deployment_id=deployment_id,
            logs=logs,
            tf_state=tf_state,
            commit_info=commit_info,
            terraform_outputs=outputs
        )
        
    finally:
        if repo_path:
            try:
                git_service.cleanup_repository(repo_path)
                log("✓ Repository cleanup completed")
            except Exception as e:
                log(f"Warning: Repository cleanup failed: {e}")

@celery_app.task(bind=True, name="tasks.delete_deployment")
def delete_deployment(self, deployment_id: str, app_git_link: str, release: str, user_vars: Dict[str, Any]):
    """
    Delete/destroy a deployment using Terraform
    
    Args:
        deployment_id: UUID of the deployment
        app_git_link: Git repo URL
        release: Tag/Release to checkout
        user_vars: User variables for Terraform
        
    Returns:
        dict: status, logs, tf_state
    """
    logs = []
    repo_path = None
    status = "unknown"
    tf_state = None
    terraform_dir = None

    def log(msg):
        """Log message"""
        timestamp = datetime.utcnow().isoformat()
        logger.info(msg)
        logs.append({"timestamp": timestamp, "message": msg})
    
    def collect_terraform_state():
        """Try to collect terraform state"""
        if terraform_dir:
            tfstate_path = os.path.join(terraform_dir, "terraform.tfstate")
            if os.path.exists(tfstate_path):
                try:
                    with open(tfstate_path, "r") as f:
                        return f.read()
                except Exception as e:
                    log(f"Warning: Could not read terraform state: {e}")
        return None

    try:
        log(f"Starting deletion of deployment {deployment_id} (release: {release})")
        
        # Phase 1: OpenStack credentials
        log("Setting up OpenStack credentials...")
        try:
            openstack_env = openstack_auth_service.get_environment_variables()
            if not openstack_env or not openstack_env.get('OS_AUTH_URL'):
                raise Exception("OpenStack credentials not configured or missing OS_AUTH_URL")
            log("✓ OpenStack credentials loaded successfully")
        except Exception as e:
            raise Exception(f"OpenStack credentials error: {str(e)}")
        
        # Phase 2: Git clone
        log(f"Cloning repository {app_git_link} (release: {release})")
        try:
            repo_path = git_service.clone_release(
                git_url=app_git_link,
                deployment_id=deployment_id,
                tag=release
            )
            log("✓ Repository cloned successfully")
        except Exception as e:
            raise Exception(f"Git clone failed: {str(e)}")
        
        # Phase 3: Terraform destroy
        terraform_dir = os.path.join(repo_path, "terraform")
        if not os.path.exists(terraform_dir):
            raise Exception(f"Terraform directory not found at {terraform_dir}")
        
        log("Running Terraform destroy...")
        try:
            terraform = TerraformExecutor(terraform_dir, env_vars=openstack_env)
            
            log("Running terraform init...")
            success, stdout, stderr = terraform.init()
            if stdout:
                lines = stdout.split("\n")
                if len(lines) > 50:
                    log(f"Terraform init output (last 50 lines):\n{chr(10).join(lines[-50:])}")
                else:
                    log(f"Terraform init output:\n{stdout}")
            if stderr:
                log(f"Terraform init stderr:\n{stderr}")
            if not success:
                raise Exception("Terraform init failed")
            
            log("Running terraform destroy (this may take several minutes)...")
            success, stdout, stderr = terraform.destroy(variables=user_vars)
            if stdout:
                lines = stdout.split("\n")
                if len(lines) > 150:
                    log(f"Terraform destroy output (last 150 lines):\n{chr(10).join(lines[-150:])}")
                else:
                    log(f"Terraform destroy output:\n{stdout}")
            if stderr:
                log(f"Terraform destroy stderr:\n{stderr}")
            if not success:
                raise Exception("Terraform destroy failed")
            
            log("✓ Terraform destroy completed")
            
            # Collect final state
            tf_state = collect_terraform_state()
            
        except Exception as e:
            # Try to collect state even on failure
            tf_state = collect_terraform_state()
            raise Exception(f"Terraform destroy error: {str(e)}")
        
        status = "success"
        log(f"✓ Deployment {deployment_id} deleted successfully")
        
        return {
            "status": status,
            "deployment_id": deployment_id,
            "logs": logs,
            "tf_state": tf_state
        }
        
    except Exception as e:
        status = "failed"
        error_msg = str(e)
        log(f"✗ Deployment deletion {deployment_id} failed: {error_msg}")
        
        # Try to collect state even on failure
        if not tf_state:
            tf_state = collect_terraform_state()
        
        # Raise custom exception - Celery will send task-failed event
        raise DeploymentFailure(
            message=error_msg,
            deployment_id=deployment_id,
            logs=logs,
            tf_state=tf_state,
            commit_info=None,
            terraform_outputs=None
        )
        
    finally:
        if repo_path:
            try:
                git_service.cleanup_repository(repo_path)
                log("✓ Repository cleanup completed")
            except Exception as e:
                log(f"Warning: Repository cleanup failed: {e}")

