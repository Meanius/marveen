#!/usr/bin/env python3
"""Unit tests for scripts/hooks/skill-usage-capture.py.

Tests cover _classify() and _agent_id_from_cwd() -- the two pure-logic
functions that determine what gets logged and under which agent.

Privacy: only fake agent IDs (agent-a, agent-b) and synthetic paths are used.
"""
import sys
import os
import unittest

# Resolve the hook module without importing as a side-effect runner.
import importlib.util

_HOOK_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "hooks", "skill-usage-capture.py",
)

_spec = importlib.util.spec_from_file_location("skill_usage_capture", _HOOK_PATH)
hook = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(hook)  # type: ignore[union-attr]


class TestClassify(unittest.TestCase):
    """_classify(tool_name, tool_input) -> (skill_name, trigger_type) | None"""

    def _call(self, tool_name, tool_input):
        return hook._classify(tool_name, tool_input)

    # Skill tool ----------------------------------------------------------------

    def test_skill_tool_returns_tool_call(self):
        result = self._call("Skill", {"skill": "fleet-helper"})
        self.assertEqual(result, ("fleet-helper", "tool_call"))

    def test_skill_tool_args_variant(self):
        result = self._call("Skill", {"skill": "deep-research", "args": "something"})
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "deep-research")
        self.assertEqual(result[1], "tool_call")

    def test_skill_tool_strips_whitespace(self):
        result = self._call("Skill", {"skill": "  fleet-helper  "})
        self.assertEqual(result, ("fleet-helper", "tool_call"))

    def test_skill_tool_empty_name_returns_none(self):
        result = self._call("Skill", {"skill": ""})
        self.assertIsNone(result)

    def test_skill_tool_missing_skill_key_returns_none(self):
        result = self._call("Skill", {})
        self.assertIsNone(result)

    # Read tool + SKILL.md path -------------------------------------------------

    def test_read_skill_md_returns_skill_read(self):
        home = os.path.expanduser("~")
        path = f"{home}/.claude/skills/fleet-helper/SKILL.md"
        result = self._call("Read", {"file_path": path})
        self.assertEqual(result, ("fleet-helper", "skill_read"))

    def test_read_skill_md_extracts_skill_name(self):
        home = os.path.expanduser("~")
        path = f"{home}/.claude/skills/deep-research/SKILL.md"
        result = self._call("Read", {"file_path": path})
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "deep-research")

    def test_read_non_skill_md_returns_none(self):
        home = os.path.expanduser("~")
        # Only the SKILL.md at the top of a skill dir should match.
        result = self._call("Read", {"file_path": f"{home}/.claude/skills/fleet-helper/references/extra.md"})
        self.assertIsNone(result)

    def test_read_arbitrary_file_returns_none(self):
        result = self._call("Read", {"file_path": "/some/other/file.md"})
        self.assertIsNone(result)

    def test_read_no_file_path_returns_none(self):
        result = self._call("Read", {})
        self.assertIsNone(result)

    # Bash (skills are read with cat/head/sed in bypass-permissions mode) --------
    #
    # These use a real skill directory created in setUp, because the Bash branch
    # only records names that exist on disk.

    def setUp(self):
        self.skills_root = os.path.join(os.path.expanduser("~"), ".claude", "skills")
        self.real_skill = "zz-test-skill-usage-fixture"
        d = os.path.join(self.skills_root, self.real_skill)
        os.makedirs(d, exist_ok=True)
        self.fixture = os.path.join(d, "SKILL.md")
        with open(self.fixture, "w", encoding="utf-8") as f:
            f.write("---\nname: fixture\n---\n")
        self._fixture_dir = d

    def tearDown(self):
        try:
            os.remove(self.fixture)
            os.rmdir(self._fixture_dir)
        except OSError:
            pass

    def test_bash_cat_tilde_path_returns_skill_read(self):
        result = self._call("Bash", {"command": f"cat ~/.claude/skills/{self.real_skill}/SKILL.md"})
        self.assertEqual(result, (self.real_skill, "skill_read"))

    def test_bash_cat_absolute_path_returns_skill_read(self):
        home = os.path.expanduser("~")
        result = self._call(
            "Bash", {"command": f"cat {home}/.claude/skills/{self.real_skill}/SKILL.md"}
        )
        self.assertEqual(result, (self.real_skill, "skill_read"))

    def test_bash_home_variable_path_returns_skill_read(self):
        result = self._call(
            "Bash", {"command": f"sed -n 1,40p $HOME/.claude/skills/{self.real_skill}/SKILL.md"}
        )
        self.assertEqual(result, (self.real_skill, "skill_read"))

    def test_bash_path_mid_command_returns_skill_read(self):
        result = self._call(
            "Bash", {"command": f"wc -l ~/.claude/skills/{self.real_skill}/SKILL.md | tee /tmp/out"}
        )
        self.assertEqual(result, (self.real_skill, "skill_read"))

    def test_bash_glob_is_not_a_skill_name(self):
        """`wc -l ~/.claude/skills/*/SKILL.md` must not be logged as skill `*`."""
        self.assertIsNone(self._call("Bash", {"command": "wc -l ~/.claude/skills/*/SKILL.md"}))

    def test_bash_skills_dir_listing_returns_none(self):
        self.assertIsNone(self._call("Bash", {"command": "ls ~/.claude/skills/"}))

    def test_bash_nonexistent_skill_returns_none(self):
        """A doc comment or test fixture mentioning the pattern is not a read."""
        self.assertIsNone(
            self._call("Bash", {"command": "grep x ~/.claude/skills/<name>/SKILL.md"})
        )

    def test_bash_skips_nonexistent_and_takes_the_real_one(self):
        result = self._call(
            "Bash",
            {"command": f"cat ~/.claude/skills/<name>/SKILL.md ~/.claude/skills/{self.real_skill}/SKILL.md"},
        )
        self.assertEqual(result, (self.real_skill, "skill_read"))

    # Other tools ----------------------------------------------------------------

    def test_bash_without_skill_path_returns_none(self):
        self.assertIsNone(self._call("Bash", {"command": "echo hi"}))

    def test_write_tool_returns_none(self):
        self.assertIsNone(self._call("Write", {"file_path": "/tmp/x.txt", "content": "x"}))

    def test_edit_tool_returns_none(self):
        self.assertIsNone(self._call("Edit", {"file_path": "/tmp/x.txt"}))

    def test_websearch_returns_none(self):
        self.assertIsNone(self._call("WebSearch", {"query": "something"}))

    def test_unknown_tool_returns_none(self):
        self.assertIsNone(self._call("UnknownTool", {"key": "value"}))


