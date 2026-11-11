"""Service settings, read from the environment.

| Variable                   | Default | Meaning                                                  |
|----------------------------|---------|----------------------------------------------------------|
| DATABASE_URL               | none    | PostgreSQL connection string (required)                  |
| DB_POOL_SIZE               | 10      | Most connections one process opens                       |
| DB_POOL_TIMEOUT_SECONDS    | 5       | How long a request waits for a free connection           |
| LOCK_TIMEOUT_MS            | 3000    | How long a transaction waits for a row lock              |
| DEADLOCK_MAX_ATTEMPTS      | 3       | Tries for a transaction that keeps hitting a deadlock    |
| RETRY_AFTER_SECONDS        | 1       | `Retry-After` on a 503                                   |
"""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    pool_size: int = 10
    pool_timeout_seconds: float = 5.0
    lock_timeout_ms: int = 3000
    deadlock_max_attempts: int = 3
    retry_after_seconds: int = 1

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ
        return cls(
            database_url=env["DATABASE_URL"],
            pool_size=int(env.get("DB_POOL_SIZE", cls.pool_size)),
            pool_timeout_seconds=float(
                env.get("DB_POOL_TIMEOUT_SECONDS", cls.pool_timeout_seconds)
            ),
            lock_timeout_ms=int(env.get("LOCK_TIMEOUT_MS", cls.lock_timeout_ms)),
            deadlock_max_attempts=int(env.get("DEADLOCK_MAX_ATTEMPTS", cls.deadlock_max_attempts)),
            retry_after_seconds=int(env.get("RETRY_AFTER_SECONDS", cls.retry_after_seconds)),
        )
