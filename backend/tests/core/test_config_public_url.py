from src.config import Settings


def test_managed_runtime_public_url_override_wins_over_dotenv(
    monkeypatch,
    tmp_path,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        'PUBLIC_URL="https://production.example.com/"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("PUPPYONE_PUBLIC_URL_OVERRIDE", "http://localhost:9090")

    settings = Settings(_env_file=env_file)

    assert settings.PUBLIC_URL == "http://localhost:9090"
