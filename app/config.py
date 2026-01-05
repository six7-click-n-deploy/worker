from pydantic_settings import BaseSettings
import os

class Settings(BaseSettings):
    # Database
    DATABASE_URL: str
    
    # Celery
    CELERY_BROKER_URL: str = "amqp://admin:admin@rabbitmq:5672/"
    
    # Worker settings
    TEMP_REPO_BASE_PATH: str = "/tmp/worker_repos"
    
    # Terraform/Packer paths (installed in container)
    TERRAFORM_PATH: str = "/usr/local/bin/terraform"
    PACKER_PATH: str = "/usr/local/bin/packer"
    
    # OpenStack configuration
    OPENSTACK_CLOUDS_YAML: str = "/app/clouds.yaml"
    
    class Config:
        env_file = ".env"
        case_sensitive = True

settings = Settings()
