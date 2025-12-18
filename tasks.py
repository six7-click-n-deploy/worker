"""
Celery tasks for deployment operations
"""
import os
import json
import logging
from uuid import UUID
from typing import Optional, Dict, Any
from celery import Task
from celery_app import celery_app
from database import SessionLocal
from models import Deployment, DeploymentStatus, App
from services.git_service import git_service
from services.executors import TerraformExecutor, PackerExecutor
from services.openstack_auth import openstack_auth_service

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class DatabaseTask(Task):
    """Base task with database session management"""
    _db = None
    
    @property
    def db(self):
        if self._db is None:
            self._db = SessionLocal()
        return self._db
    
    def after_return(self, *args, **kwargs):
        if self._db is not None:
            self._db.close()
            self._db = None


def update_deployment_status(db, deployment_id: UUID, status: DeploymentStatus, commit_info: Optional[str] = None):
    """Update deployment status in database"""
    try:
        deployment = db.query(Deployment).filter(Deployment.deploymentId == deployment_id).first()
        if deployment:
            deployment.status = status
            if commit_info:
                deployment.commitInfo = commit_info
            db.commit()
            logger.info(f"Updated deployment {deployment_id} to status {status}")
    except Exception as e:
        logger.error(f"Failed to update deployment status: {e}")
        db.rollback()


def parse_user_variables(user_input_var: Optional[str]) -> Dict[str, Any]:
    """Parse user input variables from JSON string"""
    if not user_input_var:
        return {}
    try:
        return json.loads(user_input_var)
    except json.JSONDecodeError:
        logger.warning(f"Failed to parse user variables: {user_input_var}")
        return {}


@celery_app.task(base=DatabaseTask, bind=True, name="tasks.deploy_application")
def deploy_application(self, deployment_id: str, app_id: str):
    """
    Deploy an application using Terraform and Packer
    
    Args:
        deployment_id: UUID of the deployment
        app_id: UUID of the app to deploy
    """
    db = self.db
    repo_path = None
    
    try:
        logger.info(f"Starting deployment {deployment_id} for app {app_id}")
        
        # Get deployment and app from database
        deployment = db.query(Deployment).filter(Deployment.deploymentId == UUID(deployment_id)).first()
        app = db.query(App).filter(App.appId == UUID(app_id)).first()
        
        if not deployment or not app:
            raise Exception(f"Deployment or App not found in database")
        
        if not app.git_link:
            raise Exception(f"App {app_id} has no git repository configured")
        
        # Update status to RUNNING
        update_deployment_status(db, UUID(deployment_id), DeploymentStatus.RUNNING)
        
        # Setup OpenStack environment
        logger.info("Setting up OpenStack credentials...")
        openstack_env = openstack_auth_service.get_environment_variables()
        if not openstack_env or not openstack_env.get('OS_AUTH_URL'):
            raise Exception("Failed to load OpenStack credentials")
        
        # Clone repository
        logger.info(f"Cloning repository {app.git_link}")
        repo_path = git_service.clone_repository(
            git_url=app.git_link,
            deployment_id=deployment_id,
            commit_hash=deployment.commitHash
        )
        
        # Get commit info
        commit_info = git_service.get_latest_commit_info(repo_path)
        commit_info_str = json.dumps(commit_info)
        
        # Parse user variables
        user_vars = parse_user_variables(deployment.userInputVar)
        
        # Check for Packer template and build if exists
        packer_file = os.path.join(repo_path, "packer", "template.pkr.hcl")
        if os.path.exists(packer_file):
            logger.info("Found Packer template, building image...")
            packer = PackerExecutor(os.path.join(repo_path, "packer"), env_vars=openstack_env)
            
            if not packer.init():
                raise Exception("Packer init failed")
            
            # Only pass variables that Packer template defines
            # Filter out Terraform-specific variables
            packer_vars = {}
            
            # Map network_name to networks list for Packer
            # if 'network_name' in user_vars:
            #     packer_vars['networks'] = [user_vars['network_name']]
            
            # # Add other known Packer variables
            # if 'flavor' in user_vars:
            #     packer_vars['flavor'] = user_vars['flavor']
            # if 'floating_ip_pool' in user_vars:
            #     packer_vars['floating_ip_pool'] = user_vars['floating_ip_pool']
            # if 'image_name' in user_vars:
            #     packer_vars['image_name'] = user_vars['image_name']
            # if 'source_image' in user_vars:
            #     packer_vars['source_image'] = user_vars['source_image']
            
            # # Set defaults only if not provided
            # if 'flavor' not in packer_vars:
            #     packer_vars['flavor'] = 'gp1.small'
            # if 'floating_ip_pool' not in packer_vars:
            #     packer_vars['floating_ip_pool'] = 'NAT'
            
            logger.info(f"Packer variables: {packer_vars}")
            
            if not packer.validate("template.pkr.hcl", packer_vars):
                raise Exception("Packer template validation failed")
            
            if not packer.build("template.pkr.hcl", packer_vars):
                raise Exception("Packer build failed")
        
        # Run Terraform
        terraform_dir = os.path.join(repo_path, "terraform")
        if not os.path.exists(terraform_dir):
            raise Exception("No terraform directory found in repository")
        
        logger.info("Running Terraform deployment...")
        terraform = TerraformExecutor(terraform_dir, env_vars=openstack_env)
        
        # Initialize Terraform
        if not terraform.init():
            raise Exception("Terraform init failed")
        
        # Plan
        if not terraform.plan(variables=user_vars):
            raise Exception("Terraform plan failed")
        
        # Apply
        if not terraform.apply(variables=user_vars):
            raise Exception("Terraform apply failed")
        
        # Get outputs
        outputs = terraform.output()
        logger.info(f"Terraform outputs: {outputs}")
        
        # Update deployment status to SUCCESS
        update_deployment_status(db, UUID(deployment_id), DeploymentStatus.SUCCESS, commit_info_str)
        
        logger.info(f"Deployment {deployment_id} completed successfully")
        return {
            "status": "success",
            "deployment_id": deployment_id,
            "commit_info": commit_info,
            "terraform_outputs": outputs
        }
        
    except Exception as e:
        logger.error(f"Deployment {deployment_id} failed: {e}")
        update_deployment_status(db, UUID(deployment_id), DeploymentStatus.FAILED)
        raise
    
    finally:
        # Always cleanup the cloned repository
        if repo_path:
            git_service.cleanup_repository(repo_path)


