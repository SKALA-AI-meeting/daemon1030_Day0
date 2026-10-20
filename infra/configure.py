"""Create local configuration without overwriting existing credentials."""

import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    destination = ROOT / ".env"
    if destination.exists() and destination.read_text().strip():
        print("기존 .env를 유지합니다.")
        return
    content = (
        (ROOT / ".env.example")
        .read_text()
        .replace("AUTH_SECRET=\n", "AUTH_SECRET=" + secrets.token_urlsafe(48) + "\n")
    )
    destination.write_text(content)
    destination.chmod(0o600)
    print("개발용 .env를 생성했습니다. 비밀 값은 출력하지 않습니다.")


if __name__ == "__main__":
    main()
