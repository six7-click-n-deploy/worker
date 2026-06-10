# Worker

[![Coverage](https://img.shields.io/endpoint?url=https://six7-click-n-deploy.github.io/worker/badge.json)](https://six7-click-n-deploy.github.io/worker/)

Python worker service that processes deployment tasks using Celery for OpenStack infrastructure.

## Features

- **Deploy Applications**: Clones Git repositories, runs Packer builds, and applies Terraform configurations
- **Delete Deployments**: Destroys infrastructure using Terraform
- **Upgrade Deployments**: Updates existing deployments to new versions
- **OpenStack Integration**: Full support for OpenStack authentication via YAML config
- **Automatic Cleanup**: Clones repositories fresh for each task and cleans up afterwards
- **Database Integration**: Updates deployment status in PostgreSQL database

## Development

### Prerequisites

- Python 3.10 or 3.11
- Poetry (dependency management)
- Docker (optional, for container testing)

### Setup

```bash
# Install dependencies
poetry install --with dev

# Or using make
make install
```

### Code Quality Tools

#### Linting & Formatting

```bash
# Run all linters
make lint

# Auto-format code
make format

# Individual tools
poetry run ruff check .              # Fast linting
poetry run black .                   # Code formatting
poetry run isort .                   # Import sorting
poetry run mypy app/                 # Type checking
```

#### Testing

```bash
# Run all tests
make test

# Run only unit tests
make test-unit

# Run with coverage
make test-cov

# Run in watch mode (continuous)
make test-watch
```

#### Pre-commit Hooks

```bash
# Install pre-commit hooks
pip install pre-commit
pre-commit install

# Run manually
pre-commit run --all-files
```

### CI/CD Pipeline

The worker uses GitHub Actions for automated testing and Docker image building:

**Pipeline Stages:**

1. **Lint & Format Check** (parallel)
   - Ruff linting
   - Black formatting check
   - isort import sorting check
   - MyPy type checking

2. **Tests** (runs after lint passes)
   - Unit tests with pytest
   - Coverage reporting to Codecov
   - Tests on Python 3.10 and 3.11

3. **Build Docker Image** (runs after tests pass)
   - Builds Docker image
   - Saves as artifact
   - **Does NOT push on PR**

4. **Push Docker Image** (only on merge to main)
   - Loads built image
   - Pushes to GitHub Container Registry
   - Tags: `latest`, `main`, `sha-<commit>`

**Workflow File:** `.github/workflows/worker-ci.yml`

### Available Make Commands

```bash
make help              # Show all available commands
make install           # Install dependencies
make lint              # Run all linters
make format            # Auto-format code
make test              # Run all tests
make test-unit         # Run only unit tests
make test-integration  # Run only integration tests
make test-cov          # Run tests with coverage
make clean             # Clean up generated files
make check             # Run linters + tests (CI simulation)
make docker-build      # Build Docker image
```

## Architecture

### Components

- **Celery Tasks** (`tasks.py`): Three main tasks for deployment operations
  - `deploy_application`: Full deployment with Packer + Terraform
  - `delete_deployment`: Infrastructure teardown
  - `upgrade_deployment`: Update to new version

- **Git Service** (`services/git_service.py`): Handles repository cloning and cleanup
- **Executors** (`services/executors.py`): Wrappers for Terraform and Packer CLI tools with OpenStack env injection
- **OpenStack Auth** (`services/openstack_auth.py`): `PerTaskCloudsConfig` context manager. Decrypts the per-user credential envelope received from the backend via Celery (using the shared Fernet key) and materialises a `clouds.yaml` (mode 0600) inside the per-task workspace. The file is shredded on exit.
- **Build Lock** (`services/build_lock.py`): Redis-backed distributed lock keyed on `(project_id, image_name)` so two parallel workers never run the same Packer build twice.

### Task Flow

1. **Receive Task**: Celery worker receives the deployment task plus an `openstack_envelope` (ciphertext + non-secret metadata)
2. **Clone Repository**: Fresh clone of Git repository under `/tmp/worker_repos/deploy_<id>/`
3. **Materialise credentials**: `PerTaskCloudsConfig` decrypts the envelope in-process and writes `clouds.yaml` into the workspace
4. **Build Image** (optional): Acquire `PackerBuildLock`, re-check Glance, run Packer if the image is missing
5. **Deploy Infrastructure**: Terraform init/plan/apply with `OS_CLIENT_CONFIG_FILE` pointing at the per-task `clouds.yaml`
6. **Cleanup**: Remove the rendered `clouds.yaml` and the cloned repository

## Configuration

### Environment Variables (`.env`)

```bash
CELERY_BROKER_URL=amqp://admin:admin@rabbitmq:5672/
CELERY_RESULT_BACKEND=redis://redis:6379/0
TEMP_REPO_BASE_PATH=/tmp/worker_repos

# Symmetric Fernet key shared with the backend. Required.
# Generate with:
#   python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
CREDENTIAL_ENCRYPTION_KEY=<32-byte-url-safe-base64-key>
```

### OpenStack Authentication

The worker no longer reads a static `clouds.yaml` from disk or env. Each
deployment task carries its own per-user credential envelope: the backend
encrypts the user's `identifier` / `secret` with the shared Fernet key and
ships the ciphertext (plus non-secret metadata) as the last positional
argument of the Celery task. `PerTaskCloudsConfig`:

1. decrypts the envelope in-process,
2. writes a `clouds.yaml` with mode `0600` into the per-task workspace,
3. exports `OS_CLIENT_CONFIG_FILE` and `OS_CLOUD` to Packer / Terraform,
4. removes the file in `__exit__`.

Plaintext never lands on disk outside the per-task workspace, never reaches
RabbitMQ, and never appears in Celery's result backend.

## Development

### Local Setup

```bash
# Install dependencies
pip install -r requirements.txt

# Set the shared encryption key (must match the backend's value)
export CREDENTIAL_ENCRYPTION_KEY="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"

# Run worker
celery -A celery_app worker --loglevel=info
```

### Docker Setup

```bash
# Build image
docker build -f Dockerfile.dev -t worker:dev .

# Run container — credentials arrive per-task via Celery, no volume mounts needed
docker run --env-file .env worker:dev
```

## Repository Structure

Git repositories must follow this structure:

```
app-repo/
├── packer/
│   └── template.pkr.hcl  # Optional: Packer template for OpenStack images
└── terraform/
    ├── main.tf           # Required: Terraform configuration
    ├── variables.tf      # Optional: Variable definitions
    └── outputs.tf        # Optional: Output values
```

### Example Terraform for OpenStack

```hcl
terraform {
  required_providers {
    openstack = {
      source  = "terraform-provider-openstack/openstack"
      version = "~> 1.53"
    }
  }
}

provider "openstack" {
  # Credentials from environment variables (OS_AUTH_URL, etc.)
}

resource "openstack_compute_instance_v2" "my_instance" {
  name            = var.instance_name
  image_name      = var.image_name
  flavor_name     = var.flavor_name
  key_pair        = var.key_pair
  security_groups = ["default"]
}
```

### Example Packer for OpenStack

```hcl
packer {
  required_plugins {
    openstack = {
      version = ">= 1.0.0"
      source  = "github.com/hashicorp/openstack"
    }
  }
}

source "openstack" "image" {
  # Credentials from environment variables
  image_name   = var.image_name
  source_image = var.source_image_id
  flavor       = var.flavor
  ssh_username = "ubuntu"
}

build {
  sources = ["source.openstack.image"]

  provisioner "shell" {
    inline = [
      "sudo apt-get update",
      "sudo apt-get install -y nginx"
    ]
  }
}
```

## User Variables

User input variables from the `userInputVar` field are passed to both Packer and Terraform:

```json
{
  "instance_name": "my-app-server",
  "flavor_name": "m1.small",
  "image_name": "Ubuntu-22.04"
}
```

## Error Handling

- All errors are logged and deployment status is set to `FAILED`
- Repositories are always cleaned up, even on failure
- Task timeouts: 1 hour per task, 30 minutes for Terraform apply
- Failed tasks can be retried manually
- OpenStack credentials are validated before deployment

## Security

- ⚠️ **Never commit** the `CREDENTIAL_ENCRYPTION_KEY` value
- The key in the worker env must exactly match the backend's `CREDENTIAL_ENCRYPTION_KEY`
- Per-task `clouds.yaml` files are written with mode `0600` and removed on exit
- Prefer Application Credentials (`v3applicationcredential`) over passwords; users select per-credential auth type when uploading

## Monitoring

- Check Celery logs for task execution details
- Monitor deployment status in database
- Use Flower for Celery task monitoring (optional)
- OpenStack credentials are logged (masked passwords)

## License

See LICENSE file.
