# Worker CI/CD Setup - Quick Start

## 📋 Was wurde eingerichtet

### 1. **Linting & Formatting**
- ✅ **Ruff**: Schneller Python-Linter (ersetzt flake8, pylint)
- ✅ **Black**: Code-Formatter
- ✅ **isort**: Import-Sortierung
- ✅ **MyPy**: Type-Checking

### 2. **Testing**
- ✅ **pytest**: Test-Framework
- ✅ **pytest-asyncio**: Async-Tests
- ✅ **pytest-cov**: Coverage-Reports
- ✅ **pytest-mock**: Mocking
- ✅ Test-Beispiele für Git-Service

### 3. **CI/CD Pipeline**
- ✅ GitHub Actions Workflow (`.github/workflows/worker-ci.yml`)
- ✅ 4 Stages: Lint → Test → Build → Push
- ✅ Image-Push nur bei merge auf main
- ✅ PR: Build ohne Push

### 4. **Developer Tools**
- ✅ Makefile mit nützlichen Befehlen
- ✅ Pre-commit Hooks Configuration
- ✅ Umfangreiche README-Dokumentation

## 🚀 Schnellstart

### Schritt 1: Dependencies installieren

```bash
cd worker

# Poetry installieren (falls nicht vorhanden)
curl -sSL https://install.python-poetry.org | python3 -

# Dependencies installieren
poetry install --with dev
```

### Schritt 2: Code formatieren & linting

```bash
# Alles auf einmal
make format    # Auto-format
make lint      # Prüfen

# Oder einzeln
poetry run black .
poetry run isort .
poetry run ruff check .
```

### Schritt 3: Tests ausführen

```bash
# Alle Tests
make test

# Mit Coverage
make test-cov

# Nur Unit-Tests
make test-unit
```

### Schritt 4: Pre-commit Hooks (optional aber empfohlen)

```bash
# Pre-commit installieren
pip install pre-commit

# Hooks aktivieren
pre-commit install

# Einmalig alle Files prüfen
pre-commit run --all-files
```

## 📊 CI/CD Pipeline Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                        Push / Pull Request                       │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  Stage 1: Lint & Format Check                                   │
│  ├─ Ruff check                                                  │
│  ├─ Black formatting check                                      │
│  ├─ isort check                                                 │
│  └─ MyPy type checking                                          │
└────────────────────────────┬────────────────────────────────────┘
                             │ ✅ Pass
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  Stage 2: Tests (Python 3.10 & 3.11)                           │
│  ├─ pytest unit tests                                           │
│  ├─ Coverage report                                             │
│  └─ Upload to Codecov                                           │
└────────────────────────────┬────────────────────────────────────┘
                             │ ✅ Pass
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  Stage 3: Build Docker Image                                    │
│  ├─ Build with Buildx                                           │
│  ├─ Cache layers                                                │
│  └─ Save as artifact                                            │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ├─── PR → Stop (no push)
                             │
                             └─── main branch → Continue
                                                │
                                                ▼
                   ┌────────────────────────────────────────────┐
                   │  Stage 4: Push Docker Image                │
                   │  ├─ Load from artifact                     │
                   │  ├─ Push to ghcr.io                        │
                   │  └─ Tag: latest, main, sha-<commit>        │
                   └────────────────────────────────────────────┘
```

## 🎯 Wann wird was ausgeführt?

| Event | Lint | Test | Build | Push |
|-------|------|------|-------|------|
| **Push to main** | ✅ | ✅ | ✅ | ✅ |
| **Push to develop** | ✅ | ✅ | ✅ | ❌ |
| **Pull Request** | ✅ | ✅ | ✅ | ❌ |
| **Draft PR** | ✅ | ✅ | ✅ | ❌ |

## 🔧 Konfigurationsdateien

### pyproject.toml
- Poetry dependencies
- Ruff, Black, isort, pytest config
- MyPy settings
- Coverage config

### .github/workflows/worker-ci.yml
- GitHub Actions Workflow
- 4-Stage Pipeline
- Matrix testing (Python 3.10 & 3.11)

### .pre-commit-config.yaml
- Pre-commit hooks
- Auto-format vor jedem commit

### Makefile
- Shortcut-Befehle für Development

## 📈 Code Coverage

Coverage-Reports werden automatisch zu Codecov hochgeladen. Setup:

1. Gehe zu [codecov.io](https://codecov.io)
2. Verbinde dein GitHub-Repository
3. Coverage wird bei jedem Push/PR aktualisiert

## 🐳 Docker Image

**Registry:** `ghcr.io/<your-org>/worker`

**Tags:**
- `latest` - Neuester Build von main
- `main` - Main branch
- `sha-abc123` - Spezifischer Commit

**Pull:**
```bash
docker pull ghcr.io/<your-org>/worker:latest
```

## 💡 Tipps

### Lokale CI-Simulation
```bash
# Genau das, was CI auch macht
make check
```

### Vor jedem Commit
```bash
make format  # Auto-fix
make lint    # Prüfen
make test    # Tests
```

### Schnelles Debugging
```bash
# Nur failing test
poetry run pytest tests/test_git_service.py::TestGitServiceURLParsing::test_parse_ssh_url -v

# Mit print statements
poetry run pytest -s tests/test_git_service.py
```

## 📚 Weitere Infos

- [Ruff Documentation](https://docs.astral.sh/ruff/)
- [Black Documentation](https://black.readthedocs.io/)
- [pytest Documentation](https://docs.pytest.org/)
- [GitHub Actions Documentation](https://docs.github.com/en/actions)
