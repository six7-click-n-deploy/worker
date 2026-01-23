# Worker

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
- **OpenStack Auth** (`services/openstack_auth.py`): Manages OpenStack credentials from YAML files
- **Database** (`database.py`, `models.py`): Database connection and deployment state management

### Task Flow

1. **Receive Task**: Celery worker receives deployment task from Redis queue
2. **Update Status**: Set deployment status to `RUNNING` in database
3. **Setup OpenStack**: Load credentials from `openstack_auth.yaml` or `clouds.yaml`
4. **Clone Repository**: Fresh clone of Git repository to temporary directory
5. **Build Image** (optional): Run Packer if `packer/template.pkr.hcl` exists
6. **Deploy Infrastructure**: Run Terraform init/plan/apply with OpenStack credentials
7. **Update Status**: Set status to `SUCCESS` or `FAILED` with commit info
8. **Cleanup**: Delete cloned repository

## Configuration

### Environment Variables (`.env`)

```bash
DATABASE_URL=postgresql://postgres:postgres@db:5432/clickndeploy
CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/1
TEMP_REPO_BASE_PATH=/tmp/worker_repos

# OpenStack Configuration Files
OPENSTACK_AUTH_YAML=/app/openstack_auth.yaml
OPENSTACK_CLOUDS_YAML=/app/clouds.yaml

# Or use environment variables directly
OS_AUTH_URL=https://openstack.example.com:5000/v3
OS_PROJECT_NAME=my-project
OS_USERNAME=my-user
OS_PASSWORD=my-password
OS_USER_DOMAIN_NAME=Default
OS_PROJECT_DOMAIN_NAME=Default
OS_REGION_NAME=RegionOne
```

### OpenStack Authentication

The worker supports three methods for OpenStack authentication (in priority order):

#### 1. clouds.yaml (Recommended for multi-environment)

```yaml
clouds:
  production:
    auth:
      auth_url: https://openstack.example.com:5000/v3
      username: prod-user
      password: prod-password
      project_name: production
      user_domain_name: Default
      project_domain_name: Default
    region_name: RegionOne
```

Mount to `/app/clouds.yaml` in container.

#### 2. openstack_auth.yaml (Simple single environment)

```yaml
auth_url: https://openstack.example.com:5000/v3
project_name: my-project
username: my-username
password: my-password
user_domain_name: Default
project_domain_name: Default
region_name: RegionOne
```

Mount to `/app/openstack_auth.yaml` in container.

#### 3. Environment Variables (Fallback)

Set `OS_AUTH_URL`, `OS_PROJECT_NAME`, etc. directly in `.env` or docker-compose.

## Development

### Local Setup

```bash
# Install dependencies
pip install -r requirements.txt

# Copy and configure OpenStack credentials
cp openstack_auth.yaml.example openstack_auth.yaml
# Edit openstack_auth.yaml with your credentials

# Run worker
celery -A celery_app worker --loglevel=info
```

### Docker Setup

```bash
# Build image
docker build -f Dockerfile.dev -t worker:dev .

# Run container with OpenStack config
docker run \
  --env-file .env \
  -v $(pwd)/openstack_auth.yaml:/app/openstack_auth.yaml:ro \
  worker:dev
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

- ⚠️ **Never commit** `openstack_auth.yaml` or `clouds.yaml` to git
- Mount credential files as read-only in Docker
- Use environment-specific credentials (dev/staging/prod)
- Rotate passwords regularly
- Consider using application credentials instead of user passwords

## Monitoring

- Check Celery logs for task execution details
- Monitor deployment status in database
- Use Flower for Celery task monitoring (optional)
- OpenStack credentials are logged (masked passwords)

## License

See LICENSE file.