@celery_app.task(base=DatabaseTask, bind=True, name="tasks.delete_deployment")
def delete_deployment(self, deployment_id: str, app_id: str):
    """
    Delete/destroy a deployment using Terraform
    
    Args:
        deployment_id: UUID of the deployment
        app_id: UUID of the app
    """
    db = self.db
    repo_path = None
    
    try:
        logger.info(f"Starting deletion of deployment {deployment_id}")
        
        # Get deployment and app from database
        deployment = db.query(Deployment).filter(Deployment.deploymentId == UUID(deployment_id)).first()
        app = db.query(App).filter(App.appId == UUID(app_id)).first()
        
        if not deployment or not app:
            raise Exception(f"Deployment or App not found in database")
        
        if not app.git_link:
            raise Exception(f"App {app_id} has no git repository configured")
        
        # Update status to RUNNING
        update_deployment_status(db, UUID(deployment_id), DeploymentStatus.RUNNING)
        
        # Setup OpenStack environment
        logger.info("Setting up OpenStack credentials...")
        openstack_env = openstack_auth_service.get_environment_variables()
        if not openstack_env or not openstack_env.get('OS_AUTH_URL'):
            raise Exception("Failed to load OpenStack credentials")
        
        # Clone repository (same commit as original deployment)
        logger.info(f"Cloning repository {app.git_link}")
        repo_path = git_service.clone_repository(
            git_url=app.git_link,
            deployment_id=deployment_id,
            commit_hash=deployment.commitHash
        )
        
        # Parse user variables
        user_vars = parse_user_variables(deployment.userInputVar)
        
        # Run Terraform destroy
        terraform_dir = os.path.join(repo_path, "terraform")
        if not os.path.exists(terraform_dir):
            raise Exception("No terraform directory found in repository")
        
        logger.info("Running Terraform destroy...")
        terraform = TerraformExecutor(terraform_dir, env_vars=openstack_env)
        
        # Initialize Terraform
        if not terraform.init():
            raise Exception("Terraform init failed")
        
        # Destroy
        if not terraform.destroy(variables=user_vars):
            raise Exception("Terraform destroy failed")
        
        # Update deployment status to SUCCESS (deletion successful)
        update_deployment_status(db, UUID(deployment_id), DeploymentStatus.SUCCESS)
        
        logger.info(f"Deployment {deployment_id} deleted successfully")
        return {
            "status": "success",
            "deployment_id": deployment_id,
            "action": "deleted"
        }
        
    except Exception as e:
        logger.error(f"Deployment deletion {deployment_id} failed: {e}")
        update_deployment_status(db, UUID(deployment_id), DeploymentStatus.FAILED)
        raise
    
    finally:
        # Always cleanup the cloned repository
        if repo_path:
            git_service.cleanup_repository(repo_path)


