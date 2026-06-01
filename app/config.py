from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Celery
    CELERY_BROKER_URL: str = "amqp://admin:admin@rabbitmq:5672/"
    CELERY_RESULT_BACKEND: str = "redis://redis:6379/0"

    # Worker settings
    TEMP_REPO_BASE_PATH: str = "/tmp/worker_repos"

    # Terraform/Packer paths (installed in container)
    TERRAFORM_PATH: str = "/usr/local/bin/terraform"
    PACKER_PATH: str = "/usr/local/bin/packer"

    # Symmetric Fernet key shared with the backend. The worker never reaches
    # the database; it receives ciphertext envelopes via Celery and decrypts
    # them in-process with this key.
    CREDENTIAL_ENCRYPTION_KEY: str

    # Terraform remote state — Postgres connection string for the worker-only
    # `postgres-tfstate` container. Empty string means "no remote backend"
    # (legacy local-state behaviour, only useful for unit tests). In production
    # this is always set; an empty value at task time will raise.
    TFSTATE_DATABASE_URL: str = ""

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
        extra = "ignore"


settings = Settings()
