"""Delivery rules of the shared run pipeline (``sernia_ai/runs.py``).

Every way a run reaches the agent — web chat, approval resume, 1:1 SMS, group
SMS, background trigger — finishes through ``finalize_run``. These tests pin
the rules once, instead of once per path.
"""

import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic_ai import DeferredToolRequests
from pydantic_ai.messages import ModelRequest, ToolCallPart, UserPromptPart

from api.src.sernia_ai import routes, runs
from api.src.sernia_ai.deps import SerniaDeps
from api.src.sernia_ai.general_agent import NoAction
from api.src.sernia_ai.triggers import ai_sms_event_trigger, background_agent_runner

PHONE = "+14125550101"
OTHER = "+14125550102"


def make_deps(recipients: list[str] | None = None) -> SerniaDeps:
    return SerniaDeps(
        db_session=AsyncMock(),
        conversation_id="conv-1",
        user_identifier="test",
        user_name="Test",
        user_email="test@serniacapital.com",
        modality="sms" if recipients else "web_chat",
        workspace_path=Path("/tmp"),
        sms_reply_recipients=recipients or [],
    )


def text_result(text: str = "All set.") -> SimpleNamespace:
    return SimpleNamespace(output=text)


def pending_result() -> SimpleNamespace:
    call = ToolCallPart(tool_name="send_email", args={"to": ["x@example.com"]}, tool_call_id="c1")
    return SimpleNamespace(output=DeferredToolRequests(approvals=[call]))


@pytest.fixture
def pipeline(monkeypatch):
    """The pipeline with every side effect replaced by a recorder."""
    tasks: list[str] = []

    def record_task(coro, *, name=None):
        tasks.append(name)
        coro.close()

    stubs = SimpleNamespace(
        tasks=tasks,
        persist=AsyncMock(return_value=True),
        sms=AsyncMock(),
        save=AsyncMock(),
        agent=MagicMock(run=AsyncMock(return_value=text_result())),
    )
    monkeypatch.setattr(runs, "create_logged_task", record_task)
    monkeypatch.setattr(runs, "persist_agent_run_result", stubs.persist)
    monkeypatch.setattr(runs, "send_sms_reply", stubs.sms)
    monkeypatch.setattr(runs, "save_agent_conversation", stubs.save)
    monkeypatch.setattr(runs, "sernia_agent", stubs.agent)
    monkeypatch.setattr(runs, "resolve_active_run_kwargs", AsyncMock(return_value={}))
    return stubs


class TestTextOutcome:
    @pytest.mark.asyncio
    async def test_sms_run_replies_to_the_sender(self, pipeline):
        outcome = await runs.finalize_run(
            text_result("On it."), deps=make_deps([PHONE]), clerk_user_id="bot"
        )
        pipeline.sms.assert_awaited_once_with(PHONE, "On it.")
        assert outcome.reply_text == "On it."
        assert outcome.needs_attention

    @pytest.mark.asyncio
    async def test_group_run_replies_to_the_whole_group(self, pipeline):
        await runs.finalize_run(text_result(), deps=make_deps([PHONE, OTHER]), clerk_user_id="bot")
        pipeline.sms.assert_awaited_once_with([PHONE, OTHER], "All set.")

    @pytest.mark.asyncio
    async def test_web_run_sends_no_sms_and_pushes_only_the_asker(self, pipeline):
        await runs.finalize_run(
            text_result(), deps=make_deps(), clerk_user_id="user_1", notify_user_id="user_1"
        )
        pipeline.sms.assert_not_called()
        assert pipeline.tasks == ["git_sync", "notify_chat_response"]

    @pytest.mark.asyncio
    async def test_trigger_run_delivers_nothing_but_the_saved_conversation(self, pipeline):
        """No recipients, no asker: a human finds it in the web chat list."""
        await runs.finalize_run(text_result(), deps=make_deps(), clerk_user_id="bot")
        pipeline.sms.assert_not_called()
        assert pipeline.tasks == ["git_sync"]

    @pytest.mark.asyncio
    async def test_failed_save_still_texts_the_reply_but_sends_no_push(self, pipeline):
        """Pushes deep-link to the saved conversation; a text does not."""
        pipeline.persist.return_value = False
        outcome = await runs.finalize_run(
            text_result(), deps=make_deps([PHONE]), clerk_user_id="u", notify_user_id="u"
        )
        pipeline.sms.assert_awaited_once()
        assert pipeline.tasks == ["git_sync"]
        assert outcome.persisted is False

    @pytest.mark.asyncio
    async def test_blank_text_is_not_delivered(self, pipeline):
        outcome = await runs.finalize_run(
            text_result("  \n"), deps=make_deps([PHONE]), clerk_user_id="u", notify_user_id="u"
        )
        pipeline.sms.assert_not_called()
        assert pipeline.tasks == ["git_sync"]
        assert not outcome.needs_attention


