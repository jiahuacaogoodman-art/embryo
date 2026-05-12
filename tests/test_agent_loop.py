"""Agent Loop 单元测试"""

import pytest

from embryo.runtime.agent_loop import AgentLoop


class TestLoopDetection:
    def test_no_loop_different_calls(self):
        loop = AgentLoop.__new__(AgentLoop)
        history = []
        tc1 = [{"name": "terminal", "arguments": {"command": "ls"}}]
        tc2 = [{"name": "terminal", "arguments": {"command": "pwd"}}]
        tc3 = [{"name": "read_file", "arguments": {"path": "x.py"}}]

        assert not loop._detect_loop(tc1, history)
        assert not loop._detect_loop(tc2, history)
        assert not loop._detect_loop(tc3, history)

    def test_loop_detected_3_same(self):
        loop = AgentLoop.__new__(AgentLoop)
        history = []
        tc = [{"name": "terminal", "arguments": {"command": "ls"}}]

        assert not loop._detect_loop(tc, history)
        assert not loop._detect_loop(tc, history)
        assert loop._detect_loop(tc, history)  # 3rd = loop

    def test_loop_detected_4_of_5(self):
        loop = AgentLoop.__new__(AgentLoop)
        history = []
        tc_a = [{"name": "a", "arguments": {}}]
        tc_b = [{"name": "b", "arguments": {}}]

        loop._detect_loop(tc_a, history)
        loop._detect_loop(tc_b, history)
        loop._detect_loop(tc_a, history)
        loop._detect_loop(tc_a, history)
        assert loop._detect_loop(tc_a, history)
