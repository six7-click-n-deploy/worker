from pydantic_settings import BaseSettings
import os

class Settings(BaseSettings):
    # Database
    DATABASE_URL: str
    
    # Celery
    CELERY_BROKER_URL: str = "redis://redis:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://redis:6379/1"
    
    # Worker settings
    TEMP_REPO_BASE_PATH: str = "/tmp/worker_repos"
    
    # Terraform/Packer paths (installed in container)
    TERRAFORM_PATH: str = "/usr/local/bin/terraform"
    PACKER_PATH: str = "/usr/local/bin/packer"
    
    # OpenStack configuration
    OPENSTACK_CLOUDS_YAML: str = "/app/clouds.yaml"
    OPENSTACK_CLOUD_NAME: str = "openstack"  # Cloud name to use from clouds.yaml
    
    # OpenStack defaults for Packer builds (DHBW-specific)
    PACKER_BUILD_FLAVOR: str = "gp1.small"  # Flavor for temporary build instances
    PACKER_FLOATING_IP_POOL: str = "NAT"  # External network for builds
    PACKER_BUILD_NETWORK: str = "DHBW"  # Internal network for builds
    
    class Config:
        env_file = ".env"
        case_sensitive = True

settings = Settings()
