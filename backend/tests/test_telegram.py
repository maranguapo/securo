"""Telegram bot: client, token storage, chat linking, handlers, runner, API."""
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import redis as redis_core
from app.models.telegram_link import TelegramLink
from app.models.user import User
from app.models.workspace import Workspace
from app.models.workspace_integration import WorkspaceIntegration
from app.services import integration_service, telegram_service
from app.telegram import handlers, runner
from app.telegram.client import TelegramClient, TelegramError
from tests.conftest import TestSessionLocal

TOKEN = "123456:SECRET-bot-token"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
class FakeRedis:
    def __init__(self):
        self.data: dict[str, str] = {}
        self.ttl: dict[str, Optional[int]] = {}

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, ex=None):
        self.data[key] = str(value)
        self.ttl[key] = ex

    async def delete(self, key):
        self.data.pop(key, None)


@pytest.fixture
def fake_redis():
    redis = FakeRedis()
    with patch.object(redis_core, "get_redis", AsyncMock(return_value=redis)):
        yield redis


class FakeBot(TelegramClient):
    """Records what the bot would have sent."""

    def __init__(self):
        super().__init__(TOKEN)
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id, text, *, reply_markup=None):
        self.sent.append((chat_id, text))
        return {}


def _message(chat_id, text, *, chat_type="private", username="tgname"):
    return {
        "update_id": 1,
        "message": {
            "chat": {"id": chat_id, "type": chat_type},
            "from": {"id": chat_id, "username": username},
            "text": text,
        },
    }


async def _insert_bot(session, workspace, username):
    from app.agents.services.crypto import encrypt

    session.add(
        WorkspaceIntegration(
            workspace_id=workspace.id,
            provider="telegram",
            client_id=username,
            client_secret_encrypted=encrypt(TOKEN) or "",
        )
    )
    await session.commit()


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------
def _patch_http(response=None, error=None):
    class FakeHttp:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None):
            if error:
                raise error
            return response

    return patch("app.telegram.client.httpx.AsyncClient", return_value=FakeHttp())


def _response(status, body):
    return httpx.Response(status, json=body, request=httpx.Request("POST", "https://x"))


@pytest.mark.asyncio
async def test_client_returns_the_result_of_a_successful_call():
    with _patch_http(_response(200, {"ok": True, "result": {"username": "b"}})):
        assert await TelegramClient(TOKEN).get_me() == {"username": "b"}


@pytest.mark.asyncio
async def test_client_raises_with_telegrams_description_and_code():
    body = {"ok": False, "error_code": 401, "description": "Unauthorized"}
    with _patch_http(_response(401, body)):
        with pytest.raises(TelegramError) as caught:
            await TelegramClient(TOKEN).get_me()
    assert caught.value.code == 401
    assert caught.value.is_auth_error
    assert "Unauthorized" in str(caught.value)


@pytest.mark.asyncio
async def test_a_network_failure_never_carries_the_token():
    # httpx puts the request URL, which contains the token, in some messages.
    boom = httpx.ConnectError(f"cannot connect to https://api.telegram.org/bot{TOKEN}/getMe")
    with _patch_http(error=boom):
        with pytest.raises(TelegramError) as caught:
            await TelegramClient(TOKEN).get_me()
    assert TOKEN not in str(caught.value)
    assert TOKEN not in repr(caught.value)
    assert caught.value.code is None
    assert not caught.value.is_auth_error


@pytest.mark.asyncio
async def test_a_non_json_answer_is_a_telegram_error():
    with _patch_http(httpx.Response(502, text="<html>", request=httpx.Request("POST", "https://x"))):
        with pytest.raises(TelegramError) as caught:
            await TelegramClient(TOKEN).get_me()
    assert caught.value.code == 502


def test_httpx_request_logging_is_silenced_so_the_token_stays_out_of_logs():
    assert logging.getLogger("httpx").level >= logging.WARNING


@pytest.mark.asyncio
async def test_removing_the_keyboard_sends_an_empty_one():
    sent = {}

    async def call(self, method, payload=None, **kw):
        sent.update(payload or {})
        return {}

    with patch.object(TelegramClient, "_call", call):
        await TelegramClient(TOKEN).edit_message_text(1, 2, "done")
    assert sent["reply_markup"] == {"inline_keyboard": []}


