"""Hook ownership: ctx recognises its hook entries by the CTX_HOOK=1 marker and by the unmarked
format installed before the marker (`python3 <path>/ctx.py hook <event>`); a user's hook is never
touched."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import ctx  # noqa: E402

LEGACY = "python3 /old/clone/ctx.py hook {}"
MARKED = "env CTX_HOOK=1 python3 /new/clone/ctx.py hook {}"
USER = ("echo hook done", "python3 /x/other.py hook stop", "my-ctx.py-lint hook x",
        "python3 /x/notctx.py hook stop", "echo CTX_HOOK=1-not-a-prefix hook")


class HookMarker(unittest.TestCase):
    def test_installed_commands_carry_the_marker(self):
        for client in ctx.CLIENTS:
            commands = _commands(ctx.hooks_block(client))
            self.assertTrue(commands, client)
            for c in commands:
                self.assertTrue(c.startswith("env CTX_HOOK=1 "), c)
                self.assertTrue(ctx.is_ctx_hook_command(c), c)

    def test_both_formats_are_ours_user_hooks_are_not(self):
        for event in ("session-start", "post-tool-use", "stop", "session-end"):
            self.assertTrue(ctx.hook_ours({"command": LEGACY.format(event)}))
            self.assertTrue(ctx.hook_ours({"command": LEGACY.format(event) + " --client cursor"}))
            self.assertTrue(ctx.hook_ours({"command": MARKED.format(event)}))
            self.assertTrue(ctx.hook_ours({"command": f"python3 '/a b/ctx.py' hook {event}"}))
        for c in USER:
            self.assertFalse(ctx.hook_ours({"command": c}), c)
        self.assertFalse(ctx.hook_ours({"command": 3}))
        self.assertFalse(ctx.hook_ours("not a dict"))

    def test_marker_is_the_primary_criterion(self):
        # the marker plus `ctx.py hook `: any clone path, quoted or not; a renamed script is not ours
        self.assertTrue(ctx.is_ctx_hook_command("env CTX_HOOK=1 python3 '/my clone/ctx.py' hook stop"))
        self.assertFalse(ctx.is_ctx_hook_command("env CTX_HOOK=1 python3 /opt/renamed.py hook stop"))
        self.assertTrue(ctx.is_ctx_hook_command("CTX_HOOK=1 python3 /x/ctx.py hook stop"))
        self.assertFalse(ctx.is_ctx_hook_command("my-ctx.py-lint hook x"))

    def test_reinstall_over_legacy_entries_replaces_them_and_keeps_user_hooks(self):
        user = {"type": "command", "command": USER[0]}
        legacy = {"type": "command", "command": LEGACY.format("stop")}
        cur = {"hooks": {"Stop": [{"hooks": [user, legacy]}]}}
        new = ctx.merged_settings(cur, "claude")
        stop = [h["command"] for g in new["hooks"]["Stop"] for h in g["hooks"]]
        self.assertEqual(stop[0], USER[0])
        # the legacy entry is replaced, not doubled
        self.assertEqual(len([c for c in stop if ctx.is_ctx_hook_command(c)]), 1)
        self.assertIn("CTX_HOOK=1", stop[1])
        self.assertEqual(ctx.unmerged(new, "claude"), {"hooks": {"Stop": [{"hooks": [user]}]}})
        self.assertEqual(ctx.unmerged(cur, "claude"), {"hooks": {"Stop": [{"hooks": [user]}]}})

    def test_cursor_and_agy_legacy_entries_are_ours(self):
        start = LEGACY.format("session-start")
        cur = {"version": 1, "hooks": {"stop": [{"command": USER[0]}],
                                       "sessionStart": [{"command": start + " --client cursor"}]}}
        self.assertEqual(ctx.unmerged(cur, "cursor"),
                         {"version": 1, "hooks": {"stop": [{"command": USER[0]}]}})
        legacy_agy = {"ctx": {"SessionStart": [{"type": "command", "command": start}]}}
        self.assertEqual(ctx.unmerged(legacy_agy, "agy"), {})
        self.assertIn("CTX_HOOK=1", json.dumps(ctx.merged_settings(legacy_agy, "agy")))
        with self.assertRaises(ValueError):  # a user entry named "ctx" is never replaced
            ctx.merged_settings({"ctx": {"SessionStart": [{"command": USER[0]}]}}, "agy")

    def test_marker_alone_is_not_enough_it_must_be_a_hook_command(self):
        for c in ("env CTX_HOOK=1 my-own-script", "CTX_HOOK=1 make lint", "env CTX_HOOK=1 echo hooked",
                  "env CTX_HOOK=1 other.sh hook x", "echo CTX_HOOK=1 && my hook thing"):
            self.assertFalse(ctx.is_ctx_hook_command(c), c)
        self.assertTrue(ctx.is_ctx_hook_command(MARKED.format("stop")))

    def test_agy_entry_is_ours_only_if_every_command_is_a_ctx_hook(self):
        ours = {"type": "command", "command": MARKED.format("session-start") + " --client agy"}
        user = {"type": "command", "command": USER[0]}
        self.assertTrue(ctx.ours_entry({"SessionStart": [ours]}))
        self.assertFalse(ctx.ours_entry({"SessionStart": [ours, user]}))  # mixed: the user's entry
        # the ctx command text in a non-command field does not make the entry ours
        self.assertFalse(ctx.ours_entry({"SessionStart": [dict(user, note=MARKED.format("stop"))]}))
        self.assertFalse(ctx.ours_entry({}))
        with self.assertRaises(ValueError):
            ctx.merged_settings({"ctx": {"SessionStart": [ours, user]}}, "agy")


def _commands(block):
    """Every `command` string of a hooks block (flat or grouped entries)."""
    out = []
    for entries in block.values():
        for e in entries:
            out += [h["command"] for h in e.get("hooks", [])] if "hooks" in e else [e["command"]]
    return out


if __name__ == "__main__":
    unittest.main()
