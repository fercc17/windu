# IS-CMDB Local Development

This guide covers local development setup using Docker Compose.

## Prerequisites

- Docker 20.10+
- Docker Compose 2.0+
- Git

## Quick Start

```bash
# Clone the repository
git clone https://github.com/fercc17/is-cmdb.git
cd is-cmdb

# Start the stack
make up

# Access the application
open http://localhost:8000
```

The `make up` command will:
1. Start PostgreSQL, Redis, and Django services
2. Run database migrations automatically
3. Make the application available at http://localhost:8000

## Available Commands

```bash
make help       # Show all available commands
make up         # Start all services
make down       # Stop all services
make restart    # Restart all services
make logs       # Tail logs from all services
make shell      # Open Django shell
make migrate    # Run Django migrations
make test       # Run test suite
make parse      # Run parser (requires SOURCE=/path/to/is-infrastructure)
make clean      # Stop services and remove volumes
```

## Development Workflow

### Starting Development

```bash
# Start services in detached mode
make up

# Watch logs
make logs
```

### Making Code Changes

The Django application runs with `--reload` flag, so code changes are automatically detected and the server restarts.

### Database Changes

```bash
# Create migrations after model changes
docker-compose exec web python manage.py makemigrations

# Apply migrations
make migrate
```

### Running the Parser

```bash
# Parse is-infrastructure repo (must be cloned locally)
make parse SOURCE=/path/to/is-infrastructure

# Example
make parse SOURCE=../is-infrastructure
```

### Django Shell

```bash
# Open Django shell for debugging
make shell

# Example queries
>>> from cmdb.apps.environments.models import Environment
>>> Environment.objects.count()
>>> Environment.objects.filter(region='amer').count()
```

### Running Tests

```bash
# Run full test suite
make test

# Run specific test file
docker-compose exec web python manage.py test cmdb.apps.environments.tests

# Run with coverage
docker-compose exec web coverage run --source='.' manage.py test
docker-compose exec web coverage report
```

## Architecture

The stack consists of three services:

### PostgreSQL (db)
- Port: 5432
- Database: `cmdb`
- User: `cmdb`
- Password: `cmdb`
- Persistent volume: `postgres_data`

### Redis (redis)
- Port: 6379
- Used for: Caching, session storage, future poller coordination

### Django (web)
- Port: 8000
- Workers: 4 Gunicorn workers with auto-reload
- Volume mount: `.:/app` (live code changes)
- Environment:
  - `DATABASE_URL`: postgresql://cmdb:cmdb@db:5432/cmdb
  - `REDIS_URL`: redis://redis:6379/0
  - `DEBUG`: True
  - `SECRET_KEY`: dev-secret-key-change-in-production

## Troubleshooting

### Port Already in Use

If ports 5432, 6379, or 8000 are already in use:

```bash
# Check what's using the port
lsof -i :8000

# Stop existing services
make down

# Or change ports in docker-compose.yml
```

### Database Connection Issues

```bash
# Check database health
docker-compose ps

# Verify database is ready
docker-compose exec db pg_isready -U cmdb

# Reset database
make clean
make up
```

### Application Errors

```bash
# View logs
make logs

# Access web container
docker-compose exec web bash

# Check Django status
docker-compose exec web python manage.py check
```

### Clean Start

```bash
# Stop everything and remove volumes
make clean

# Start fresh
make up
```

## IDE Setup

### VS Code

Install extensions:
- Python
- Docker
- Remote - Containers

Configure Python interpreter to use the Docker container.

### PyCharm

1. Configure Docker Compose as remote interpreter
2. Set source root to `/app`
3. Enable Django support

## Code Style

```bash
# Install pre-commit hooks (on host)
pip install pre-commit
pre-commit install

# Run linting
docker-compose exec web ruff check .

# Format code
docker-compose exec web black .
```

## Common Tasks

### Add a New App

```bash
docker-compose exec web python manage.py startapp myapp cmdb/apps/myapp
```

### Create Superuser

```bash
docker-compose exec web python manage.py createsuperuser
```

### Backup Database

```bash
docker-compose exec db pg_dump -U cmdb cmdb > backup.sql
```

### Restore Database

```bash
cat backup.sql | docker-compose exec -T db psql -U cmdb cmdb
```

## Performance

For better performance during development:

```bash
# Use fewer workers
# Edit docker-compose.yml: --workers 2

# Disable debug toolbar
# Set DEBUG_TOOLBAR = False in settings.py
```

## Next Steps

- Read [ARCHITECTURE.md](../ARCHITECTURE.md) for system overview
- Read [SCHEMA.md](../SCHEMA.md) for database schema
- Read [PARSER.md](../PARSER.md) for parser details
- Check [GITHUB_ISSUES.md](../GITHUB_ISSUES.md) for planned features
