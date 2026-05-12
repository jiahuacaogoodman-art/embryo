"""Tools 系统单元测试"""

import pytest

from embryo.tools.registry import ToolRegistry, Tool
from embryo.tools.terminal import TERMINAL_TOOL, execute_command
from embryo.tools.file_ops import read_file, write_file, edit_file, list_directory


class TestToolRegistry:
    def test_register_and_execute(self):
        reg = ToolRegistry()
        tool = Tool(
            name="echo",
            description="Echo input",
            parameters={"type": "object", "properties": {"text": {"type": "string"}}},
            handler=lambda text="": f"echo: {text}",
        )
        reg.register(tool)
        assert reg.count == 1
        result = reg.execute("echo", {"text": "hello"})
        assert result == "echo: hello"

    def test_unknown_tool(self):
        reg = ToolRegistry()
        with pytest.raises(KeyError):
            reg.execute("nonexistent", {})

    def test_schema_export(self):
        reg = ToolRegistry()
        reg.register(TERMINAL_TOOL)
        schema = reg.get_openai_tools_schema()
        assert len(schema) == 1
        assert schema[0]["function"]["name"] == "terminal"

    def test_list_tools(self):
        reg = ToolRegistry()
        reg.register(TERMINAL_TOOL)
        assert "terminal" in reg.list_tools()


class TestTerminalTool:
    def test_echo(self):
        result = execute_command("echo hello")
        assert "hello" in result

    def test_error_command(self):
        result = execute_command("false")
        assert "exit code" in result or result == "(no output)"

    def test_timeout(self):
        result = execute_command("sleep 10", timeout=1)
        assert "超时" in result or "Timeout" in result


class TestFileTools:
    def test_read_nonexistent(self):
        result = read_file("/nonexistent/file.txt")
        assert "Error" in result

    def test_write_and_read(self, tmp_path):
        path = str(tmp_path / "test.txt")
        write_file(path, "hello world")
        result = read_file(path)
        assert "hello world" in result

    def test_edit(self, tmp_path):
        path = str(tmp_path / "edit.txt")
        write_file(path, "old text here")
        result = edit_file(path, "old text", "new text")
        assert "已编辑" in result
        content = read_file(path)
        assert "new text" in content

    def test_list_directory(self, tmp_path):
        (tmp_path / "file.txt").write_text("content")
        result = list_directory(str(tmp_path))
        assert "file.txt" in result
