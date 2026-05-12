"""Skills Manager 单元测试"""

import tempfile
from pathlib import Path

import pytest

from embryo.skills.manager import SkillManager, SkillMeta, _estimate_tokens


@pytest.fixture
def skills_dir():
    return Path("/projects/sandbox/embryo/skills")


@pytest.fixture
def manager(skills_dir):
    return SkillManager(skills_dir=skills_dir, max_skill_tokens=2000)


class TestSkillManager:
    def test_index_skills(self, manager):
        skills = manager.list_skills()
        assert len(skills) >= 2
        names = [s.name for s in skills]
        assert "GUI 表单填写" in names or "form-filling" in names

    def test_get_relevant_skills(self, manager):
        results = manager.get_relevant_skills("登录系统")
        assert len(results) >= 1
        assert "登录" in results[0]

    def test_no_match(self, manager):
        results = manager.get_relevant_skills("量子物理学")
        assert len(results) == 0

    def test_load_skill_full(self, manager):
        content = manager.load_skill_full("GUI 系统登录")
        assert "截图" in content or "OCR" in content

    def test_load_nonexistent(self, manager):
        result = manager.load_skill_full("不存在的Skill")
        assert "Error" in result or "不存在" in result

    def test_skill_summaries(self, manager):
        summaries = manager.get_skill_summaries()
        assert "表单" in summaries or "登录" in summaries

    def test_create_skill(self):
        tmp = Path(tempfile.mkdtemp())
        mgr = SkillManager(skills_dir=tmp)
        mgr.create_skill("test-skill", "---\nname: Test\ndescription: A test\ntags: [test]\n---\n# Test\nHello")
        assert len(mgr.list_skills()) == 1


class TestTokenEstimate:
    def test_english(self):
        tokens = _estimate_tokens("hello world this is a test")
        assert tokens > 0

    def test_chinese(self):
        tokens = _estimate_tokens("你好世界这是测试")
        assert tokens > 0
        # 中文应该比等长英文更多 tokens
        en_tokens = _estimate_tokens("hello")
        zh_tokens = _estimate_tokens("你好")
        assert zh_tokens >= en_tokens