# ---------------------------------------------------------------------------
# Token storage
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_saving_a_token_stores_the_username_and_encrypts_the_token(
    session: AsyncSession, test_workspace
):
    with patch.object(TelegramClient, "get_me", AsyncMock(return_value={"username": "my_bot"})):
        status = await integration_service.save_telegram_token(session, test_workspace.id, TOKEN)

    assert status.configured and status.client_id == "my_bot"
    row = (await session.execute(select(WorkspaceIntegration))).scalar_one()
    assert TOKEN not in row.client_secret_encrypted
    assert await integration_service.get_telegram_bot(session, test_workspace.id) == ("my_bot", TOKEN)


@pytest.mark.asyncio
async def test_a_rejected_token_is_not_saved(session: AsyncSession, test_workspace):
    with patch.object(
        TelegramClient, "get_me", AsyncMock(side_effect=TelegramError("Unauthorized", code=401))
    ):
        with pytest.raises(integration_service.InvalidCredentialsError):
            await integration_service.save_telegram_token(session, test_workspace.id, TOKEN)
    assert await integration_service.get_telegram_bot(session, test_workspace.id) is None


@pytest.mark.asyncio
async def test_an_unreachable_telegram_is_reported_as_such(session: AsyncSession, test_workspace):
    with patch.object(TelegramClient, "get_me", AsyncMock(side_effect=TelegramError("timeout"))):
        with pytest.raises(integration_service.ProviderUnreachableError):
            await integration_service.save_telegram_token(session, test_workspace.id, TOKEN)


@pytest.mark.asyncio
async def test_a_token_that_is_not_a_bot_is_refused(session: AsyncSession, test_workspace):
    with patch.object(TelegramClient, "get_me", AsyncMock(return_value={})):
        with pytest.raises(integration_service.InvalidCredentialsError):
            await integration_service.save_telegram_token(session, test_workspace.id, TOKEN)


# ---------------------------------------------------------------------------
# Linking
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_link_code_is_single_use_and_expires(fake_redis, test_workspace, test_user):
    code = await telegram_service.create_link_code(test_workspace.id, test_user.id)

    assert fake_redis.ttl[f"telegram:link:{code}"] == telegram_service.LINK_TTL_SECONDS
    assert await telegram_service.consume_link_code(code) == (test_workspace.id, test_user.id)
    assert await telegram_service.consume_link_code(code) is None


@pytest.mark.asyncio
async def test_unknown_or_corrupt_codes_are_refused(fake_redis):
    assert await telegram_service.consume_link_code("nope") is None
    fake_redis.data["telegram:link:bad"] = "not json"
    assert await telegram_service.consume_link_code("bad") is None


@pytest.mark.asyncio
async def test_linking_again_moves_the_user_to_the_new_chat(
    session: AsyncSession, test_workspace, test_user
):
    await telegram_service.link_chat(session, test_workspace.id, test_user.id, 111, "a")
    await telegram_service.link_chat(session, test_workspace.id, test_user.id, 222, "b")

    links = (await session.execute(select(TelegramLink))).scalars().all()
    assert [(link.chat_id, link.telegram_username) for link in links] == [(222, "b")]


@pytest.mark.asyncio
async def test_a_chat_belongs_to_one_user_per_workspace(
    session: AsyncSession, test_workspace, test_user, viewer_auth_headers
):
    other = (
        await session.execute(select(User).where(User.email == "viewer-role@example.com"))
    ).scalar_one()
    await telegram_service.link_chat(session, test_workspace.id, test_user.id, 111, "a")
    await telegram_service.link_chat(session, test_workspace.id, other.id, 111, "b")

    links = (await session.execute(select(TelegramLink))).scalars().all()
    assert [link.user_id for link in links] == [other.id]


@pytest.mark.asyncio
async def test_only_roles_that_can_write_may_link(
    session: AsyncSession, test_workspace, test_user, viewer_auth_headers
):
    viewer = (
        await session.execute(select(User).where(User.email == "viewer-role@example.com"))
    ).scalar_one()

    assert await telegram_service.can_write(session, test_workspace.id, test_user.id)
    assert not await telegram_service.can_write(session, test_workspace.id, viewer.id)
    assert not await telegram_service.can_write(session, test_workspace.id, uuid.uuid4())


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_start_with_a_valid_code_links_the_chat_and_says_so(
    session: AsyncSession, fake_redis, test_workspace, test_user
):
    code = await telegram_service.create_link_code(test_workspace.id, test_user.id)
    bot = FakeBot()

    await handlers.handle_update(TestSessionLocal, test_workspace.id, bot, _message(555, f"/start {code}"))

    link = (await session.execute(select(TelegramLink))).scalar_one()
    assert (link.chat_id, link.user_id, link.telegram_username) == (555, test_user.id, "tgname")
    assert len(bot.sent) == 1 and bot.sent[0][0] == 555
    assert test_workspace.name in bot.sent[0][1]
    # Portuguese, because the test user's language preference is pt-BR.
    assert "Conectado" in bot.sent[0][1]


