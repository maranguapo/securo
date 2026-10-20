"""Per-workspace Pluggy credentials.

Pluggy credentials used to be one pair in the server environment. They now
belong to a workspace, so a self-hosted instance shared by several people
does not run everyone through a single Pluggy account.
"""
import uuid
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workspace import Workspace
from app.models.workspace_integration import WorkspaceIntegration
from app.providers import pluggy as pluggy_module
from app.providers.base import ProviderNotConfiguredError
from app.providers.pluggy import PluggyProvider
from app.services import integration_service

SECRET = "super-secret-value-123"


def _url(workspace_id) -> str:
    return f"/api/workspaces/{workspace_id}/integrations/pluggy"


@pytest.fixture
def verified():
    """Skip the network call that checks the pair against Pluggy."""
    with patch.object(
        integration_service, "verify_pluggy_credentials", new=AsyncMock()
    ) as mock:
        yield mock


async def _save(client, headers, workspace, client_id="cid", secret=SECRET):
    return await client.put(
        _url(workspace.id),
        headers=headers,
        json={"client_id": client_id, "client_secret": secret},
    )


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_status_is_unconfigured_by_default(
    client: AsyncClient, auth_headers, test_workspace
):
    resp = await client.get(_url(test_workspace.id), headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == {"configured": False, "client_id": None}


@pytest.mark.asyncio
async def test_owner_can_save_and_the_secret_is_never_returned(
    client: AsyncClient, auth_headers, test_workspace, verified
):
    resp = await _save(client, auth_headers, test_workspace, client_id="my-id")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"configured": True, "client_id": "my-id"}
    assert SECRET not in resp.text

    status = await client.get(_url(test_workspace.id), headers=auth_headers)
    assert status.json() == {"configured": True, "client_id": "my-id"}
    assert SECRET not in status.text
    verified.assert_awaited_once_with("my-id", SECRET)


@pytest.mark.asyncio
async def test_secret_is_stored_encrypted(
    client: AsyncClient, auth_headers, test_workspace, session: AsyncSession, verified
):
    await _save(client, auth_headers, test_workspace)

    row = (
        await session.execute(
            select(WorkspaceIntegration).where(
                WorkspaceIntegration.workspace_id == test_workspace.id
            )
        )
    ).scalar_one()
    assert SECRET not in row.client_secret_encrypted
    assert row.client_secret_encrypted != SECRET


@pytest.mark.asyncio
async def test_saving_again_replaces_instead_of_duplicating(
    client: AsyncClient, auth_headers, test_workspace, session: AsyncSession, verified
):
    await _save(client, auth_headers, test_workspace, client_id="first")
    await _save(client, auth_headers, test_workspace, client_id="second", secret="other")

    rows = (
        await session.execute(
            select(WorkspaceIntegration).where(
                WorkspaceIntegration.workspace_id == test_workspace.id
            )
        )
    ).scalars().all()
    assert [r.client_id for r in rows] == ["second"]


@pytest.mark.asyncio
async def test_rejected_credentials_are_not_saved(
    client: AsyncClient, auth_headers, test_workspace
):
    with patch.object(
        integration_service,
        "verify_pluggy_credentials",
        new=AsyncMock(side_effect=integration_service.InvalidCredentialsError("nope")),
    ):
        resp = await _save(client, auth_headers, test_workspace)
    assert resp.status_code == 400

    status = await client.get(_url(test_workspace.id), headers=auth_headers)
    assert status.json()["configured"] is False


@pytest.mark.asyncio
async def test_unreachable_pluggy_is_a_502_and_nothing_is_saved(
    client: AsyncClient, auth_headers, test_workspace
):
    with patch.object(
        integration_service,
        "verify_pluggy_credentials",
        new=AsyncMock(side_effect=integration_service.ProviderUnreachableError("down")),
    ):
        resp = await _save(client, auth_headers, test_workspace)
    assert resp.status_code == 502

    status = await client.get(_url(test_workspace.id), headers=auth_headers)
    assert status.json()["configured"] is False


