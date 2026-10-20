import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.workspace import WorkspaceKind


class WorkspaceRead(BaseModel):
    id: uuid.UUID
    name: str
    # Deliberately a bare `str` on the way out: reads must not blow up on
    # a row that predates the current kind list. Writes are validated.
    kind: str
    is_archived: bool
    default_currency: str
    locale: Optional[str] = None
    # The workspace's own calendar, or None to follow the application
    # timezone. See `core.app_clock`.
    timezone: Optional[str] = None
    # Where the workspace operates fiscally. Never the UI language: see
    # `models.workspace.Workspace.tax_jurisdiction`.
    tax_jurisdiction: Optional[str] = None
    icon: Optional[str] = None
    color: Optional[str] = None
    created_at: datetime
    created_by_user_id: Optional[uuid.UUID] = None
    managed_by_user_id: Optional[uuid.UUID] = None
    # The current user's role inside this workspace, when surfaced via
    # /api/workspaces (the listing endpoint). Omitted from per-workspace
    # detail responses since the membership row is fetched alongside.
    role: Optional[str] = None
    # Which modules this workspace shows, resolved server-side by
    # `services.module_service`. The frontend consumes this list; it must
    # never re-derive it from `kind`, or the two copies drift and a user
    # sees a module the server thinks is off.
    enabled_modules: list[str] = []

    model_config = ConfigDict(from_attributes=True)


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    kind: WorkspaceKind = "personal"
    default_currency: Optional[str] = Field(default=None, min_length=3, max_length=3)
    locale: Optional[str] = Field(default=None, max_length=10)
    # Configuration, unlike `kind`: a business can move, and a workspace set
    # up before packs existed needs a way to say where it files.
    tax_jurisdiction: Optional[str] = Field(default=None, max_length=10)
    icon: Optional[str] = Field(default=None, max_length=50)
    color: Optional[str] = Field(default=None, max_length=7)
    # When True, also add the creator as an `owner` member. When False
    # (default), the creator is only the external manager — useful when
    # the workspace will be handed off to someone else as the day-to-day
    # owner.
    self_membership: bool = False


class WorkspaceUpdate(BaseModel):
    # No `kind` here on purpose: it is fixed at creation. See
    # `models.workspace.WorkspaceKind`.
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    icon: Optional[str] = Field(default=None, max_length=50)
    color: Optional[str] = Field(default=None, max_length=7)
    default_currency: Optional[str] = Field(default=None, min_length=3, max_length=3)
    locale: Optional[str] = Field(default=None, max_length=10)
    # An IANA name to keep this workspace's calendar in, or null to follow
    # the application timezone again. Validated by the route.
    timezone: Optional[str] = Field(default=None, max_length=64)
    # Editable, unlike `kind`: a business relocates, and every workspace that
    # existed before jurisdictions did needs a way to say where it files.
    tax_jurisdiction: Optional[str] = Field(default=None, max_length=10)


class MemberRead(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    email: str
    display_name: Optional[str] = None
    role: str
    joined_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MemberInvite(BaseModel):
    email: EmailStr
    role: str = "editor"
    # Optional password — only used when inviting a brand-new user. If
    # omitted, the endpoint rejects the invite when the target user
    # doesn't exist. (Email-based magic-link onboarding can come later.)
    password: Optional[str] = Field(default=None, min_length=8, max_length=128)


class MemberRoleUpdate(BaseModel):
    role: str


class IntegrationStatusRead(BaseModel):
    """Whether a workspace has credentials for a provider.

    The secret is write-only and never appears here. `client_id` is shown
    only to owners, who manage the integration.
    """

    configured: bool
    client_id: Optional[str] = None


class PluggyCredentialsUpdate(BaseModel):
    client_id: str = Field(min_length=1, max_length=255)
    client_secret: str = Field(min_length=1, max_length=500)


class AutomationSettingsRead(BaseModel):
    ai_categorization_enabled: bool = False
    llm_connection_id: Optional[uuid.UUID] = None
    llm_model: Optional[str] = None
    min_confidence: float = 0.6
    digest_enabled: bool = False
    # Wall-clock "HH:MM" in the workspace's own timezone.
    digest_time: str = "20:00"
    # False when the server runs without the agents module: the LLM
    # connections live there, so the feature cannot be turned on.
    agents_enabled: bool = True


class AutomationSettingsUpdate(BaseModel):
    ai_categorization_enabled: bool
    llm_connection_id: Optional[uuid.UUID] = None
    llm_model: Optional[str] = Field(default=None, max_length=120)
    min_confidence: float = Field(default=0.6, ge=0.3, le=1.0)
    digest_enabled: bool = False
    digest_time: str = Field(default="20:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")


class AutomationRunResult(BaseModel):
    categorized: int
    # Transactions put in front of the user in a digest sent just now.
    digest_sent: int = 0


class TelegramTokenUpdate(BaseModel):
    token: str = Field(min_length=1, max_length=200)


class TelegramLinkRead(BaseModel):
    url: str
    expires_at: datetime


class TelegramChatRead(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    # Only owners see whose chat it is; everyone else sees their own.
    email: Optional[str] = None
    telegram_username: Optional[str] = None
    is_mine: bool
    created_at: datetime
