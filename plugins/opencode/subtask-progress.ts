// OpenCode 2.x plugin: time every MCP tool call, and keep a live board of subtask's work.
// Install: copy to ~/.config/opencode/plugins/ (or .opencode/plugins/ in a project).
// OpenCode 2 plugins export a default {id, setup}; setup gets the plugin context and
// registers `ctx.tool.hook("execute.before" | "execute.after", fn)` (checked against v2.0.28).
// OpenCode has no API to write into the chat or the TUI from a plugin, so both outputs are files
// in $SUBTASK_HOME (default ~/.local/share/subtask):
//   opencode-tool-timing.jsonl  one line per MCP call: tool, duration, ok/error.
//                               SUBTASK_TIMING_PREFIX limits it (e.g. "subtask_").
//   opencode-board.txt          groups (DAG queue and depends_on, map_reduce progress), jobs with
//                               verify retries and rollback, jobs rejoined after a restart, budget,
//                               invariants, oracle speedup, bisect rounds, foreach copies, consensus.
//                               Rewritten after every subtask call and every few seconds while work
//                               runs. Watch it in a split: `watch -n2 cat ~/.local/share/subtask/opencode-board.txt`.
// The board comes from `subtask board --json` (SUBTASK_BIN overrides the command).
import { execFile } from "node:child_process"
import { appendFileSync, mkdirSync, writeFileSync } from "node:fs"
import { homedir } from "node:os"
import { join } from "node:path"

const DIR = process.env.SUBTASK_HOME ?? join(homedir(), ".local", "share", "subtask")
const FILE = join(DIR, "opencode-tool-timing.jsonl")
const BOARD = join(DIR, "opencode-board.txt")
const BIN = process.env.SUBTASK_BIN ?? "subtask"
const TICK_MS = Number(process.env.SUBTASK_BOARD_TICK_MS ?? 3000)
const PREFIX = process.env.SUBTASK_TIMING_PREFIX ?? ""
const BUILTIN = new Set(["bash", "shell", "read", "write", "edit", "patch", "glob", "grep", "list", "task", "todowrite", "todoread", "webfetch", "question", "execute", "search"])
const ACTIVE = new Set(["queued", "running"])

const started = new Map<string, number>()
const key = (e: any) => String(e.callID ?? e.toolCallID ?? e.id ?? `${e.sessionID}:${e.tool}`)
const watched = (tool: string) =>
  tool.startsWith(PREFIX) && !BUILTIN.has(tool) && tool.includes("_") && !tool.startsWith("opencode_")

type Job = {
  id: string; state: string; engine: string; group: string | null; attempt: number
  verify: string | null; verify_attempts: number; max_retries: number; retrying: boolean
  rolled_back_to: number | null; stalled: string | null; budget_warning: string | null
  invariants?: string[]; reverted?: string[]
  oracle?: { output_match?: boolean; speedup_pct?: number | null; ms_before?: number; ms_after?: number; error?: string } | null
}
type Task = { alias: string; state: string; job: string | null; depends_on: string[]; why: string; expanded_from?: string | null }
type Bisect = { id: string; state: string; round: number; tested: number; candidates: number; commit?: string }
type Group = { id: string; kind: string; label: string; summary: string; tasks: Task[] }
type Board = { groups: Group[]; jobs: Job[]; bisects?: Bisect[]; budget: { cap_usd: number; left_usd: number } }

// Jobs already running when OpenCode started survived a restart of the client: say so.
let rejoined: Set<string> | null = null
let timer: ReturnType<typeof setInterval> | null = null

export function render(board: Board, back: Set<string>): string {
  const lines: string[] = [`subtask · ${new Date().toLocaleTimeString()}`]
  for (const g of board.groups) {
    const kind = g.kind === "map_reduce" ? "map_reduce" : g.label === "consensus" ? "consensus" : "dag"
    lines.push(`${kind} ${g.id}${g.label && g.label !== "consensus" ? ` ${g.label}` : ""} · ${g.summary}`)
    for (const t of g.tasks) {
      const deps = t.depends_on.length ? ` ← ${t.depends_on.join(", ")}` : ""
      const what = t.job ?? (t.state === "pending" ? "queued" : t.state)
      const fan = t.expanded_from ? ` (from ${t.expanded_from})` : ""
      lines.push(`  ${t.alias}${fan}: ${what}${deps}${t.why ? ` · ${t.why}` : ""}`)
    }
  }
  for (const j of board.jobs) {
    const bits = [j.state, j.engine]
    if (j.verify) bits.push(`verify ${j.verify} (${j.verify_attempts}/${j.max_retries + 1})`)
    if (j.retrying) bits.push("retrying")
    if (j.rolled_back_to) bits.push(`rolled back to ${j.rolled_back_to}`)
    if (j.stalled) bits.push(`STALLED ${j.stalled}`)
    if (back.has(j.id)) bits.push("rejoined after restart")
    if (j.budget_warning) bits.push("budget_warning")
    if (j.invariants?.length) bits.push(`INVARIANT ${j.invariants[0].slice(0, 60)}`)
    if (j.reverted?.length) bits.push(`reverted ${j.reverted.length} file(s) outside allowed_paths`)
    if (j.oracle) {
      const same = j.oracle.error ? "error" : j.oracle.output_match ? "same output" : "OUTPUT DIFFERS"
      const pct = j.oracle.speedup_pct
      bits.push(`oracle ${same}${pct == null ? "" : ` ${pct >= 0 ? "+" : ""}${Math.round(pct)}% (${j.oracle.ms_before}ms → ${j.oracle.ms_after}ms)`}`)
    }
    lines.push(`${j.id} ${bits.join(" · ")}`)
  }
  for (const b of board.bisects ?? []) {
    const found = b.commit ? ` → ${b.commit.slice(0, 9)}` : ""
    lines.push(`${b.id} bisect ${b.state} · round ${b.round} · ${b.tested}/${b.candidates} tested${found}`)
  }
  lines.push(`budget: ${board.budget.left_usd.toFixed(2)}$ left of ${board.budget.cap_usd.toFixed(0)}$`)
  return lines.join("\n") + "\n"
}

function refresh(): void {
  execFile(BIN, ["board", "--json"], { timeout: 15000 }, (err, stdout) => {
    if (err) return // no subtask on PATH, or a slow disk: the next tick tries again
    try {
      const board = JSON.parse(stdout) as Board
      if (rejoined === null) rejoined = new Set(board.jobs.filter((j) => ACTIVE.has(j.state)).map((j) => j.id))
      writeFileSync(BOARD, render(board, rejoined))
      const busy = board.jobs.some((j) => ACTIVE.has(j.state) || j.retrying) ||
        board.groups.some((g) => g.tasks.some((t) => t.state === "pending"))
      if (busy && timer === null) timer = setInterval(refresh, TICK_MS)
      if (!busy && timer !== null) {
        clearInterval(timer)
        timer = null
      }
    } catch {
      // a board line must never break a tool call
    }
  })
}

export default {
  id: "subtask.tool-timing",
  setup: async (ctx: any) => {
    mkdirSync(DIR, { recursive: true })
    refresh()
    await ctx.tool.hook("execute.before", (e: any) => {
      if (watched(String(e.tool))) started.set(key(e), Date.now())
    })
    await ctx.tool.hook("execute.after", (e: any) => {
      if (String(e.tool).startsWith("subtask_")) refresh()
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
