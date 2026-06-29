# Worker

[![Coverage](https://img.shields.io/endpoint?url=https://six7-click-n-deploy.github.io/worker/badge.json)](https://six7-click-n-deploy.github.io/worker/)

Celery-Worker des App Stores. Konsumiert Deployment-Tasks aus RabbitMQ, klont das App-Repository, führt Packer + Terraform aus und provisioniert auf OpenStack.

## Setup

Dieses Repository wird nicht eigenständig gestartet. Der Worker braucht RabbitMQ, Redis, die Postgres-tfstate-DB und vom Backend dispatchte Tasks — der gesamte Stack wird über das deployment-Repository hochgefahren. Vollständige Anleitung: [deployment/README.md](https://github.com/six7-click-n-deploy/deployment#readme).

Voraussetzung für alle folgenden Befehle: `make dev-up` aus dem `deployment/`-Verzeichnis wurde ausgeführt und der Stack läuft.

## Entwicklung

Alle `make`-Befehle werden aus dem `deployment/`-Verzeichnis des [deployment-Repos](https://github.com/six7-click-n-deploy/deployment) ausgeführt — dort liegt das Makefile.

```bash
# in app-store/deployment
make dev-restart-worker   # Worker neu starten (z. B. nach Änderung an tasks.py)
make dev-logs-worker      # Worker-Logs verfolgen
make shell-worker         # interaktive Shell im Container
```

Tests, Lint und Format laufen im Worker-Container — `make shell-worker` öffnet eine Shell, in der `poetry run pytest`, `poetry run ruff check` und `poetry run ruff format` zur Verfügung stehen.

## Was der Worker tut

- **Deploy**: klont das App-Repo am Release-Tag, baut bei Bedarf ein Packer-Image, führt `terraform apply` aus
- **Destroy**: `terraform destroy` gegen denselben Tag/dieselben Variablen
- **Update**: deployt neue Version im Bestands-State
- **OpenStack-Auth**: per-Task `clouds.yaml`, generiert aus dem vom Backend verschlüsselten Credentials-Envelope

## Technologie-Stack

- **Celery 5** mit RabbitMQ als Broker, Redis als Result-Backend
- **Terraform 1.x** mit Postgres-Remote-State
- **Packer 1.x** für Image-Builds
- **GitPython** für Repo-Klone
- **SQLAlchemy 2.0** nur lesend gegen die App-DB
- **pytest** mit `unit` und `integration` als Markern

## Mehr

- Architektur und projektübergreifende Doku: [.github-Repo](https://github.com/six7-click-n-deploy/.github)
- Backend-Service: [backend-Repo](https://github.com/six7-click-n-deploy/backend)
