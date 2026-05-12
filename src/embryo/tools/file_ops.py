"""文件操作工具 - 读写和编辑文件

"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .registry import Tool


def read_file(path: str, offset: int = 0, limit: int = 500) -> str:
    """读取文件内容

    Args:
        path: 文件路径
        offset: 起始行号（0-indexed）
        limit: 最大读取行数

    Returns:
        文件内容（带行号）
    """
    file_path = Path(path).expanduser()
    if not file_path.exists():
        return f"[Error] 文件不存在: {path}"
    if not file_path.is_file():
        return f"[Error] 不是文件: {path}"

    try:
        lines = file_path.read_text(encoding="utf-8").splitlines()
        total = len(lines)
        selected = lines[offset: offset + limit]

        result = f"[{path}] (共 {total} 行，显示 {offset+1}-{offset+len(selected)})\n"
        for i, line in enumerate(selected, start=offset + 1):
            result += f"{i:4d} | {line}\n"
        return result.rstrip()
    except Exception as e:
        return f"[Error] 读取失败: {e}"


def write_file(path: str, content: str) -> str:
    """写入文件（创建或覆盖）

    Args:
        path: 文件路径
        content: 文件内容

    Returns:
        操作结果
    """
    file_path = Path(path).expanduser()
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        return f"已写入 {path} ({len(content)} 字节)"
    except Exception as e:
        return f"[Error] 写入失败: {e}"


def edit_file(path: str, old_text: str, new_text: str) -> str:
    """编辑文件（精确替换）

    Args:
        path: 文件路径
        old_text: 要替换的原文（必须精确匹配）
        new_text: 替换后的文本

    Returns:
        操作结果
    """
    file_path = Path(path).expanduser()
    if not file_path.exists():
        return f"[Error] 文件不存在: {path}"

    try:
        content = file_path.read_text(encoding="utf-8")
        if old_text not in content:
            return f"[Error] 未找到要替换的文本。请确认 old_text 精确匹配文件内容。"

        count = content.count(old_text)
        if count > 1:
            return f"[Error] old_text 在文件中出现 {count} 次，请提供更多上下文使其唯一。"

        new_content = content.replace(old_text, new_text, 1)
        file_path.write_text(new_content, encoding="utf-8")
        return f"已编辑 {path}（替换了 {len(old_text)} → {len(new_text)} 字符）"
    except Exception as e:
        return f"[Error] 编辑失败: {e}"


def list_directory(path: str = ".", depth: int = 1) -> str:
    """列出目录内容

    Args:
        path: 目录路径
        depth: 递归深度

    Returns:
        目录结构
    """
    dir_path = Path(path).expanduser()
    if not dir_path.exists():
        return f"[Error] 目录不存在: {path}"

    result = []
    _list_recursive(dir_path, result, depth, 0)
    return "\n".join(result) if result else "(空目录)"


def _list_recursive(path: Path, result: list[str], max_depth: int, current_depth: int):
    indent = "  " * current_depth
    try:
        items = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        for item in items:
            if item.name.startswith(".") and current_depth == 0:
                continue  # 跳过顶层隐藏文件
            if item.is_dir():
                result.append(f"{indent}{item.name}/")
                if current_depth < max_depth - 1:
                    _list_recursive(item, result, max_depth, current_depth + 1)
            else:
                size = item.stat().st_size
                result.append(f"{indent}{item.name} ({size}B)")
    except PermissionError:
        result.append(f"{indent}(permission denied)")


# 工具定义
READ_FILE_TOOL = Tool(
    name="read_file",
    description="读取文件内容。返回带行号的文件内容。",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径"},
            "offset": {"type": "integer", "description": "起始行号（0-indexed）", "default": 0},
            "limit": {"type": "integer", "description": "最大行数", "default": 500},
        },
        "required": ["path"],
    },
    handler=read_file,
    category="file",
)

WRITE_FILE_TOOL = Tool(
    name="write_file",
    description="创建或覆盖写入文件。父目录会自动创建。",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径"},
            "content": {"type": "string", "description": "文件内容"},
        },
        "required": ["path", "content"],
    },
    handler=write_file,
    category="file",
)

EDIT_FILE_TOOL = Tool(
    name="edit_file",
    description="精确编辑文件：查找 old_text 并替换为 new_text。old_text 必须精确匹配。",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径"},
            "old_text": {"type": "string", "description": "要替换的原文（必须精确匹配）"},
            "new_text": {"type": "string", "description": "替换后的文本"},
        },
        "required": ["path", "old_text", "new_text"],
    },
    handler=edit_file,
    category="file",
)

LIST_DIR_TOOL = Tool(
    name="list_directory",
    description="列出目录结构和文件。",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "目录路径", "default": "."},
            "depth": {"type": "integer", "description": "递归深度", "default": 1},
        },
        "required": [],
    },
    handler=list_directory,
    category="file",
)