class TestPausedForApproval:
    @pytest.mark.asyncio
    async def test_pushes_the_approval_and_sends_no_reply(self, pipeline):
        outcome = await runs.finalize_run(
            pending_result(), deps=make_deps([PHONE]), clerk_user_id="u", notify_user_id="u"
        )
        pipeline.sms.assert_not_called()
        assert pipeline.tasks == ["git_sync", "notify_pending_approval"]
        assert [p["tool_name"] for p in outcome.pending] == ["send_email"]
        assert outcome.reply_text is None
        assert outcome.needs_attention

    @pytest.mark.asyncio
    async def test_no_approval_push_when_the_save_failed(self, pipeline):
        pipeline.persist.return_value = False
        outcome = await runs.finalize_run(pending_result(), deps=make_deps(), clerk_user_id="u")
        assert pipeline.tasks == ["git_sync"]
        assert outcome.pending  # still reported to the caller


class TestNoAction:
    @pytest.mark.asyncio
    async def test_saves_the_run_and_delivers_nothing(self, pipeline):
        result = SimpleNamespace(output=NoAction(reason="routine ack"))
        outcome = await runs.finalize_run(
            result, deps=make_deps([PHONE]), clerk_user_id="u", notify_user_id="u"
        )
        pipeline.persist.assert_awaited_once()
        pipeline.sms.assert_not_called()
        assert pipeline.tasks == ["git_sync"]
        assert outcome.no_action_reason == "routine ack"
        assert not outcome.needs_attention


class TestSave:
    @pytest.mark.asyncio
    async def test_conversation_fields_reach_the_save(self, pipeline):
        await runs.finalize_run(
            text_result(),
            deps=make_deps([PHONE]),
            clerk_user_id="bot",
            metadata={"trigger_source": "ai_sms"},
            modality="sms",
            contact_identifier=PHONE,
        )
        saved = pipeline.persist.await_args.kwargs
        assert saved["conversation_id"] == "conv-1"
        assert saved["agent_name"] == "sernia"
        assert saved["clerk_user_id"] == "bot"
        assert saved["metadata"] == {"trigger_source": "ai_sms"}
        assert saved["modality"] == "sms"
        assert saved["contact_identifier"] == PHONE


