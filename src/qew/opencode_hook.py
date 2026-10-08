"""opencode project plugin adapter."""

from pathlib import Path

from qew.hook_config import install_managed_file, remove_managed_file


_MARKER = "// Managed by Qew: opencode-hooks-v1"
_PLUGIN_PATH = (".opencode", "plugins", "qew-hooks.js")
# Qew's earlier session-start prime plugin: dropped on install when Qew owns it.
_LEGACY_MARKER = "// Managed by Qew: opencode-prime-v1"
_LEGACY_PATH = (".opencode", "plugins", "qew-prime.js")
_PLUGIN = f'''{_MARKER}
import {{ spawn }} from "node:child_process"

// Pipe a shell command to `qew hook` and return {{ code, output }}. Never throws:
// a missing, failing, or slow qew means silence, not a broken session. Exit
// code 2 is Qew's deliberate refusal; the output then names the Qew command to use.
const run = (directory, event, command) =>
  new Promise((resolve) => {{
    const quiet = {{ code: 0, output: "" }}
    try {{
      const child = spawn("qew", ["hook", event, "--agent", "opencode"], {{
        cwd: directory,
        stdio: ["pipe", "pipe", "ignore"],
        timeout: 10000,
        windowsHide: true,
      }})
      let output = ""
      child.stdout.setEncoding("utf8")
      child.stdout.on("data", (chunk) => {{
        if (output.length < 8192) output += chunk
      }})
      child.on("error", () => resolve(quiet))
      child.on("close", (code) => resolve(code === 0 || code === 2 ? {{ code, output: output.trim() }} : quiet))
      child.stdin.on("error", () => {{}})
      child.stdin.end(JSON.stringify({{ tool_name: "bash", tool_input: {{ command }}, cwd: directory }}))
    }} catch {{
      resolve(quiet)
    }}
  }})

export const QewHooksPlugin = async ({{ directory }}) => {{
  const commands = new Map()

  return {{
    "tool.execute.before": async (input, output) => {{
      let refusal = null
      try {{
        if (input.tool !== "bash") return
        const command = output?.args?.command
        if (typeof command !== "string") return
        commands.set(input.callID, command)
        const result = await run(directory, "pre-tool", command)
        if (result.code === 2) refusal = result.output || "refused by Qew"
      }} catch {{}}
      if (refusal) throw new Error(refusal)
    }},
    "tool.execute.after": async (input, output) => {{
      try {{
        if (input.tool !== "bash") return
        const command = input.args?.command ?? commands.get(input.callID)
        commands.delete(input.callID)
        if (typeof command !== "string") return
        const result = await run(directory, "post-tool", command)
        if (result.code === 0 && result.output) {{
          output.output = [output.output ?? "", result.output].filter(Boolean).join("\\n\\n")
        }}
      }} catch {{}}
    }},
  }}
}}
'''


def install(root: Path) -> bool:
    installed = install_managed_file(root, _PLUGIN_PATH, _PLUGIN, _MARKER)
    dropped = remove_managed_file(root, _LEGACY_PATH, _LEGACY_MARKER)
    return installed or dropped


def remove(root: Path) -> bool:
    removed = remove_managed_file(root, _PLUGIN_PATH, _MARKER)
    dropped = remove_managed_file(root, _LEGACY_PATH, _LEGACY_MARKER)
    return removed or dropped
