"""安全策略引擎单元测试"""

import pytest

from embryo.security.policy import PolicyEngine, PolicyDecision


@pytest.fixture
def engine():
    return PolicyEngine()


class TestPolicyEngine:
    def test_safe_tools_allowed(self, engine):
        r = engine.check("read_file", {"path": "/tmp/test.txt"})
        assert r.decision == PolicyDecision.ALLOW

    def test_dangerous_command_denied(self, engine):
        r = engine.check("terminal", {"command": "rm -rf /"})
        assert r.decision == PolicyDecision.DENY

    def test_sudo_needs_confirmation(self, engine):
        r = engine.check("terminal", {"command": "sudo apt update"})
        assert r.decision == PolicyDecision.ASK

    def test_normal_command_allowed(self, engine):
        r = engine.check("terminal", {"command": "ls -la"})
        assert r.decision == PolicyDecision.ALLOW

    def test_system_write_needs_confirmation(self, engine):
        r = engine.check("write_file", {"path": "/etc/hosts", "content": "hack"})
        assert r.decision == PolicyDecision.ASK

    def test_normal_write_allowed(self, engine):
        r = engine.check("write_file", {"path": "/tmp/safe.txt", "content": "ok"})
        assert r.decision == PolicyDecision.ALLOW

    def test_git_force_push_needs_confirmation(self, engine):
        r = engine.check("terminal", {"command": "git push --force origin main"})
        assert r.decision == PolicyDecision.ASK

    def test_fork_bomb_denied(self, engine):
        r = engine.check("terminal", {"command": ":(){ :|:& };:"})
        assert r.decision == PolicyDecision.DENY

    def test_gui_operations_warned(self, engine):
        r = engine.check("click", {"x": 100, "y": 200})
        assert r.decision == PolicyDecision.WARN

    def test_custom_allowed_paths(self):
        engine = PolicyEngine(allowed_paths=["/home/user", "/tmp"])
        r = engine.check("write_file", {"path": "/home/user/file.txt", "content": "ok"})
        assert r.decision == PolicyDecision.ALLOW

        r = engine.check("write_file", {"path": "/var/log/test.txt", "content": "hack"})
        assert r.decision == PolicyDecision.DENY