@pytest.mark.asyncio
async def test_blank_values_are_rejected(
    client: AsyncClient, auth_headers, test_workspace, verified
):
    resp = await _save(client, auth_headers, test_workspace, client_id="   ")
    assert resp.status_code == 400
    verified.assert_not_awaited()


@pytest.mark.asyncio
async def test_viewer_cannot_write_and_never_sees_the_client_id(
    client: AsyncClient, auth_headers, viewer_auth_headers, test_workspace, verified
):
    await _save(client, auth_headers, test_workspace, client_id="owner-id")

    assert (await _save(client, viewer_auth_headers, test_workspace)).status_code == 403
    deleted = await client.delete(_url(test_workspace.id), headers=viewer_auth_headers)
    assert deleted.status_code == 403

    seen = await client.get(_url(test_workspace.id), headers=viewer_auth_headers)
    assert seen.status_code == 200
    assert seen.json() == {"configured": True, "client_id": None}


@pytest.mark.asyncio
async def test_another_users_workspace_is_not_found(
    client: AsyncClient, auth_headers, verified
):
    resp = await client.get(_url(uuid.uuid4()), headers=auth_headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_owner_can_remove_the_credentials(
    client: AsyncClient, auth_headers, test_workspace, verified
):
    await _save(client, auth_headers, test_workspace)

    resp = await client.delete(_url(test_workspace.id), headers=auth_headers)
    assert resp.status_code == 204

    status = await client.get(_url(test_workspace.id), headers=auth_headers)
    assert status.json() == {"configured": False, "client_id": None}


@pytest.mark.asyncio
async def test_providers_endpoint_reports_pluggy_per_workspace(
    client: AsyncClient, auth_headers, test_workspace, verified
):
    def pluggy(body):
        return next(p for p in body["providers"] if p["name"] == "pluggy")

    before = await client.get("/api/connections/providers", headers=auth_headers)
    assert pluggy(before.json())["configured"] is False

    await _save(client, auth_headers, test_workspace)

    after = await client.get("/api/connections/providers", headers=auth_headers)
    assert pluggy(after.json())["configured"] is True


@pytest.mark.asyncio
async def test_connect_token_without_credentials_explains_what_to_do(
    client: AsyncClient, auth_headers
):
    resp = await client.post(
        "/api/connections/connect-token",
        headers=auth_headers,
        json={"provider": "pluggy"},
    )
    assert resp.status_code == 400
    assert "Workspace settings" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Provider resolution
# ---------------------------------------------------------------------------
async def _second_workspace(session: AsyncSession, owner_id) -> Workspace:
    other = Workspace(id=uuid.uuid4(), name="Other", created_by_user_id=owner_id)
    session.add(other)
    await session.commit()
    return other


@pytest.mark.asyncio
async def test_provider_is_built_from_the_workspaces_own_credentials(
    session: AsyncSession, test_user, test_workspace
):
    other = await _second_workspace(session, test_user.id)
    await integration_service.save_pluggy_credentials(
        session, test_workspace.id, "id-a", "secret-a", verify=False
    )
    await integration_service.save_pluggy_credentials(
        session, other.id, "id-b", "secret-b", verify=False
    )

    a = await integration_service.provider_for_workspace(session, "pluggy", test_workspace.id)
    b = await integration_service.provider_for_workspace(session, "pluggy", other.id)

    assert isinstance(a, PluggyProvider) and isinstance(b, PluggyProvider)
    assert (a._client_id, a._client_secret) == ("id-a", "secret-a")
    assert (b._client_id, b._client_secret) == ("id-b", "secret-b")


@pytest.mark.asyncio
async def test_workspace_without_credentials_cannot_get_a_provider(
    session: AsyncSession, test_user, test_workspace
):
    other = await _second_workspace(session, test_user.id)
    await integration_service.save_pluggy_credentials(
        session, test_workspace.id, "id-a", "secret-a", verify=False
    )

    with pytest.raises(ProviderNotConfiguredError):
        await integration_service.provider_for_workspace(session, "pluggy", other.id)


@pytest.mark.asyncio
async def test_unreadable_secret_counts_as_not_configured(
    session: AsyncSession, test_workspace
):
    """A rotated SECRET_KEY leaves ciphertext nobody can decrypt."""
    session.add(
        WorkspaceIntegration(
            workspace_id=test_workspace.id,
            provider="pluggy",
            client_id="cid",
            client_secret_encrypted="not-valid-fernet-ciphertext",
        )
    )
    await session.commit()

    status = await integration_service.get_status(session, test_workspace.id)
    assert status.configured is False
    with pytest.raises(ProviderNotConfiguredError):
        await integration_service.provider_for_workspace(
            session, "pluggy", test_workspace.id
        )


@pytest.mark.asyncio
async def test_other_providers_still_resolve_from_the_registry(session: AsyncSession):
    from app.providers import _PROVIDERS

    sentinel = object()
    with patch.dict(_PROVIDERS, {"fake": lambda: sentinel}):
        got = await integration_service.provider_for_workspace(
            session, "fake", uuid.uuid4()
        )
    assert got is sentinel


# ---------------------------------------------------------------------------
# The provider itself
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_provider_without_credentials_refuses_to_authenticate():
    with pytest.raises(ProviderNotConfiguredError):
        await PluggyProvider()._ensure_api_key()


@pytest.mark.asyncio
async def test_api_keys_are_not_shared_between_credential_pairs():
    pluggy_module._API_KEY_CACHE.clear()
    keys = {"id-a": "key-a", "id-b": "key-b"}
    calls: list[str] = []

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json):
            calls.append(json["clientId"])
            return httpx.Response(
                200, json={"apiKey": keys[json["clientId"]]},
                request=httpx.Request("POST", url),
            )

    with patch("app.providers.pluggy.httpx.AsyncClient", return_value=FakeClient()):
        a = PluggyProvider("id-a", "s")
        b = PluggyProvider("id-b", "s")
        assert await a._ensure_api_key() == "key-a"
        assert await b._ensure_api_key() == "key-b"
        # Second call per workspace is served from its own cache entry.
        assert await a._ensure_api_key() == "key-a"
        assert await b._ensure_api_key() == "key-b"

    assert calls == ["id-a", "id-b"]
    pluggy_module._API_KEY_CACHE.clear()