@pytest.mark.asyncio
async def test_a_used_code_cannot_link_a_second_chat(
    session: AsyncSession, fake_redis, test_workspace, test_user
):
    code = await telegram_service.create_link_code(test_workspace.id, test_user.id)
    bot = FakeBot()

    await handlers.handle_update(TestSessionLocal, test_workspace.id, bot, _message(555, f"/start {code}"))
    await handlers.handle_update(TestSessionLocal, test_workspace.id, bot, _message(666, f"/start {code}"))

    chats = [link.chat_id for link in (await session.execute(select(TelegramLink))).scalars()]
    assert chats == [555]
    assert "invalid" in bot.sent[-1][1]


@pytest.mark.asyncio
async def test_a_code_from_another_workspace_does_not_link_here(
    session: AsyncSession, fake_redis, test_workspace, test_user
):
    other = Workspace(id=uuid.uuid4(), name="Other", created_by_user_id=test_user.id)
    session.add(other)
    await session.commit()
    code = await telegram_service.create_link_code(other.id, test_user.id)
    bot = FakeBot()

    await handlers.handle_update(TestSessionLocal, test_workspace.id, bot, _message(555, f"/start {code}"))

    assert (await session.execute(select(TelegramLink))).scalars().all() == []


@pytest.mark.asyncio
async def test_someone_demoted_since_asking_for_the_code_cannot_link(
    session: AsyncSession, fake_redis, test_workspace, viewer_auth_headers
):
    viewer = (
        await session.execute(select(User).where(User.email == "viewer-role@example.com"))
    ).scalar_one()
    code = await telegram_service.create_link_code(test_workspace.id, viewer.id)
    bot = FakeBot()

    await handlers.handle_update(TestSessionLocal, test_workspace.id, bot, _message(555, f"/start {code}"))

    assert (await session.execute(select(TelegramLink))).scalars().all() == []
    assert "permission" in bot.sent[0][1]


@pytest.mark.asyncio
async def test_strangers_get_no_reply(session: AsyncSession, fake_redis, test_workspace):
    bot = FakeBot()

    await handlers.handle_update(TestSessionLocal, test_workspace.id, bot, _message(999, "hello"))
    await handlers.handle_update(TestSessionLocal, test_workspace.id, bot, _message(999, "/start"))

    assert bot.sent == []


@pytest.mark.asyncio
async def test_a_linked_chat_gets_help(
    session: AsyncSession, fake_redis, test_workspace, test_user
):
    await telegram_service.link_chat(session, test_workspace.id, test_user.id, 555, None)
    bot = FakeBot()

    await handlers.handle_update(TestSessionLocal, test_workspace.id, bot, _message(555, "oi"))

    assert len(bot.sent) == 1 and "resumo diário" in bot.sent[0][1]


@pytest.mark.asyncio
async def test_group_chats_and_non_message_updates_are_ignored(
    session: AsyncSession, fake_redis, test_workspace, test_user
):
    code = await telegram_service.create_link_code(test_workspace.id, test_user.id)
    bot = FakeBot()

    await handlers.handle_update(
        TestSessionLocal, test_workspace.id, bot, _message(-100, f"/start {code}", chat_type="group")
    )
    await handlers.handle_update(TestSessionLocal, test_workspace.id, bot, {"update_id": 2})

    assert bot.sent == []
    assert (await session.execute(select(TelegramLink))).scalars().all() == []


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_the_runner_finds_every_configured_bot_and_decrypts_its_token(
    session: AsyncSession, test_workspace
):
    await _insert_bot(session, test_workspace, "securo_bot")
    session.add(
        WorkspaceIntegration(
            workspace_id=test_workspace.id,
            provider="pluggy",
            client_id="p",
            client_secret_encrypted="x",
        )
    )
    await session.commit()

    assert await runner._configured_bots(TestSessionLocal) == {test_workspace.id: TOKEN}


