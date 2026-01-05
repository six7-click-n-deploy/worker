import os
import logging
from typing import Dict, Any
from datetime import datetime
from .celery_app import celery_app
from .services import git_service, TerraformExecutor, PackerExecutor, openstack_auth_service

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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

    def log(msg):
        """Log message and update Celery state"""
        timestamp = datetime.utcnow().isoformat()
        logger.info(msg)
        logs.append({"timestamp": timestamp, "message": msg})
        
        # Update Celery state with progress (stored in Result Backend = Redis)
        self.update_state(
            state='PROGRESS',
            meta={
                'deployment_id': deployment_id,
                'current': len(logs),
                'status': msg,
                'logs': logs
            }
        )

    try:
        log(f"Starting deployment {deployment_id} for release {release}")
        
        log("Setting up OpenStack credentials...")
        openstack_env = openstack_auth_service.get_environment_variables()
        if not openstack_env or not openstack_env.get('OS_AUTH_URL'):
            raise Exception("Failed to load OpenStack credentials")
        
        log(f"Cloning repository {app_git_link} (release: {release})")
        repo_path = git_service.clone_release(
            git_url=app_git_link,
            deployment_id=deployment_id,
            tag=release
        )
        
        # Packer
        packer_file = os.path.join(repo_path, "packer", "template.pkr.hcl")
        if os.path.exists(packer_file):
            log("Found Packer template, building image...")
            packer = PackerExecutor(os.path.join(repo_path, "packer"), env_vars=openstack_env)
            
            if not packer.init():
                raise Exception("Packer init failed")
            
            packer_vars = {}  # user_vars can be filtered for packer
            log(f"Packer variables: {packer_vars}")
            
            if not packer.validate("template.pkr.hcl", packer_vars):
                raise Exception("Packer template validation failed")
            
            if not packer.build("template.pkr.hcl", packer_vars):
                raise Exception("Packer build failed")
        
        # Terraform
        terraform_dir = os.path.join(repo_path, "terraform")
        if not os.path.exists(terraform_dir):
            raise Exception("No terraform directory found in repository")
        
        log("Running Terraform deployment...")
        terraform = TerraformExecutor(terraform_dir, env_vars=openstack_env)
        
        if not terraform.init():
            raise Exception("Terraform init failed")
        
        if not terraform.plan(variables=user_vars):
            raise Exception("Terraform plan failed")
        
        if not terraform.apply(variables=user_vars):
            raise Exception("Terraform apply failed")
        
        outputs = terraform.output()
        log(f"Terraform outputs: {outputs}")
        
        # Terraform State
        tfstate_path = os.path.join(terraform_dir, "terraform.tfstate")
        if os.path.exists(tfstate_path):
            with open(tfstate_path, "r") as f:
                tf_state = f.read()
        
        status = "success"
        log(f"Deployment {deployment_id} completed successfully")
        
        # Return result (stored in Celery Result Backend)
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
        log(f"Deployment {deployment_id} failed: {error_msg}")
        
        return {
            "status": status,
            "deployment_id": deployment_id,
            "logs": logs,
            "tf_state": tf_state,
            "commit_info": commit_info,
            "terraform_outputs": outputs,
            "error": error_msg
        }
        
    finally:
        if repo_path:
            git_service.cleanup_repository(repo_path)

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
        dict: status, logs
    """
    logs = []
    repo_path = None
    status = "unknown"
    
    # Get Redis publisher for task updates
    redis_pub = get_redis_publisher()
    task_id = self.request.id

    def log(msg):
        """Log message and publish to Redis"""
        timestamp = datetime.utcnow().isoformat()
        logger.info(msg)
        logs.append({"timestamp": timestamp, "message": msg})
        
        # Update Redis with new log
        redis_pub.append_log(
            task_id=task_id,
            deployment_id=deployment_id,
            log_message=msg
        )
        
        # Legacy: Update Celery state
        self.update_state(state='PROGRESS', meta={'log': msg, 'logs': logs})

    try:
        # Update task status to RUNNING
        redis_pub.update_task(
            task_id=task_id,
            deployment_id=deployment_id,
            status="running",
            started_at=datetime.utcnow().isoformat(),
            progress=0
        )
        
        log(f"Starting deletion of deployment {deployment_id} (release: {release})")
        redis_pub.update_task(task_id, deployment_id, progress=20)
        
        log("Setting up OpenStack credentials...")
        openstack_env = openstack_auth_service.get_environment_variables()
        if not openstack_env or not openstack_env.get('OS_AUTH_URL'):
            raise Exception("Failed to load OpenStack credentials")
        redis_pub.update_task(task_id, deployment_id, progress=40)
        
        log(f"Cloning repository {app_git_link} (release: {release})")
        repo_path = git_service.clone_repository(
            git_url=app_git_link,
            deployment_id=deployment_id,
            tag=release
        )
        redis_pub.update_task(task_id, deployment_id, progress=60)
        
        terraform_dir = os.path.join(repo_path, "terraform")
        if not os.path.exists(terraform_dir):
            raise Exception("No terraform directory found in repository")
        
        log("Running Terraform destroy...")
        terraform = TerraformExecutor(terraform_dir, env_vars=openstack_env)
        
        if not terraform.init():
            raise Exception("Terraform init failed")
        redis_pub.update_task(task_id, deployment_id, progress=80)
        
        if not terraform.destroy(variables=user_vars):
            raise Exception("Terraform destroy failed")
        redis_pub.update_task(task_id, deployment_id, progress=95)
        
        status = "success"
        log(f"Deployment {deployment_id} deleted successfully")
        
        # Final update to Redis
        redis_pub.update_task(
            task_id=task_id,
            deployment_id=deployment_id,
            status="success",
            progress=100,
            finished_at=datetime.utcnow().isoformat()
        )
        
        return {
            "status": status,
            "deployment_id": deployment_id,
            "logs": logs
        }
        
    except Exception as e:
        status = "failed"
        error_msg = str(e)
        log(f"Deployment deletion {deployment_id} failed: {error_msg}")
        
        # Update Redis with failure
        redis_pub.update_task(
            task_id=task_id,
            deployment_id=deployment_id,
            status="failed",
            finished_at=datetime.utcnow().isoformat(),
            error=error_msg
        )
        
        return {
            "status": status,
            "deployment_id": deployment_id,
            "logs": logs,
            "error": error_msg
        }
        
    finally:
        if repo_path:
            git_service.cleanup_repository(repo_path)