class TestAgentIdFromCwd(unittest.TestCase):
    """_agent_id_from_cwd(cwd) derives the agent identity from the session cwd."""

    def _call(self, cwd):
        return hook._agent_id_from_cwd(cwd)

    def _install(self):
        return hook._install_dir()

    def test_agents_subdir_returns_agent_name(self):
        install = self._install()
        cwd = os.path.join(install, "agents", "agent-a")
        self.assertEqual(self._call(cwd), "agent-a")

    def test_agents_subdir_nested_returns_first_segment(self):
        install = self._install()
        cwd = os.path.join(install, "agents", "agent-b", "subdir")
        self.assertEqual(self._call(cwd), "agent-b")

    def test_install_root_returns_main_agent_id(self):
        install = self._install()
        result = self._call(install)
        # Should fall back to MAIN_AGENT_ID or 'marveen'
        self.assertIsInstance(result, str)
        self.assertTrue(len(result) > 0)

    def test_empty_cwd_returns_nonempty_string(self):
        result = self._call("")
        self.assertIsInstance(result, str)
        self.assertTrue(len(result) > 0)

    def test_trailing_slash_ignored(self):
        install = self._install()
        cwd_with_slash = os.path.join(install, "agents", "agent-a") + "/"
        self.assertEqual(self._call(cwd_with_slash), "agent-a")

    # Regression guards for the drifted-private-copy bug: the hook used to
    # carry its own resolver that missed the install-subdirectory case and
    # invented an agent id from the cwd basename, so skill_usage rows were
    # attributed to a directory name instead of a real agent.

    def test_delegates_to_the_shared_ledger_resolver(self):
        import sys as _sys
        _sys.path.insert(0, os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks"))
        import ledger_lib
        self.assertIs(hook._agent_id_from_cwd, ledger_lib.agent_id_from_cwd)

    def test_install_subdir_is_main_agent_not_directory_name(self):
        install = self._install()
        cwd = os.path.join(install, "store", "some-workdir")
        # The drifted copy returned "some-workdir" here.
        self.assertNotEqual(self._call(cwd), "some-workdir")


if __name__ == "__main__":
    unittest.main(verbosity=2)
