"""
OpenStack authentication service for setting up credentials
"""
import os
import yaml
import logging
from typing import Dict, Any, Optional
from config import settings

logger = logging.getLogger(__name__)

class OpenStackAuthService:
    """Service for managing OpenStack authentication"""
    
    def __init__(self):
        self.clouds_yaml_path = settings.OPENSTACK_CLOUDS_YAML
    
    def load_clouds_yaml(self) -> Optional[Dict[str, Any]]:
        """
        Load OpenStack clouds.yaml configuration
        
        Returns:
            dict: Clouds configuration or None if not found
        """
        try:
            if os.path.exists(self.clouds_yaml_path):
                with open(self.clouds_yaml_path, 'r') as f:
                    clouds_config = yaml.safe_load(f)
                    logger.info(f"Loaded clouds.yaml from {self.clouds_yaml_path}")
                    return clouds_config
            else:
                logger.warning(f"clouds.yaml not found at {self.clouds_yaml_path}")
                return None
        except Exception as e:
            logger.error(f"Failed to load clouds.yaml: {e}")
            return None
    
    def get_environment_variables(self, cloud_name: str = "openstack") -> Dict[str, str]:
        """
        Get OpenStack environment variables from clouds.yaml
        
        Args:
            cloud_name: Cloud name from clouds.yaml (default: "openstack")
            
        Returns:
            dict: Environment variables for OpenStack
        """
        env_vars = {}
        
        # Load from clouds.yaml
        clouds = self.load_clouds_yaml()
        if clouds and 'clouds' in clouds and cloud_name in clouds['clouds']:
            cloud_config = clouds['clouds'][cloud_name]
            auth = cloud_config.get('auth', {})
            
            env_vars = {
                'OS_AUTH_URL': auth.get('auth_url', ''),
                'OS_PROJECT_NAME': auth.get('project_name', ''),
                'OS_PROJECT_ID': auth.get('project_id', ''),
                'OS_USERNAME': auth.get('username', ''),
                'OS_PASSWORD': auth.get('password', ''),
                'OS_USER_DOMAIN_NAME': auth.get('user_domain_name', 'Default'),
                'OS_REGION_NAME': cloud_config.get('region_name', 'RegionOne'),
                'OS_INTERFACE': cloud_config.get('interface', 'public'),
                'OS_IDENTITY_API_VERSION': str(cloud_config.get('identity_api_version', 3)),
            }
            logger.info(f"Using OpenStack credentials from clouds.yaml cloud '{cloud_name}'")
            return env_vars
        
        logger.error(f"Cloud '{cloud_name}' not found in clouds.yaml")
        return {}
    
    def setup_openstack_environment(self, cloud_name: str = "openstack") -> bool:
        """
        Setup OpenStack environment variables for current process
        
        Args:
            cloud_name: Cloud name from clouds.yaml (default: "openstack")
            
        Returns:
            bool: True if credentials were set successfully
        """
        env_vars = self.get_environment_variables(cloud_name)
        
        if not env_vars or not env_vars.get('OS_AUTH_URL'):
            logger.error("Failed to setup OpenStack environment - no valid credentials")
            return False
        
        # Set environment variables for current process
        for key, value in env_vars.items():
            if value:
                os.environ[key] = value
        
        logger.info("OpenStack environment variables configured successfully")
        return True

# Singleton instance
openstack_auth_service = OpenStackAuthService()
