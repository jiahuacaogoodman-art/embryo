"""Memory Store 单元测试"""

import tempfile
from pathlib import Path

import pytest

from embryo.memory.store import MemoryStore, MemoryEntry, _tokenize


@pytest.fixture
def store():
    tmp = Path(tempfile.mkdtemp())
    return MemoryStore(storage_path=tmp, max_entries=50)


class TestTokenize:
    def test_english(self):
        tokens = _tokenize("hello world test")
        assert "hello" in tokens
        assert "world" in tokens

    def test_chinese(self):
        tokens = _tokenize("登录系统")
        assert "登" in tokens
        assert "录" in tokens
        assert "登录" in tokens
        assert "系统" in tokens

    def test_mixed(self):
        tokens = _tokenize("Python 代码风格")
        assert "python" in tokens
        assert "代码" in tokens


class TestMemoryStore:
    def test_store_and_recall(self, store):
        store.store("fact", "用户名是 admin")
        store.store("lesson", "登录前需要等待页面加载")

        results = store.recall_relevant("登录")
        assert len(results) >= 1
        assert "登录" in results[0]

    def test_deduplication(self, store):
        store.store("fact", "系统运行在 Ubuntu 22.04 上")
        store.store("fact", "系统运行在 Ubuntu 22.04 上")  # duplicate
        assert store.count == 1

    def test_persistence(self):
        tmp = Path(tempfile.mkdtemp())
        s1 = MemoryStore(storage_path=tmp, max_entries=50)
        s1.store("fact", "test persistence")

        s2 = MemoryStore(storage_path=tmp, max_entries=50)
        assert s2.count == 1
        assert "persistence" in s2.recall_all()[0].content

    def test_forget(self, store):
        entry = store.store("fact", "to be deleted")
        assert store.count == 1
        store.forget(entry.id)
        assert store.count == 0

    def test_eviction(self):
        tmp = Path(tempfile.mkdtemp())
        store = MemoryStore(storage_path=tmp, max_entries=5)
        for i in range(10):
            store.store("fact", f"memory entry number {i}")
        assert store.count <= 5

    def test_category_recall(self, store):
        store.store("preference", "用户喜欢暗色主题")
        store.store("lesson", "不要用 sudo")
        store.store("environment", "macOS 14")

        prefs = store.recall_by_category("preference")
        assert len(prefs) == 1
        assert "暗色" in prefs[0].content
