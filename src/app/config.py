"""Application configuration.

All settings come from environment variables (or a local .env file).
`settings` is imported everywhere else; nothing reads os.environ directly.
"""

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Shipped in .env.example, so public. Anyone who copies the file and runs it
# unchanged would sign cookies with a secret this repo hands out.
_PLACEHOLDER_SECRET = "change-me"
_MIN_SECRET_LEN = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Discord
    discord_token: str = ""  # empty -> web-only dev mode
    discord_client_id: str = ""
    discord_client_secret: str = ""
    # Set to a test server's ID for instant slash-command sync during local
    # dev (guild-scoped syncs propagate in seconds; global syncs can take up
    # to an hour). Leave empty in production so commands reach every server
    # the bot is added to.
    dev_guild_id: int = 0

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

    @model_validator(mode="after")
    def _reject_weak_session_secret(self) -> "Settings":
        """Fail closed in production, stay quiet in local dev.

        A guessable secret lets an attacker forge `oauth_state`, defeating the
        OAuth CSRF check in web/auth.py (login CSRF / account fixation). It is
        not a direct session forgery — auth.py treats the DB-backed `sid` as
        the real credential — but it is still a live hole.

        Gated on an https base_url because that is the only signal we have
        that this is a real deployment: dev runs web-only against
        http://localhost:8000 and must keep working with the default.
        """
        if not self.base_url.lower().startswith("https"):
            return self
        secret = self.session_secret.strip()
        if secret == _PLACEHOLDER_SECRET or not secret or len(secret) < _MIN_SECRET_LEN:
            raise ValueError(
                "SESSION_SECRET is unsafe for an https deployment: it must be set, "
                f"not the placeholder '{_PLACEHOLDER_SECRET}', and at least "
                f"{_MIN_SECRET_LEN} characters. Generate one with: "
                'python -c "import secrets; print(secrets.token_hex(32))"'
            )
        return self

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
