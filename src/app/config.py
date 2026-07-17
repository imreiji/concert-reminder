"""Application configuration.

All settings come from environment variables (or a local .env file).
`settings` is imported everywhere else; nothing reads os.environ directly.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Discord
    discord_token: str = ""  # empty -> web-only dev mode
    discord_client_id: str = ""
    discord_client_secret: str = ""

    # Access control: comma-separated Discord user IDs with edit rights
    editor_whitelist: str = ""
    # Access control: comma-separated Discord user IDs who can manage editors
    admin_whitelist: str = ""

    # Web
    base_url: str = "http://localhost:8000"
    session_secret: str = "change-me"
    web_host: str = "127.0.0.1"
    web_port: int = 8000

    # Storage
    database_url: str = "sqlite+aiosqlite:///./app.db"

    # Defaults
    default_timezone: str = "America/Moncton"

    @property
    def editor_ids(self) -> frozenset[int]:
        """Parsed whitelist. Malformed entries are ignored rather than fatal."""
        ids: set[int] = set()
        for part in self.editor_whitelist.split(","):
            part = part.strip()
            if part.isdigit():
                ids.add(int(part))
        return frozenset(ids)

    def is_editor(self, discord_id: int) -> bool:
        return discord_id in self.editor_ids

    @property
    def admin_ids(self) -> frozenset[int]:
        """Parsed whitelist. Malformed entries are ignored rather than fatal."""
        ids: set[int] = set()
        for part in self.admin_whitelist.split(","):
            part = part.strip()
            if part.isdigit():
                ids.add(int(part))
        return frozenset(ids)

    def is_admin(self, discord_id: int) -> bool:
        return discord_id in self.admin_ids

    @property
    def bot_enabled(self) -> bool:
        return bool(self.discord_token)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