@pytest.mark.asyncio
async def test_polling_handles_updates_remembers_the_offset_and_stops_when_the_token_is_revoked(
    fake_redis, test_workspace
):
    handled: list[dict] = []
    calls: list[Optional[int]] = []

    class ScriptedClient:
        def __init__(self, token):
            self.script = [
                [{"update_id": 10}, {"update_id": 11}],
                TelegramError("Unauthorized", code=401),
            ]

        async def get_updates(self, offset=None, **kw):
            calls.append(offset)
            step = self.script.pop(0)
            if isinstance(step, Exception):
                raise step
            return step

    async def record(session_maker, workspace_id, client, update):
        handled.append(update)

    with patch.object(runner, "TelegramClient", ScriptedClient), patch.object(
        runner, "handle_update", record
    ):
        await runner.poll_bot(TestSessionLocal, test_workspace.id, TOKEN)

    assert [u["update_id"] for u in handled] == [10, 11]
    assert calls == [None, 12]
    assert fake_redis.data[f"telegram:offset:{test_workspace.id}"] == "12"


@pytest.mark.asyncio
async def test_one_failing_update_does_not_stop_the_others(fake_redis, test_workspace):
    seen: list[int] = []

    class ScriptedClient:
        def __init__(self, token):
            self.script = [
                [{"update_id": 1}, {"update_id": 2}],
                TelegramError("Unauthorized", code=401),
            ]

        async def get_updates(self, offset=None, **kw):
            step = self.script.pop(0)
            if isinstance(step, Exception):
                raise step
            return step

    async def flaky(session_maker, workspace_id, client, update):
        seen.append(update["update_id"])
        if update["update_id"] == 1:
            raise RuntimeError("boom")

    with patch.object(runner, "TelegramClient", ScriptedClient), patch.object(
        runner, "handle_update", flaky
    ):
        await runner.poll_bot(TestSessionLocal, test_workspace.id, TOKEN)

    assert seen == [1, 2]


@pytest.mark.asyncio
async def test_polling_backs_off_on_transient_errors_and_keeps_going(fake_redis, test_workspace):
    class ScriptedClient:
        def __init__(self, token):
            self.script = [
                TelegramError("timeout"),
                [],
                TelegramError("Unauthorized", code=401),
            ]

        async def get_updates(self, offset=None, **kw):
            step = self.script.pop(0)
            if isinstance(step, Exception):
                raise step
            return step

    sleeps: list[float] = []

    async def no_sleep(seconds):
        sleeps.append(seconds)

    with patch.object(runner, "TelegramClient", ScriptedClient), patch.object(
        runner.asyncio, "sleep", no_sleep
    ):
        await runner.poll_bot(TestSessionLocal, test_workspace.id, TOKEN)

    assert sleeps == [1]


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
def _url(workspace_id, suffix=""):
    return f"/api/workspaces/{workspace_id}/integrations/telegram{suffix}"


@pytest.mark.asyncio
async def test_owner_saves_a_token_and_it_never_comes_back(
    client: AsyncClient, auth_headers, test_workspace
):
    with patch.object(TelegramClient, "get_me", AsyncMock(return_value={"username": "my_bot"})):
        resp = await client.put(_url(test_workspace.id), headers=auth_headers, json={"token": TOKEN})

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"configured": True, "client_id": "my_bot"}
    assert TOKEN not in resp.text
    status = await client.get(_url(test_workspace.id), headers=auth_headers)
    assert status.json() == {"configured": True, "client_id": "my_bot"}
    assert TOKEN not in status.text


@pytest.mark.asyncio
async def test_a_bad_token_is_a_400_and_an_unreachable_telegram_a_502(
    client: AsyncClient, auth_headers, test_workspace
):
    with patch.object(
        TelegramClient, "get_me", AsyncMock(side_effect=TelegramError("Unauthorized", code=401))
    ):
        bad = await client.put(_url(test_workspace.id), headers=auth_headers, json={"token": "x"})
    with patch.object(TelegramClient, "get_me", AsyncMock(side_effect=TelegramError("timeout"))):
        down = await client.put(_url(test_workspace.id), headers=auth_headers, json={"token": "x"})

    assert bad.status_code == 400
    assert down.status_code == 502
    assert (await client.get(_url(test_workspace.id), headers=auth_headers)).json()["configured"] is False


