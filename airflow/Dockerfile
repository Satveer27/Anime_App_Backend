# Airflow image with the app's own virtualenv beside it (ADR 0001: separate virtualenvs).
# Build context is the repo root (see docker-compose.yml). The app code is mounted at /opt/app.
FROM apache/airflow:3.3.2

USER root
RUN mkdir -p /opt/app /opt/app-venv && chown airflow /opt/app /opt/app-venv

USER airflow
RUN pip install --no-cache-dir uv
ENV UV_PROJECT_ENVIRONMENT=/opt/app-venv

WORKDIR /opt/app
COPY --chown=airflow pyproject.toml uv.lock /opt/app/
RUN uv sync --frozen --no-dev --no-install-project
