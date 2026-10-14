// OpenCode 2.x plugin: time every MCP tool call (server, tool, duration, ok/error).
// Install: copy to ~/.config/opencode/plugins/ (or .opencode/plugins/ in a project).
// OpenCode 2 plugins export a default {id, setup}; setup gets the plugin context and
// registers `ctx.tool.hook("execute.before" | "execute.after", fn)` (checked against v2.0.28).
// OpenCode has no API to write into the chat from a plugin: lines go to
// $SUBTASK_HOME/opencode-tool-timing.jsonl (default ~/.local/share/subtask). Watch it with
// `tail -f`. Only tools named "<server>_<tool>" of an MCP server are recorded; set
// SUBTASK_TIMING_PREFIX to limit it (e.g. "subtask_").
import { appendFileSync, mkdirSync } from "node:fs"
import { homedir } from "node:os"
import { join } from "node:path"

const DIR = process.env.SUBTASK_HOME ?? join(homedir(), ".local", "share", "subtask")
const FILE = join(DIR, "opencode-tool-timing.jsonl")
const PREFIX = process.env.SUBTASK_TIMING_PREFIX ?? ""
const BUILTIN = new Set(["bash", "shell", "read", "write", "edit", "patch", "glob", "grep", "list", "task", "todowrite", "todoread", "webfetch", "question", "execute", "search"])

const started = new Map<string, number>()
const key = (e: any) => String(e.callID ?? e.toolCallID ?? e.id ?? `${e.sessionID}:${e.tool}`)
const watched = (tool: string) =>
  tool.startsWith(PREFIX) && !BUILTIN.has(tool) && tool.includes("_") && !tool.startsWith("opencode_")

export default {
  id: "subtask.tool-timing",
  setup: async (ctx: any) => {
    mkdirSync(DIR, { recursive: true })
    await ctx.tool.hook("execute.before", (e: any) => {
      if (watched(String(e.tool))) started.set(key(e), Date.now())
    })
    await ctx.tool.hook("execute.after", (e: any) => {
      const t0 = started.get(key(e))
      if (t0 === undefined) return
      started.delete(key(e))
      const line = { ts: new Date().toISOString(), tool: e.tool, ms: Date.now() - t0, status: e.status ?? (e.error ? "error" : "ok") }
      try {
        appendFileSync(FILE, JSON.stringify(line) + "\n")
      } catch {
        // a timing line must never break a tool call
      }
    })
  },
}
