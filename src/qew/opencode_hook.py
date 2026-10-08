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

// Pipe a shell command to `qew hook` and return what it says. Never throws:
// a missing, failing, or slow qew means silence, not a broken session.
const run = (directory, event, command) =>
  new Promise((resolve) => {{
    try {{
      const child = spawn("qew", ["hook", event, "--agent", "opencode"], {{
        cwd: directory,
        stdio: ["pipe", "pipe", "ignore"],
        timeout: event === "post-shell" ? 60000 : 10000,
        windowsHide: true,
      }})
      let output = ""
      child.stdout.setEncoding("utf8")
      child.stdout.on("data", (chunk) => {{
        if (output.length < 8192) output += chunk
      }})
      child.on("error", () => resolve(""))
      child.on("close", (code) => resolve(code === 0 ? output.trim() : ""))
      child.stdin.on("error", () => {{}})
      child.stdin.end(JSON.stringify({{ tool_name: "bash", tool_input: {{ command }} }}))
    }} catch {{
      resolve("")
    }}
  }})

export const QewHooksPlugin = async ({{ directory }}) => {{
  const warnings = new Map()
  const commands = new Map()

  return {{
    "tool.execute.before": async (input, output) => {{
      try {{
        if (input.tool !== "bash") return
        const command = output?.args?.command
        if (typeof command !== "string") return
        commands.set(input.callID, command)
        const warning = await run(directory, "pre-shell", command)
        if (warning) warnings.set(input.callID, warning)
      }} catch {{}}
    }},
    "tool.execute.after": async (input, output) => {{
      try {{
        if (input.tool !== "bash") return
        const command = input.args?.command ?? commands.get(input.callID)
        const parts = []
        const warning = warnings.get(input.callID)
        warnings.delete(input.callID)
        commands.delete(input.callID)
        if (warning) parts.push(warning)
        if (typeof command === "string") {{
          const landed = await run(directory, "post-shell", command)
          if (landed) parts.push(landed)
        }}
        if (parts.length) {{
          output.output = [output.output ?? "", ...parts].filter(Boolean).join("\\n\\n")
        }}
      }} catch {{}}
    }},
  }}
}}
'''


def install(root: Path) -> bool:
    installed = install_managed_file(root, _PLUGIN_PATH, _PLUGIN, _MARKER)
    dropped = remove_managed_file(root, _LEGACY_PATH, _LEGACY_MARKER, owned_only=True)
    return installed or dropped


def remove(root: Path) -> bool:
    removed = remove_managed_file(root, _PLUGIN_PATH, _MARKER)
    dropped = remove_managed_file(root, _LEGACY_PATH, _LEGACY_MARKER, owned_only=True)
    return removed or dropped
