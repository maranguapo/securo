"""Per-workspace credentials for third-party providers.

Today this is Pluggy only. The secret is stored as Fernet ciphertext and is
never handed back to a caller outside this module except as the plaintext
needed to build a provider; the API exposes only "is one set".
"""
import uuid
from dataclasses import dataclass
from typing import Optional

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.services.crypto import decrypt, encrypt
from app.models.workspace_integration import WorkspaceIntegration
from app.providers import get_provider
from app.providers.base import BankProvider, ProviderNotConfiguredError
from app.providers.pluggy import PLUGGY_API_BASE, PluggyProvider

PLUGGY = "pluggy"


class InvalidCredentialsError(ValueError):
    """The provider rejected the client id / secret pair."""


class ProviderUnreachableError(Exception):
    """Credentials could not be checked because the provider did not answer."""


@dataclass(frozen=True)
class IntegrationStatus:
    configured: bool
    client_id: Optional[str] = None


async def _get_row(
    session: AsyncSession, workspace_id: uuid.UUID, provider: str
) -> Optional[WorkspaceIntegration]:
    result = await session.execute(
        select(WorkspaceIntegration).where(
            WorkspaceIntegration.workspace_id == workspace_id,
            WorkspaceIntegration.provider == provider,
        )
    )
    return result.scalar_one_or_none()


async def get_status(
    session: AsyncSession, workspace_id: uuid.UUID, provider: str = PLUGGY
) -> IntegrationStatus:
    row = await _get_row(session, workspace_id, provider)
    # A row whose secret no longer decrypts (SECRET_KEY was rotated) is as
    # good as absent: report it unconfigured so the owner is asked to re-enter.
    if row is None or decrypt(row.client_secret_encrypted) is None:
        return IntegrationStatus(configured=False)
    return IntegrationStatus(configured=True, client_id=row.client_id)


async def verify_pluggy_credentials(client_id: str, client_secret: str) -> None:
    """Ask Pluggy to authenticate the pair, so a typo fails at save time
    rather than on the user's first bank connection."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{PLUGGY_API_BASE}/auth",
                json={"clientId": client_id, "clientSecret": client_secret},
            )
    except httpx.HTTPError as exc:
        raise ProviderUnreachableError(str(exc)) from exc
    if resp.status_code in (400, 401, 403):
        raise InvalidCredentialsError("Pluggy rejected these credentials")
    if resp.status_code >= 400:
        raise ProviderUnreachableError(f"Pluggy answered HTTP {resp.status_code}")


async def save_pluggy_credentials(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    client_id: str,
    client_secret: str,
    *,
    verify: bool = True,
) -> IntegrationStatus:
    client_id = client_id.strip()
    client_secret = client_secret.strip()
    if not client_id or not client_secret:
        raise ValueError("Client ID and client secret are required")
    if verify:
        await verify_pluggy_credentials(client_id, client_secret)

    ciphertext = encrypt(client_secret)
    assert ciphertext is not None  # client_secret is non-empty
    row = await _get_row(session, workspace_id, PLUGGY)
    if row is None:
        row = WorkspaceIntegration(workspace_id=workspace_id, provider=PLUGGY)
        session.add(row)
    row.client_id = client_id
    row.client_secret_encrypted = ciphertext
    await session.commit()
    return IntegrationStatus(configured=True, client_id=client_id)


async def delete_integration(
    session: AsyncSession, workspace_id: uuid.UUID, provider: str = PLUGGY
) -> None:
    await session.execute(
        delete(WorkspaceIntegration).where(
            WorkspaceIntegration.workspace_id == workspace_id,
            WorkspaceIntegration.provider == provider,
        )
    )
    await session.commit()


async def provider_for_workspace(
    session: AsyncSession, provider_name: str, workspace_id: uuid.UUID
) -> BankProvider:
    """Build a provider instance for a workspace.

    Providers configured through the environment resolve as before. Pluggy is
    built from the workspace's own credentials; without them this raises
    `ProviderNotConfiguredError`, which the sync path already treats as a
    configuration problem rather than a bank failure.
    """
    if provider_name != PLUGGY:
        return get_provider(provider_name)

    row = await _get_row(session, workspace_id, PLUGGY)
    secret = decrypt(row.client_secret_encrypted) if row else None
    if row is None or not secret:
        raise ProviderNotConfiguredError(
            "Pluggy credentials are not set for this workspace. "
            "Add them in Workspace settings."
        )
    return PluggyProvider(client_id=row.client_id, client_secret=secret)
