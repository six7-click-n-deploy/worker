"""
OpenStack authentication service for setting up credentials
"""
import os
import yaml
import logging
from typing import Dict, Any, Optional
from ..config import settings

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
            cloud_name: Name of the cloud to use from clouds.yaml

        Returns:
            dict: Environment variables for OpenStack authentication
        """
        clouds_config = self.load_clouds_yaml()
        if not clouds_config or cloud_name not in clouds_config.get('clouds', {}):
            logger.warning(f"Cloud '{cloud_name}' not found in clouds.yaml")
            return {}

        cloud_config = clouds_config['clouds'][cloud_name]
        auth_config = cloud_config.get('auth', {})

        # Map clouds.yaml auth fields to OpenStack environment variables
        env_vars = {}

        # Standard OpenStack environment variables
        env_vars['OS_AUTH_URL'] = auth_config.get('auth_url', '')
        env_vars['OS_PROJECT_ID'] = auth_config.get('project_id', '')
        env_vars['OS_PROJECT_NAME'] = auth_config.get('project_name', '')
        env_vars['OS_USER_DOMAIN_NAME'] = auth_config.get('user_domain_name', 'Default')
        env_vars['OS_USERNAME'] = auth_config.get('username', '')
        env_vars['OS_PASSWORD'] = auth_config.get('password', '')
        env_vars['OS_REGION_NAME'] = auth_config.get('region_name', '')

        # Additional cloud-specific settings
        if 'region_name' in cloud_config:
            env_vars['OS_REGION_NAME'] = cloud_config['region_name']

        # Filter out empty values
        env_vars = {k: v for k, v in env_vars.items() if v}

        logger.info(f"Loaded OpenStack environment variables for cloud '{cloud_name}'")
        return env_vars


# Singleton instance
openstack_auth_service = OpenStackAuthService()