class TestRunAndFinalize:
    @pytest.mark.asyncio
    async def test_runs_on_the_active_model_and_tags_the_source(self, pipeline, monkeypatch):
        monkeypatch.setattr(
            runs, "resolve_active_run_kwargs", AsyncMock(return_value={"model": "the-model"})
        )
        history = [ModelRequest(parts=[UserPromptPart(content="earlier")])]
        deps = make_deps([PHONE])
        outcome = await runs.run_and_finalize(
            "hi",
            deps=deps,
            trigger_source="ai_sms",
            clerk_user_id="bot",
            message_history=history,
        )
        kwargs = pipeline.agent.run.await_args.kwargs
        assert kwargs["model"] == "the-model"
        assert kwargs["metadata"] == {"trigger_source": "ai_sms"}
        assert kwargs["message_history"] is history
        assert kwargs["deps"] is deps
        assert outcome is not None and outcome.reply_text == "All set."

    @pytest.mark.asyncio
    async def test_callers_connection_is_released_before_the_model_runs(self, pipeline):
        """History is loaded on the caller's session; holding that connection
        through a minutes-long run, plus a second one for the save, drains
        the pool under concurrent runs."""
        deps = make_deps([PHONE])
        order: list[str] = []
        deps.db_session.rollback.side_effect = lambda: order.append("released")

        async def run(*args, **kwargs):
            order.append("model ran")
            return text_result()

        pipeline.agent.run.side_effect = run
        pipeline.persist.side_effect = lambda *a, **k: order.append("saved") or True
        await runs.run_and_finalize("hi", deps=deps, trigger_source="ai_sms", clerk_user_id="bot")
        assert order == ["released", "model ran", "saved"]

    @pytest.mark.asyncio
    async def test_a_failed_release_does_not_stop_the_run(self, pipeline):
        deps = make_deps([PHONE])
        deps.db_session.rollback.side_effect = RuntimeError("connection already gone")
        outcome = await runs.run_and_finalize(
            "hi", deps=deps, trigger_source="ai_sms", clerk_user_id="bot"
        )
        assert outcome is not None and outcome.reply_text == "All set."

    @pytest.mark.asyncio
    async def test_crashed_run_returns_none_and_delivers_nothing(self, pipeline):
        pipeline.agent.run.side_effect = RuntimeError("model down")
        outcome = await runs.run_and_finalize(
            "hi", deps=make_deps([PHONE]), trigger_source="ai_sms", clerk_user_id="bot"
        )
        assert outcome is None
        pipeline.persist.assert_not_called()
        pipeline.sms.assert_not_called()
        assert pipeline.tasks == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("keep_failed_run", [True, False])
    async def test_caller_chooses_whether_a_crashed_run_is_kept(
        self, pipeline, monkeypatch, keep_failed_run
    ):
        """SMS conversations opt out: their history is rebuilt from the live
        thread on every inbound text."""
        pipeline.agent.run.side_effect = RuntimeError("model down")
        save_failed = AsyncMock()
        monkeypatch.setattr(runs, "save_failed_run", save_failed)
        await runs.run_and_finalize(
            "hi",
            deps=make_deps(),
            trigger_source="zillow_email_event",
            clerk_user_id="bot",
            metadata={"trigger_source": "zillow_email_event"},
            keep_failed_run=keep_failed_run,
        )
        assert save_failed.await_count == (1 if keep_failed_run else 0)
        if keep_failed_run:
            kwargs = save_failed.await_args.kwargs
            assert kwargs["conversation_id"] == "conv-1"
            assert kwargs["metadata"] == {"trigger_source": "zillow_email_event"}


class TestSaveFailedRun:
    MESSAGES = [ModelRequest(parts=[UserPromptPart(content="hi")])]

    @pytest.mark.asyncio
    async def test_keeps_the_partial_conversation_flagged_with_its_metadata(self, pipeline):
        await runs.save_failed_run(
            self.MESSAGES,
            conversation_id="conv-1",
            clerk_user_id="bot",
            metadata={"trigger_source": "zillow_email_event"},
        )
        saved = pipeline.save.await_args.kwargs
        assert saved["messages"] == self.MESSAGES
        assert saved["metadata"] == {
            "trigger_source": "zillow_email_event",
            "partial": True,
            "error": True,
        }

    @pytest.mark.asyncio
    async def test_marks_provider_failures(self, pipeline):
        await runs.save_failed_run(
            self.MESSAGES, conversation_id="conv-1", clerk_user_id="u", llm_error=True
        )
        assert pipeline.save.await_args.kwargs["metadata"]["llm_error"] is True

    @pytest.mark.asyncio
    async def test_writes_nothing_when_the_run_produced_no_messages(self, pipeline):
        await runs.save_failed_run([], conversation_id="conv-1", clerk_user_id="u")
        await runs.save_failed_run(None, conversation_id="conv-1", clerk_user_id="u")
        pipeline.save.assert_not_called()

    @pytest.mark.asyncio
    async def test_never_raises(self, pipeline):
        pipeline.save.side_effect = RuntimeError("db down")
        await runs.save_failed_run(self.MESSAGES, conversation_id="conv-1", clerk_user_id="u")


class TestOnePipeline:
    """The post-run steps exist in ``runs.py`` and nowhere else.

    Each entry point used to carry its own copy of sync/save/notify/reply, and
    the copies drifted. If one of these names shows up in an entry-point
    module again, a second pipeline is being built.
    """

    PIPELINE_ONLY = (
        "commit_and_push",
        "persist_agent_run_result",
        "notify_pending_approval",
        "notify_chat_response",
        "extract_pending_approvals",
        "send_message",
    )

    @pytest.mark.parametrize("module", [routes, ai_sms_event_trigger, background_agent_runner])
    def test_entry_points_do_not_reimplement_it(self, module):
        source = inspect.getsource(module)
        leaked = [name for name in self.PIPELINE_ONLY if f"{name}(" in source]
        assert leaked == [], f"{module.__name__} calls pipeline steps directly: {leaked}"
