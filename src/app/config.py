"""Application configuration.

All settings come from environment variables (or a local .env file).
`settings` is imported everywhere else; nothing reads os.environ directly.
"""

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Minimum length for a real session secret. token_hex(32) yields 64 chars, so
# this only rejects things nobody would have generated on purpose.
MIN_SESSION_SECRET_LEN = 32

SESSION_SECRET_HELP = (
    "SESSION_SECRET is unsafe (the shipped placeholder, blank, or under "
    f"{MIN_SESSION_SECRET_LEN} characters) but BASE_URL is https, which means this is a real "
    'deployment. Generate one: python -c "import secrets; print(secrets.token_hex(32))"'
)


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

    # Local-only harness switch. When true, web/app.py registers the rehearsal
    # router; production never sets it, so those routes do not exist there at
    # all -- no auth surface, no accidental "fire every reminder now" button.
    # Same shape as bot_enabled: one config value switching a subsystem off.
    rehearsal_enabled: bool = False

    # Same shape as bot_enabled and rehearsal_enabled: one config value
    # switching a subsystem off. Default False so the feature ships switched
    # off, and so tests and dev runs never reach the network.
    discovery_enabled: bool = False

    # Same shape as discovery_enabled: one config value switching the AI
    # triage subsystem off. Default False so the feature ships switched off,
    # and so tests and dev runs never reach the network or spend a real key.
    triage_enabled: bool = False
    # DeepSeek API credentials. Empty by default -- a blank key means the
    # subsystem cannot run even if triage_enabled is somehow set, the same
    # belt-and-suspenders shape discord_token gives bot_enabled.
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    # No default id: the owner picks the exact model, and hardcoding one here
    # would silently start billing a model nobody chose the moment the flag
    # flips on.
    deepseek_model: str = ""

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

    # Written by deploy/backup.sh after a successful upload. The app cannot ask
    # S3 whether a backup landed -- the IAM user is PutObject-only by design --
    # so this marker is the only local evidence a backup ran.
    backup_marker_path: str = "/home/ubuntu/.dekimasen-backup-ok"

    # Defaults
    default_timezone: str = "America/Moncton"

    # Privacy policy contacts (GET /privacy). Deployment config, never
    # committed -- same rule as every other secret-ish value here. Either,
    # both, or neither may be set; the page renders whichever are present
    # and falls back to a neutral line when none are.
    privacy_contact_discord: str = ""
    privacy_contact_email: str = ""

    @model_validator(mode="after")
    def _reject_weak_session_secret(self) -> "Settings":
        """Fail startup rather than sign real session cookies with a secret
        that is public in this repo. Gated on an https BASE_URL because local
        dev (http://localhost:8000, the documented web-only mode) is expected
        to run straight from a fresh clone with the placeholder in place --
        making this unconditional would break that workflow."""
        secret = self.session_secret.strip()
        unsafe = (
            not secret
            or secret == "change-me"
            or len(secret) < MIN_SESSION_SECRET_LEN
        )
        if unsafe and self.base_url.lower().startswith("https"):
            raise ValueError(SESSION_SECRET_HELP)
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