@celery_app.task(base=DatabaseTask, bind=True, name="tasks.upgrade_deployment")
def upgrade_deployment(self, deployment_id: str, app_id: str, new_commit_hash: Optional[str] = None):
    """
    Upgrade an existing deployment to a new version
    
    Args:
        deployment_id: UUID of the deployment
        app_id: UUID of the app
        new_commit_hash: Optional specific commit to upgrade to (defaults to latest)
    """
    db = self.db
    repo_path = None
    
    try:
        logger.info(f"Starting upgrade of deployment {deployment_id}")
        
        # Get deployment and app from database
        deployment = db.query(Deployment).filter(Deployment.deploymentId == UUID(deployment_id)).first()
        app = db.query(App).filter(App.appId == UUID(app_id)).first()
        
        if not deployment or not app:
            raise Exception(f"Deployment or App not found in database")
        
        if not app.git_link:
            raise Exception(f"App {app_id} has no git repository configured")
        
        # Update status to RUNNING
        update_deployment_status(db, UUID(deployment_id), DeploymentStatus.RUNNING)
        
        # Setup OpenStack environment
        logger.info("Setting up OpenStack credentials...")
        openstack_env = openstack_auth_service.get_environment_variables()
        if not openstack_env or not openstack_env.get('OS_AUTH_URL'):
            raise Exception("Failed to load OpenStack credentials")
        
        # Clone repository (latest or specific commit)
        logger.info(f"Cloning repository {app.git_link}")
        repo_path = git_service.clone_repository(
            git_url=app.git_link,
            deployment_id=deployment_id,
            commit_hash=new_commit_hash
        )
        
        # Get commit info
        commit_info = git_service.get_latest_commit_info(repo_path)
        commit_info_str = json.dumps(commit_info)
        
        # Update deployment with new commit hash
        deployment.commitHash = commit_info.get("hash")
        db.commit()
        
        # Parse user variables
        user_vars = parse_user_variables(deployment.userInputVar)
        
        # Check for Packer template and rebuild if exists
        packer_file = os.path.join(repo_path, "packer", "template.pkr.hcl")
        if os.path.exists(packer_file):
            logger.info("Found Packer template, rebuilding image...")
            packer = PackerExecutor(os.path.join(repo_path, "packer"), env_vars=openstack_env)
            
            if not packer.validate("template.pkr.hcl"):
                raise Exception("Packer template validation failed")
            
            if not packer.build("template.pkr.hcl", variables=user_vars):
                raise Exception("Packer build failed")
        
        # Run Terraform apply (this will update existing resources)
        terraform_dir = os.path.join(repo_path, "terraform")
        if not os.path.exists(terraform_dir):
            raise Exception("No terraform directory found in repository")
        
        logger.info("Running Terraform upgrade...")
        terraform = TerraformExecutor(terraform_dir, env_vars=openstack_env)
        
        # Initialize Terraform
        if not terraform.init():
            raise Exception("Terraform init failed")
        
        # Plan
        if not terraform.plan(variables=user_vars):
            raise Exception("Terraform plan failed")
        
        # Apply
        if not terraform.apply(variables=user_vars):
            raise Exception("Terraform apply failed")
        
        # Get outputs
        outputs = terraform.output()
        logger.info(f"Terraform outputs: {outputs}")
        
        # Update deployment status to SUCCESS
        update_deployment_status(db, UUID(deployment_id), DeploymentStatus.SUCCESS, commit_info_str)
        
        logger.info(f"Deployment {deployment_id} upgraded successfully")
        return {
            "status": "success",
            "deployment_id": deployment_id,
            "action": "upgraded",
            "commit_info": commit_info,
            "terraform_outputs": outputs
        }
        
    except Exception as e:
        logger.error(f"Deployment upgrade {deployment_id} failed: {e}")
        update_deployment_status(db, UUID(deployment_id), DeploymentStatus.FAILED)
        raise
    
    finally:
        # Always cleanup the cloned repository
        if repo_path:
            git_service.cleanup_repository(repo_path)
