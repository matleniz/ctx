"""Hook ownership: ctx recognises its hook entries by the CTX_HOOK=1 marker only; a user's hook is
never touched."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import ctx.cli  # noqa: E402

LEGACY = "python3 /old/clone/ctx.py hook {}"
MARKED = "env CTX_HOOK=1 python3 /new/clone/ctx.py hook {}"
USER = ("echo hook done", "python3 /x/other.py hook stop", "my-ctx.py-lint hook x",
        "python3 /x/notctx.py hook stop", "echo CTX_HOOK=1-not-a-prefix hook")


class HookMarker(unittest.TestCase):
    def test_installed_commands_carry_the_marker(self):
        for client in ctx.hooks.CLIENTS:
            commands = _commands(ctx.install.hooks_block(client))
            self.assertTrue(commands, client)
            for c in commands:
                self.assertTrue(c.startswith("env CTX_HOOK=1 "), c)
                self.assertTrue(ctx.install.is_ctx_hook_command(c), c)

    def test_marked_entries_are_ours_unmarked_ones_are_not(self):
        for event in ("session-start", "post-tool-use", "stop", "session-end"):
            self.assertTrue(ctx.install.hook_ours({"command": MARKED.format(event)}))
            self.assertFalse(ctx.install.hook_ours({"command": LEGACY.format(event)}))
        for c in USER:
            self.assertFalse(ctx.install.hook_ours({"command": c}), c)
        self.assertFalse(ctx.install.hook_ours({"command": 3}))
        self.assertFalse(ctx.install.hook_ours("not a dict"))

    def test_marker_is_the_primary_criterion(self):
        # the marker plus `ctx.py hook `: any clone path, quoted or not; a renamed script is not ours
        self.assertTrue(ctx.install.is_ctx_hook_command("env CTX_HOOK=1 python3 '/my clone/ctx.py' hook stop"))
        self.assertFalse(ctx.install.is_ctx_hook_command("env CTX_HOOK=1 python3 /opt/renamed.py hook stop"))
        self.assertTrue(ctx.install.is_ctx_hook_command("CTX_HOOK=1 python3 /x/ctx.py hook stop"))
        self.assertFalse(ctx.install.is_ctx_hook_command("my-ctx.py-lint hook x"))

    def test_agy_user_entry_named_ctx_is_never_replaced(self):
        with self.assertRaises(ValueError):
            ctx.install.merged_settings({"ctx": {"SessionStart": [{"command": USER[0]}]}}, "agy")

    def test_marker_alone_is_not_enough_it_must_be_a_hook_command(self):
        for c in ("env CTX_HOOK=1 my-own-script", "CTX_HOOK=1 make lint", "env CTX_HOOK=1 echo hooked",
                  "env CTX_HOOK=1 other.sh hook x", "echo CTX_HOOK=1 && my hook thing"):
            self.assertFalse(ctx.install.is_ctx_hook_command(c), c)
        self.assertTrue(ctx.install.is_ctx_hook_command(MARKED.format("stop")))

    def test_agy_entry_is_ours_only_if_every_command_is_a_ctx_hook(self):
        ours = {"type": "command", "command": MARKED.format("session-start") + " --client agy"}
        user = {"type": "command", "command": USER[0]}
        self.assertTrue(ctx.install.ours_entry({"SessionStart": [ours]}))
        self.assertFalse(ctx.install.ours_entry({"SessionStart": [ours, user]}))  # mixed: the user's entry
        # the ctx command text in a non-command field does not make the entry ours
        self.assertFalse(ctx.install.ours_entry({"SessionStart": [dict(user, note=MARKED.format("stop"))]}))
        self.assertFalse(ctx.install.ours_entry({}))
        with self.assertRaises(ValueError):
            ctx.install.merged_settings({"ctx": {"SessionStart": [ours, user]}}, "agy")


def _commands(block):
    """Every `command` string of a hooks block (flat or grouped entries)."""
    out = []
    for entries in block.values():
        for e in entries:
            out += [h["command"] for h in e.get("hooks", [])] if "hooks" in e else [e["command"]]
    return out


if __name__ == "__main__":
    unittest.main()
