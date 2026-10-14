// OpenCode plugin: log the duration of subtask MCP tool calls.
// Install: copy to ~/.config/opencode/plugins/ (or .opencode/plugins/ in a project).
// OpenCode names MCP tools "<server>_<tool>"; set SUBTASK_PROGRESS_PREFIX to watch another
// server, or "" to time every tool. Hook signatures: @opencode-ai/plugin dist/index.d.ts.
import type { Plugin } from "@opencode-ai/plugin"

const PREFIX = process.env.SUBTASK_PROGRESS_PREFIX ?? "subtask_"

export const SubtaskProgress: Plugin = async ({ client }) => {
  const started = new Map<string, number>()
  return {
    "tool.execute.before": async (input) => {
      if (input.tool.startsWith(PREFIX)) started.set(input.callID, Date.now())
    },
    "tool.execute.after": async (input) => {
      const t0 = started.get(input.callID)
      if (t0 === undefined) return
      started.delete(input.callID)
      const ms = Date.now() - t0
      await client.app.log({
        body: {
          service: "subtask-progress",
          level: "info",
          message: `mcp ${input.tool} ${(ms / 1000).toFixed(1)}s`,
          extra: { tool: input.tool, duration_ms: ms, sessionID: input.sessionID },
        },
      })
    },
  }
}
