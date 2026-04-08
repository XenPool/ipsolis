"""Shared pytest configuration.

Adds worker/ to sys.path so tests can import tasks.* without a running
Celery / Redis instance.  The Celery app is instantiated lazily – no
connection is made until a task is actually sent, so import-time setup
is safe even without a broker.

Path resolution works for both:
  - local run:  cd api && python -m pytest tests/
  - Docker run: docker compose exec api python -m pytest tests/
    (worker/ mounted at /app/worker via docker-compose.yml)
"""

import os
import sys


def pytest_configure(config):  # noqa: ARG001
    test_dir = os.path.dirname(os.path.abspath(__file__))

    # local:  api/tests/ → ../../worker/
    # Docker: /app/tests/ → /app/worker/
    candidates = [
        os.path.join(test_dir, "..", "..", "worker"),
        os.path.join(test_dir, "..", "worker"),
    ]
    worker_dir = next(
        (os.path.abspath(c) for c in candidates if os.path.isdir(c)), None
    )
    if worker_dir and worker_dir not in sys.path:
        sys.path.insert(0, worker_dir)
