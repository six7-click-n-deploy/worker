"""
OpenStack service for image management
"""

import json
import logging
import os
import subprocess

logger = logging.getLogger(__name__)


class OpenStackService:
    """Service for OpenStack image operations"""

    def __init__(self, env_vars: dict[str, str] | None = None):
        """
        Initialize OpenStack service

        Args:
            env_vars: OpenStack environment variables (OS_AUTH_URL, OS_USERNAME, etc.)
        """
        self.env_vars = env_vars or {}

    def check_image_exists(self, image_name: str) -> tuple[bool, str | None]:
        """
        Check if an image with the given name already exists in OpenStack

        Args:
            image_name: Name of the image to check

        Returns:
            tuple: (exists: bool, image_id: str | None)
        """
        if not self.env_vars.get("OS_AUTH_URL"):
            logger.warning("No OpenStack credentials available for image check")
            return (False, None)

        try:
            # Prepare environment for openstack CLI
            env = os.environ.copy()
            env.update(self.env_vars)

            # Use openstack CLI to list images with the given name
            cmd = ["openstack", "image", "list", "--name", image_name, "-f", "json"]

            logger.info(f"Checking if image '{image_name}' exists in OpenStack")

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, env=env)

            if result.returncode != 0:
                logger.error(f"Failed to check image existence: {result.stderr}")
                return (False, None)

            images = json.loads(result.stdout)

            if images and len(images) > 0:
                # Image exists, return the first match
                image_id = images[0].get("ID")
                logger.info(f"Image '{image_name}' already exists with ID: {image_id}")
                return (True, image_id)
            else:
                logger.info(f"Image '{image_name}' does not exist")
                return (False, None)

        except subprocess.TimeoutExpired:
            logger.error("Timeout while checking image existence")
            return (False, None)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse OpenStack CLI output: {e}")
            return (False, None)
        except FileNotFoundError:
            logger.error("OpenStack CLI not found. Please install python-openstackclient")
            return (False, None)
        except Exception as e:
            logger.error(f"Error checking image existence: {e}")
            return (False, None)