@pytest.mark.asyncio
async def test_viewers_cannot_manage_the_bot_but_can_see_its_name(
    client: AsyncClient, auth_headers, viewer_auth_headers, test_workspace, session: AsyncSession
):
    await _insert_bot(session, test_workspace, "my_bot")

    put = await client.put(_url(test_workspace.id), headers=viewer_auth_headers, json={"token": "x"})
    delete = await client.delete(_url(test_workspace.id), headers=viewer_auth_headers)
    seen = await client.get(_url(test_workspace.id), headers=viewer_auth_headers)

    assert put.status_code == 403 and delete.status_code == 403
    assert seen.json() == {"configured": True, "client_id": "my_bot"}


@pytest.mark.asyncio
async def test_the_link_needs_a_bot_and_points_at_it(
    client: AsyncClient, auth_headers, test_workspace, session: AsyncSession, fake_redis
):
    without = await client.post(_url(test_workspace.id, "/link"), headers=auth_headers)
    assert without.status_code == 400

    await _insert_bot(session, test_workspace, "my_bot")
    resp = await client.post(_url(test_workspace.id, "/link"), headers=auth_headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["url"].startswith("https://t.me/my_bot?start=")
    code = body["url"].split("start=")[1]
    assert f"telegram:link:{code}" in fake_redis.data
    assert datetime.fromisoformat(body["expires_at"]) > datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_viewers_cannot_ask_for_a_link(
    client: AsyncClient, viewer_auth_headers, test_workspace, session: AsyncSession, fake_redis
):
    await _insert_bot(session, test_workspace, "my_bot")

    resp = await client.post(_url(test_workspace.id, "/link"), headers=viewer_auth_headers)

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_owners_see_every_chat_and_members_only_their_own(
    client: AsyncClient,
    auth_headers,
    viewer_auth_headers,
    test_workspace,
    test_user,
    session: AsyncSession,
):
    viewer = (
        await session.execute(select(User).where(User.email == "viewer-role@example.com"))
    ).scalar_one()
    await telegram_service.link_chat(session, test_workspace.id, test_user.id, 1, "owner_tg")
    await telegram_service.link_chat(session, test_workspace.id, viewer.id, 2, "viewer_tg")

    owner_view = (await client.get(_url(test_workspace.id, "/chats"), headers=auth_headers)).json()
    viewer_view = (
        await client.get(_url(test_workspace.id, "/chats"), headers=viewer_auth_headers)
    ).json()

    assert {c["telegram_username"] for c in owner_view} == {"owner_tg", "viewer_tg"}
    assert {c["email"] for c in owner_view} == {"test@example.com", "viewer-role@example.com"}
    assert [c["telegram_username"] for c in viewer_view] == ["viewer_tg"]
    assert viewer_view[0]["email"] is None and viewer_view[0]["is_mine"] is True


@pytest.mark.asyncio
async def test_members_can_disconnect_only_their_own_chat(
    client: AsyncClient,
    auth_headers,
    viewer_auth_headers,
    test_workspace,
    test_user,
    session: AsyncSession,
):
    viewer = (
        await session.execute(select(User).where(User.email == "viewer-role@example.com"))
    ).scalar_one()
    owner_link = await telegram_service.link_chat(session, test_workspace.id, test_user.id, 1, "o")
    viewer_link = await telegram_service.link_chat(session, test_workspace.id, viewer.id, 2, "v")

    forbidden = await client.delete(
        _url(test_workspace.id, f"/chats/{owner_link.id}"), headers=viewer_auth_headers
    )
    own = await client.delete(
        _url(test_workspace.id, f"/chats/{viewer_link.id}"), headers=viewer_auth_headers
    )
    by_owner = await client.delete(
        _url(test_workspace.id, f"/chats/{owner_link.id}"), headers=auth_headers
    )
    missing = await client.delete(
        _url(test_workspace.id, f"/chats/{uuid.uuid4()}"), headers=auth_headers
    )

    assert (forbidden.status_code, own.status_code, by_owner.status_code, missing.status_code) == (
        403,
        204,
        204,
        404,
    )


@pytest.mark.asyncio
async def test_removing_the_bot_also_disconnects_its_chats(
    client: AsyncClient, auth_headers, test_workspace, test_user, session: AsyncSession
):
    await _insert_bot(session, test_workspace, "my_bot")
    await telegram_service.link_chat(session, test_workspace.id, test_user.id, 1, "o")

    resp = await client.delete(_url(test_workspace.id), headers=auth_headers)

    assert resp.status_code == 204
    assert (await session.execute(select(TelegramLink))).scalars().all() == []
    assert (await client.get(_url(test_workspace.id), headers=auth_headers)).json()["configured"] is False