@pytest.mark.asyncio
async def test_changing_the_secret_does_not_reuse_the_old_api_key():
    pluggy_module._API_KEY_CACHE.clear()
    issued = iter(["old-key", "new-key"])

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json):
            return httpx.Response(
                200, json={"apiKey": next(issued)}, request=httpx.Request("POST", url)
            )

    with patch("app.providers.pluggy.httpx.AsyncClient", return_value=FakeClient()):
        assert await PluggyProvider("id", "old-secret")._ensure_api_key() == "old-key"
        assert await PluggyProvider("id", "new-secret")._ensure_api_key() == "new-key"
    pluggy_module._API_KEY_CACHE.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status, expected",
    [
        (401, integration_service.InvalidCredentialsError),
        (403, integration_service.InvalidCredentialsError),
        (500, integration_service.ProviderUnreachableError),
    ],
)
async def test_verify_maps_pluggy_answers(status, expected):
    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json):
            return httpx.Response(status, request=httpx.Request("POST", url))

    with patch("app.services.integration_service.httpx.AsyncClient", return_value=FakeClient()):
        with pytest.raises(expected):
            await integration_service.verify_pluggy_credentials("id", "secret")


@pytest.mark.asyncio
async def test_verify_treats_a_network_failure_as_unreachable():
    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json):
            raise httpx.ConnectError("boom")

    with patch("app.services.integration_service.httpx.AsyncClient", return_value=FakeClient()):
        with pytest.raises(integration_service.ProviderUnreachableError):
            await integration_service.verify_pluggy_credentials("id", "secret")
