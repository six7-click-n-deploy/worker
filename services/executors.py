"""
Terraform and Packer execution utilities
"""
import os
import subprocess
import logging
import json
from typing import Optional, Dict, Any
from config import settings

logger = logging.getLogger(__name__)

class TerraformExecutor:
    """Executor for Terraform operations"""
    
    def __init__(self, working_dir: str, env_vars: Optional[Dict[str, str]] = None):
        self.working_dir = working_dir
        self.terraform_path = settings.TERRAFORM_PATH
        self.env_vars = env_vars or {}
    
    def _get_env(self) -> Dict[str, str]:
        """Get environment variables including OpenStack credentials"""
        env = os.environ.copy()
        env.update(self.env_vars)
        return env
    
    def init(self) -> bool:
        """
        Initialize Terraform in the working directory
        
        Returns:
            bool: True if successful
        """
        try:
            logger.info(f"Running terraform init in {self.working_dir}")
            result = subprocess.run(
                [self.terraform_path, "init", "-input=false"],
                cwd=self.working_dir,
                capture_output=True,
                text=True,
                timeout=300,  # 5 minutes timeout
                env=self._get_env()
            )
            
            if result.returncode != 0:
                logger.error(f"Terraform init failed: {result.stderr}")
                return False
            
            logger.info("Terraform init successful")
            return True
            
        except subprocess.TimeoutExpired:
            logger.error("Terraform init timed out")
            return False
        except Exception as e:
            logger.error(f"Terraform init error: {e}")
            return False
    
    def plan(self, var_file: Optional[str] = None, variables: Optional[Dict[str, Any]] = None) -> bool:
        """
        Run terraform plan
        
        Args:
            var_file: Path to tfvars file
            variables: Dictionary of variables to pass
            
        Returns:
            bool: True if successful
        """
        try:
            cmd = [self.terraform_path, "plan", "-input=false"]
            
            if var_file:
                cmd.extend(["-var-file", var_file])
            
            if variables:
                for key, value in variables.items():
                    cmd.extend(["-var", f"{key}={value}"])
            
            logger.info(f"Running terraform plan in {self.working_dir}")
            result = subprocess.run(
                cmd,
                cwd=self.working_dir,
                capture_output=True,
                text=True,
                timeout=300,
                env=self._get_env()
            )
            
            if result.returncode != 0:
                logger.error(f"Terraform plan failed: {result.stderr}")
                return False
            
            logger.info("Terraform plan successful")
            return True
            
        except Exception as e:
            logger.error(f"Terraform plan error: {e}")
            return False
    
    def apply(self, var_file: Optional[str] = None, variables: Optional[Dict[str, Any]] = None) -> bool:
        """
        Run terraform apply
        
        Args:
            var_file: Path to tfvars file
            variables: Dictionary of variables to pass
            
        Returns:
            bool: True if successful
        """
        try:
            cmd = [self.terraform_path, "apply", "-auto-approve", "-input=false"]
            
            if var_file:
                cmd.extend(["-var-file", var_file])
            
            if variables:
                for key, value in variables.items():
                    cmd.extend(["-var", f"{key}={value}"])
            
            logger.info(f"Running terraform apply in {self.working_dir}")
            result = subprocess.run(
                cmd,
                cwd=self.working_dir,
                capture_output=True,
                text=True,
                timeout=1800,  # 30 minutes timeout
                env=self._get_env()
            )
            
            if result.returncode != 0:
                logger.error(f"Terraform apply failed: {result.stderr}")
                return False
            
            logger.info("Terraform apply successful")
            logger.info(f"Apply output: {result.stdout}")
            return True
            
        except subprocess.TimeoutExpired:
            logger.error("Terraform apply timed out")
            return False
        except Exception as e:
            logger.error(f"Terraform apply error: {e}")
            return False
    
    def destroy(self, var_file: Optional[str] = None, variables: Optional[Dict[str, Any]] = None) -> bool:
        """
        Run terraform destroy
        
        Args:
            var_file: Path to tfvars file
            variables: Dictionary of variables to pass
            
        Returns:
            bool: True if successful
        """
        try:
            cmd = [self.terraform_path, "destroy", "-auto-approve", "-input=false"]
            
            if var_file:
                cmd.extend(["-var-file", var_file])
            
            if variables:
                for key, value in variables.items():
                    cmd.extend(["-var", f"{key}={value}"])
            
            logger.info(f"Running terraform destroy in {self.working_dir}")
            result = subprocess.run(
                cmd,
                cwd=self.working_dir,
                capture_output=True,
                text=True,
                timeout=1800,
                env=self._get_env()
            )
            
            if result.returncode != 0:
                logger.error(f"Terraform destroy failed: {result.stderr}")
                return False
            
            logger.info("Terraform destroy successful")
            return True
            
        except subprocess.TimeoutExpired:
            logger.error("Terraform destroy timed out")
            return False
        except Exception as e:
            logger.error(f"Terraform destroy error: {e}")
            return False
    
    def output(self) -> Optional[Dict[str, Any]]:
        """
        Get terraform outputs as JSON
        
        Returns:
            dict: Terraform outputs or None if failed
        """
        try:
            logger.info(f"Getting terraform output from {self.working_dir}")
            result = subprocess.run(
                [self.terraform_path, "output", "-json"],
                cwd=self.working_dir,
                capture_output=True,
                text=True,
                timeout=60,
                env=self._get_env()
            )
            
            if result.returncode != 0:
                logger.error(f"Terraform output failed: {result.stderr}")
                return None
            
            return json.loads(result.stdout)
            
        except Exception as e:
            logger.error(f"Terraform output error: {e}")
            return None


