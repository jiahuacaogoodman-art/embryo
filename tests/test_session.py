"""Session 持久化测试"""

import tempfile
from pathlib import Path

import pytest

from embryo.runtime.session import Session, SessionStatus, Message, ToolCall


class TestSession:
    def test_add_message(self):
        s = Session()
        s.add_message("user", "hello")
        s.add_message("assistant", "hi")
        assert len(s.messages) == 2
        assert s.messages[0].role == "user"

    def test_add_tool_call(self):
        s = Session()
        tc = ToolCall(name="terminal", arguments={"command": "ls"}, result="file.txt")
        s.add_tool_call(tc)
        assert s.total_steps == 1
        assert len(s.tool_calls) == 1

    def test_save_and_load(self):
        tmp = Path(tempfile.mkdtemp())
        s = Session()
        s.context["task"] = "test"
        s.add_message("user", "hello world")
        s.add_message("assistant", "hi there")
        tc = ToolCall(name="test", arguments={"a": 1}, result="ok", success=True)
        s.add_tool_call(tc)
        s.save(tmp)

        loaded = Session.load(tmp / f"{s.id}.json")
        assert loaded.id == s.id
        assert len(loaded.messages) == 2
        assert len(loaded.tool_calls) == 1
        assert loaded.context["task"] == "test"

    def test_list_sessions(self):
        tmp = Path(tempfile.mkdtemp())
        s1 = Session()
        s1.add_message("user", "first session")
        s1.save(tmp)

        s2 = Session()
        s2.add_message("user", "second session")
        s2.save(tmp)

        sessions = Session.list_sessions(tmp)
        assert len(sessions) == 2

    def test_conversation_for_llm(self):
        s = Session()
        s.add_message("system", "you are helpful")
        s.add_message("user", "hi")
        msgs = s.get_conversation_for_llm()
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"
