"""The published agent skill (skills/agentcrawl/SKILL.md) must match the real CLI."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from agentcrawl.cli import main

SKILL = Path(__file__).resolve().parents[1] / "skills" / "agentcrawl" / "SKILL.md"


def _help(command: str, capsys) -> str:
    with pytest.raises(SystemExit) as exit_info:
        main([command, "--help"])
    output = capsys.readouterr()
    assert exit_info.value.code == 0, f"unknown command: agentcrawl {command}\n{output.err}"
    return output.out


def test_skill_frontmatter_names_the_folder() -> None:
    frontmatter = SKILL.read_text(encoding="utf-8").split("---")[1]
    assert re.search(r"^name: agentcrawl$", frontmatter, re.M)
    assert re.search(r"^description: .{40,}", frontmatter, re.M)


def test_skill_only_cites_real_commands_and_flags(capsys) -> None:
    cited = re.findall(r"^\s*agentcrawl ([a-z-]+)([^\n]*)", SKILL.read_text(encoding="utf-8"), re.M)
    assert cited, "the skill should show CLI examples"
    for command, rest in cited:
        help_text = _help(command, capsys)
        for flag in re.findall(r"(?<!\S)(--[a-z-]+)", rest):
            assert re.search(rf"(?<![\w-]){flag}(?![\w-])", help_text), (
                f"unknown flag: agentcrawl {command} {flag}"
            )
