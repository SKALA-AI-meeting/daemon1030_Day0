import subprocess
import sys

from services.gateway.persistence.database import database
from services.gateway.seed import seed
from services.gateway.settings import Settings

if __name__ == "__main__":
    subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "migrations/alembic.ini",
            "upgrade",
            "head",
        ],
        check=True,
    )
    settings = Settings().validate()
    engine, sessions = database(settings.database_url)
    with sessions.begin() as db:
        seed(db, settings.development)
