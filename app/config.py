from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str

    # Celery
    CELERY_BROKER_URL: str = "amqp://admin:admin@rabbitmq:5672/"
    CELERY_RESULT_BACKEND: str = "redis://redis:6379/0"

    # Worker settings
    TEMP_REPO_BASE_PATH: str = "/tmp/worker_repos"

    # Terraform/Packer paths (installed in container)
    TERRAFORM_PATH: str = "/usr/local/bin/terraform"
    PACKER_PATH: str = "/usr/local/bin/packer"

    # OpenStack configuration
    OPENSTACK_CLOUDS_YAML: str = "/app/clouds.yaml"

    # Git
    GIT_ACCESS_TOKEN: str = ""

    # Email (Gmail SMTP)
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""  # Gmail address
    SMTP_PASSWORD: str = ""  # Gmail App Password
    SMTP_FROM_EMAIL: str = ""  # Sender email
    SMTP_FROM_NAME: str = "AppStore Deployment"

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()
