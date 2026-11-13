from types import SimpleNamespace

from flowsint_core.core.llm import MessageRole
from flowsint_core.core.services.chat_service import ChatService


def _msg(content, is_bot):
    return SimpleNamespace(content=content, is_bot=is_bot)


def _build(recent, prompt, context=None):
    service = ChatService.__new__(ChatService)
    return service.build_llm_messages(
        {"recent_messages": recent, "context_message": context, "user_prompt": prompt}
    )


def _roles(messages):
    return [m.role for m in messages]


def test_current_prompt_already_in_history_is_sent_once():
    messages = _build(
        [_msg("привет", False), _msg("ответ", True), _msg("найди python.org", False)],
        "найди python.org",
    )
    assert _roles(messages) == [
        MessageRole.SYSTEM,
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.USER,
    ]
    assert messages[-1].content == "найди python.org"


def test_history_keeps_cyrillic_unescaped():
    messages = _build([_msg("вопрос", False), _msg("ответ", True)], "ещё")
    assert messages[1].content == "вопрос"
    assert messages[2].content == "ответ"


def test_context_goes_into_single_leading_system_message():
    messages = _build([_msg("a", False), _msg("b", True)], "c", context="Context: x")
    assert _roles(messages).count(MessageRole.SYSTEM) == 1
    assert messages[0].role == MessageRole.SYSTEM
    assert messages[0].content.endswith("Context: x")


def test_turns_alternate_and_start_with_user():
    # Window starts on an assistant turn and contains a user message whose
    # reply was lost, followed by the current prompt.
    messages = _build(
        [_msg("old answer", True), _msg("lost question", False), _msg("now", False)],
        "now",
    )
    roles = _roles(messages)
    assert roles == [MessageRole.SYSTEM, MessageRole.USER]
    assert messages[1].content == "lost question\n\nnow"
