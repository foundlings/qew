"""Walk of the generated opencode plugin executed under node against a fake `qew`.

Adversary: a plugin that crashes the agent, shells out for unrelated tools, or lets a
qew failure or a missing binary turn into a thrown error; only a deliberate refusal throws.
"""

import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest

from tests.ledger_harness import World

NODE = shutil.which("node")

DRIVER = """
const [pluginPath, mode] = process.argv.slice(2)
const mod = await import(pluginPath)
const hooks = await mod.QewHooksPlugin({ directory: process.cwd() })
const log = []
async function call(tool, command, output) {
  const entry = { tool, command, threw: null, output: null }
  const out = { output }
  try {
    await hooks["tool.execute.before"]({ tool, callID: "c" + log.length }, { args: { command } })
    entry.afterRan = true
    await hooks["tool.execute.after"]({ tool, callID: "c" + (log.length), args: { command } }, out)
  } catch (error) {
    entry.threw = String(error.message)
    entry.afterRan = false
  }
  entry.output = out.output
  log.push(entry)
}
if (mode === "normal") {
  await call("bash", "ls", "listing")
  await call("read", "ls", "file text")
  await call("bash", "FAIL now", "kept")
  await call("bash", "NOTE this", "ran")
} else if (mode === "deny") {
  await call("bash", "DENY this", "never")
} else {
  await call("bash", "ls", "listing")
}
console.log(JSON.stringify(log))
"""

FAKE_QEW = """#!/bin/sh
echo "$@" >> "$QEW_LOG"
input=$(cat)
echo "$input" >> "$QEW_LOG"
case "$input" in
  *FAIL*) exit 3 ;;
  *DENY*) if [ "$2" = "pre-tool" ]; then echo "use qew complete instead"; exit 2; fi ;;
  *NOTE*) if [ "$2" = "post-tool" ]; then echo "reminder text"; fi ;;
esac
exit 0
"""


@unittest.skipIf(NODE is None, "node is not installed")
class OpencodePluginWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)
        self.assertEqual(self.world.run("hook", "install", "--opencode").returncode, 0)
        self.work = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.work, True)
        shutil.copy(self.world.code / ".opencode" / "plugins" / "qew-hooks.js", self.work / "plugin.mjs")
        (self.work / "driver.mjs").write_text(DRIVER, encoding="utf-8")
        (self.work / "bin").mkdir()
        fake = self.work / "bin" / "qew"
        fake.write_text(FAKE_QEW, encoding="utf-8")
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        self.log = self.work / "qew.log"

    def drive(self, mode: str, path: str = None) -> list:
        env = {**os.environ, "QEW_LOG": str(self.log), "PATH": path or f"{self.work / 'bin'}:{os.environ['PATH']}"}
        result = subprocess.run(
            [NODE, str(self.work / "driver.mjs"), str(self.work / "plugin.mjs"), mode],
            cwd=self.work, env=env, capture_output=True, text=True, timeout=60, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_bash_calls_pipe_to_qew_and_nothing_else_does_and_failures_never_throw(self) -> None:
        """The plugin must not crash the agent nor shell out for unrelated tools."""
        ok, read, failing, noted = self.drive("normal")
        self.assertEqual((ok["threw"], ok["output"]), (None, "listing"))
        self.assertEqual((read["threw"], read["output"]), (None, "file text"))
        self.assertEqual((failing["threw"], failing["output"]), (None, "kept"))
        self.assertEqual((noted["threw"], noted["output"]), (None, "ran\n\nreminder text"))
        lines = self.log.read_text(encoding="utf-8").splitlines()
        argv = [line for line in lines if line.startswith("hook ")]
        self.assertEqual(argv.count("hook pre-tool --agent opencode"), 3)
        self.assertEqual(argv.count("hook post-tool --agent opencode"), 3)
        stdins = [json.loads(line) for line in lines if line.startswith("{")]
        self.assertEqual({entry["tool_input"]["command"] for entry in stdins}, {"ls", "FAIL now", "NOTE this"})
        self.assertNotIn("file text", self.log.read_text(encoding="utf-8"))

    def test_a_refusal_throws_its_reason_and_the_after_hook_does_not_run(self) -> None:
        """Exit 2 must block the tool call with qew's own words."""
        (entry,) = self.drive("deny")
        self.assertEqual(entry["threw"], "use qew complete instead")
        self.assertFalse(entry["afterRan"])
        self.assertEqual(entry["output"], "never")
        self.assertNotIn("post-tool", self.log.read_text(encoding="utf-8"))

    def test_a_missing_qew_binary_never_throws(self) -> None:
        """A machine without qew on PATH must not break the agent's shell."""
        (entry,) = self.drive("missing", path="/nonexistent")
        self.assertEqual((entry["threw"], entry["output"]), (None, "listing"))
        self.assertFalse(self.log.exists())


if __name__ == "__main__":
    unittest.main()