class PackerExecutor:
    """Executor for Packer operations"""
    
    def __init__(self, working_dir: str, env_vars: Optional[Dict[str, str]] = None):
        self.working_dir = working_dir
        self.packer_path = settings.PACKER_PATH
        self.env_vars = env_vars or {}
    
    def _get_env(self) -> Dict[str, str]:
        """Get environment variables including OpenStack credentials"""
        env = os.environ.copy()
        env.update(self.env_vars)
        return env
    
    def init(self) -> bool:
        """
        Initialize Packer (install required plugins)
        
        Returns:
            bool: True if successful
        """
        try:
            logger.info(f"Running packer init in {self.working_dir}")
            result = subprocess.run(
                [self.packer_path, "init", "."],
                cwd=self.working_dir,
                capture_output=True,
                text=True,
                timeout=300,
                env=self._get_env()
            )
            
            if result.returncode != 0:
                logger.error(f"Packer init failed with return code {result.returncode}")
                logger.error(f"STDOUT: {result.stdout}")
                logger.error(f"STDERR: {result.stderr}")
                return False
            
            logger.info("Packer init successful")
            logger.info(f"Output: {result.stdout}")
            return True
            
        except Exception as e:
            logger.error(f"Packer init error: {e}")
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
        try:
            cmd = [self.packer_path, "validate"]
            
            if variables:
                for key, value in variables.items():
                    # Convert lists and dicts to JSON strings for Packer
                    if isinstance(value, (list, dict)):
                        value_str = json.dumps(value)
                    else:
                        value_str = str(value)
                    cmd.extend(["-var", f"{key}={value_str}"])
            
            cmd.append(template_file)
            
            logger.info(f"Validating packer template {template_file}")
            logger.info(f"Command: {' '.join(cmd)}")
            result = subprocess.run(
                cmd,
                cwd=self.working_dir,
                capture_output=True,
                text=True,
                timeout=60,
                env=self._get_env()
            )
            
            if result.returncode != 0:
                logger.error(f"Packer validate failed with return code {result.returncode}")
                logger.error(f"STDOUT: {result.stdout}")
                logger.error(f"STDERR: {result.stderr}")
                return False
            
            logger.info("Packer template is valid")
            logger.info(f"Validation output: {result.stdout}")
            return True
            
        except Exception as e:
            logger.error(f"Packer validate error: {e}")
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
        try:
            cmd = [self.packer_path, "build"]
            
            # Add -force flag to overwrite existing images
            if force:
                cmd.append("-force")
            
            if variables:
                for key, value in variables.items():
                    # Convert lists and dicts to JSON strings for Packer
                    if isinstance(value, (list, dict)):
                        value_str = json.dumps(value)
                    else:
                        value_str = str(value)
                    cmd.extend(["-var", f"{key}={value_str}"])
            
            cmd.append(template_file)
            
            # Merge environment variables
            build_env = self._get_env()
            if extra_env:
                build_env.update(extra_env)
            
            # Enable Packer debug logging
            build_env['PACKER_LOG'] = '1'
            
            logger.info(f"Building packer image from {template_file}")
            logger.info(f"This may take 15-30 minutes...")
            logger.info(f"Command: {' '.join(cmd)}")
            
            # Stream output in real-time for long-running builds
            process = subprocess.Popen(
                cmd,
                cwd=self.working_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=build_env
            )
            
            # Stream and log output line by line
            output_lines = []
            for line in process.stdout:
                line = line.rstrip()
                if line:
                    logger.info(f"Packer: {line}")
                    output_lines.append(line)
            
            process.wait(timeout=3600)  # 1 hour timeout
            
            if process.returncode != 0:
                logger.error(f"Packer build failed with return code {process.returncode}")
                logger.error(f"Output: {chr(10).join(output_lines[-50:])}")  # Last 50 lines
                return False
            
            logger.info("Packer build successful")
            return True
            
        except subprocess.TimeoutExpired:
            logger.error("Packer build timed out")
            return False
        except Exception as e:
            logger.error(f"Packer build error: {e}")
            return False
