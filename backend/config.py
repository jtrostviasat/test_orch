"""
Centralized, environment-driven configuration.

All secrets and tunables are read from the environment (12-factor style) via
pydantic-settings. Both the backend and worker import this so behavior stays
consistent across services.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-backed settings singleton for backend and worker."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- App ---
    app_env: str = "development"
    secret_key: str = "change-me"
    worker_host_id: str = "worker_host_01"
    # Grace period (seconds) before the admin host-status poll emits its soft
    # "polling complete (N of M)" sentinel. Late replies can still upgrade it.
    host_poll_grace_seconds: float = 4.0

    # --- PostgreSQL (central state DB) ---
    # You can either provide a full DATABASE_URL, or the discrete parts below.
    database_url: str | None = None
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_user: str = "test_orch"
    postgres_password: str = "test_orch"
    postgres_db: str = "test_orch"

    # --- Celery / RabbitMQ ---
    celery_broker_url: str = "amqp://guest:guest@rabbitmq:5672//"
    celery_result_backend: str = "rpc://"
    rabbitmq_host: str = "rabbitmq"
    rabbitmq_port: int = 5672
    rabbitmq_user: str = "guest"
    rabbitmq_password: str = "guest"

    # --- InfluxDB ---
    influx_url: str = "http://influxdb:8086"
    influx_token: str = "changeme-influx-token"
    influx_org: str = "test_orch"
    influx_bucket: str = "test_logs"

    # --- LDAP ---
    ldap_host: str = "ldap.corp.example.com"
    ldap_port: int = 389
    ldap_use_ssl: bool = False
    ldap_base_dn: str = "dc=corp,dc=example,dc=com"
    ldap_user_dn_template: str = "uid={username},ou=people,dc=corp,dc=example,dc=com"

    # --- Quali CloudShell ---
    quali_api_url: str = "https://cloudshell.corp.example.com/api"
    quali_api_token: str = "changeme-quali-token"

    # --- Artifactory ---
    artifactory_url: str = "https://artifactory.corp.example.com/artifactory"
    artifactory_repo: str = "test-artifacts"
    artifactory_user: str = "ci-user"
    artifactory_token: str = "changeme-artifactory-token"

    # --- Docker (rootless) ---
    docker_host: str = "unix:///run/user/1000/docker.sock"
    test_runs_dir: str = "/tmp/test_runs"

    @property
    def sqlalchemy_url(self) -> str:
        """
        Build the SQLAlchemy connection URL.

        Args:
            None (reads instance settings).

        Returns:
            str: ``database_url`` verbatim if set, otherwise a
            ``postgresql+psycopg://`` URL assembled from the discrete
            ``postgres_*`` fields.
        """
        if self.database_url:
            return self.database_url
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    """
    Return the cached settings singleton (parsed from the environment once).

    Args:
        None.

    Returns:
        Settings: The process-wide settings instance. Call
        ``get_settings.cache_clear()`` in tests to force a re-read.
    """
    return Settings()
