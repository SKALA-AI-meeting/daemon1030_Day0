import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Settings:
    database_url: str = field(
        default_factory=lambda: os.getenv(
            "DATABASE_URL",
            "postgresql+psycopg://dayzero:dayzero@127.0.0.1:55432/dayzero",
        )
    )
    secret: str = field(default_factory=lambda: os.getenv("AUTH_SECRET", ""))
    development: bool = field(
        default_factory=lambda: os.getenv("DAYZERO_DEVELOPMENT", "false").lower() == "true"
    )
    agent_url: str = field(default_factory=lambda: os.getenv("AGENT_URL", "http://127.0.0.1:8001"))
    rabbit_url: str = field(
        default_factory=lambda: os.getenv("RABBITMQ_URL", "amqp://dayzero:dayzero@127.0.0.1:5673/")
    )
    origins: tuple[str, ...] = (
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8080",
        "http://127.0.0.1:8080",
    )
    heartbeat_grace: int = 15

    def validate(self):
        if len(self.secret) < 32:
            raise RuntimeError("AUTH_SECRET에 32자 이상의 임의 문자열을 설정하세요.")
        return self
