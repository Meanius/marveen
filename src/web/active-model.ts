import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { homedir } from 'node:os'
import { encodeClaudeProjectDir } from '../claude-project-dir.js'

// Claude Code writes one .jsonl session log per session under
// ~/.claude/projects/<encoded-working-dir>/. Every assistant turn carries the
// model id that answered it. We use that to surface the *live* running model
// (vs. the configured value in agent-config.json), so the dashboard can show
// what the running process is actually using, including across restarts.
//
// When an agent is launched with --continue, Claude Code appends to the same
// session jsonl across restarts, so the latest "model" field may reflect a
// pre-restart turn rather than the freshly-spawned process. Callers that know
// when the current session started should pass sinceUnixSec; we then ignore
// any line whose own timestamp predates that, leaving the caller to fall back
// to the configured model until the new session writes its first turn.
const cache = new Map<string, { value: string | null; expiresAt: number }>()
const TTL_MS = 3000

// Resolve the session-log directory Claude Code writes for a working dir.
// Logs live under <config-root>/projects/<encoded-working-dir>/, where the
// config root is ~/.claude by default but an alternate one when the agent was
// launched with CLAUDE_CONFIG_DIR. Pass that absolute config root as configDir
// so we read the right project dir for agents on a non-default config.
export function projectsDirFor(workingDir: string, configDir?: string, homeDirOverride?: string): string {
  const base = configDir ?? join(homeDirOverride ?? homedir(), '.claude')
  // The encoding is Claude Code's, measured -- see src/claude-project-dir.ts.
  const encoded = encodeClaudeProjectDir(workingDir)
  return join(base, 'projects', encoded)
}

export function readActiveModelFromProjectDir(workingDir: string, sinceUnixSec?: number, configDir?: string): string | null {
  const now = Date.now()
  const cacheKey = `${workingDir}:${sinceUnixSec ?? ''}:${configDir ?? ''}`
  const cached = cache.get(cacheKey)
  if (cached && cached.expiresAt > now) return cached.value
  let value: string | null = null
  try {
    const dir = projectsDirFor(workingDir, configDir)
    if (!existsSync(dir)) {
      cache.set(cacheKey, { value: null, expiresAt: now + TTL_MS })
      return null
    }
    const jsonls = readdirSync(dir)
      .filter(f => f.endsWith('.jsonl'))
      .map(f => ({ f, mtime: statSync(join(dir, f)).mtimeMs }))
      .sort((a, b) => b.mtime - a.mtime)
    if (jsonls.length === 0) {
      cache.set(cacheKey, { value: null, expiresAt: now + TTL_MS })
      return null
    }
    const content = readFileSync(join(dir, jsonls[0].f), 'utf-8')
    const lines = content.split('\n')
    for (let i = lines.length - 1; i >= 0; i--) {
      const line = lines[i].trim()
      if (!line) continue
      try {
        const entry = JSON.parse(line)
        const msg = entry?.message
        const model = msg?.model
        if (typeof model !== 'string' || model.startsWith('<')) continue
        if (sinceUnixSec !== undefined) {
          const ts = entry?.timestamp
          if (typeof ts !== 'string') continue
          const lineUnix = Math.floor(new Date(ts).getTime() / 1000)
          if (!Number.isFinite(lineUnix) || lineUnix < sinceUnixSec) continue
        }
        value = model
        break
      } catch { /* skip malformed JSON line */ }
    }
  } catch { /* fall through */ }
  cache.set(cacheKey, { value, expiresAt: now + TTL_MS })
  return value
}

const ctxCache = new Map<string, { value: number | null; expiresAt: number }>()

// Current context size of the live session, in tokens. Claude Code records a
// `usage` object on each assistant turn; the context that gets re-read every
// turn is input_tokens + cache_read_input_tokens + cache_creation_input_tokens
// (output_tokens is the new reply, not context). We scan the newest transcript
// from the end for the last turn carrying a usage and sum those three. Returns
// null when there is no transcript / no usage yet (fresh session). This is what
// the dashboard surfaces so the operator can see a session growing heavy and
// decide to restart it.
export function readContextTokensFromProjectDir(workingDir: string, configDir?: string): number | null {
  const now = Date.now()
  const cacheKey = `${workingDir}:${configDir ?? ''}`
  const cached = ctxCache.get(cacheKey)
  if (cached && cached.expiresAt > now) return cached.value
  let value: number | null = null
  try {
    const dir = projectsDirFor(workingDir, configDir)
    if (existsSync(dir)) {
      const jsonls = readdirSync(dir)
        .filter(f => f.endsWith('.jsonl'))
        .map(f => ({ f, mtime: statSync(join(dir, f)).mtimeMs }))
        .sort((a, b) => b.mtime - a.mtime)
      if (jsonls.length > 0) {
        const content = readFileSync(join(dir, jsonls[0].f), 'utf-8')
        const lines = content.split('\n')
        for (let i = lines.length - 1; i >= 0; i--) {
          const line = lines[i].trim()
          if (!line) continue
          try {
            const u = JSON.parse(line)?.message?.usage
            if (u && typeof u === 'object') {
              const inp = Number(u.input_tokens) || 0
              const cr = Number(u.cache_read_input_tokens) || 0
              const cc = Number(u.cache_creation_input_tokens) || 0
              const total = inp + cr + cc
              if (total > 0) { value = total; break }
            }
          } catch { /* skip malformed JSON line */ }
        }
      }
    }
  } catch { /* fall through */ }
  ctxCache.set(cacheKey, { value, expiresAt: now + TTL_MS })
  return value
}

/**
 * Wall-clock mtime (ms) of the newest transcript for a working dir, or null
 * when there is none (fresh session, unreadable dir, agent on a remote host).
 *
 * This is the cheapest "when did this session last do anything" signal, and
 * the honest one: Claude Code appends to the jsonl on every turn, so the
 * file's mtime is written BY the session, outside the dashboard process. A
 * clock kept in dashboard memory dies with the dashboard, and a
 * count-the-sweeps streak measures the sweep interval rather than the agent.
 * Neither survives a restart; this does.
 *
 * What it does NOT measure: whether the agent is working right now. A single
 * long tool call (a 30-minute Bash, a subagent) appends nothing while it runs,
 * so the transcript goes quiet while real work is in flight. Callers must pair
 * this with a live-work signal -- the guard uses paneIdle -- and never treat a
 * stale mtime on its own as "finished".
 *
 * The mtime is already computed inside readContextTokensFromProjectDir to pick
 * the newest file; this exposes it rather than recomputing the selection
 * differently, so the two always describe the SAME transcript.
 */
export function readTranscriptMtimeFromProjectDir(workingDir: string, configDir?: string): number | null {
  try {
    const dir = projectsDirFor(workingDir, configDir)
    if (!existsSync(dir)) return null
    let newest: number | null = null
    for (const f of readdirSync(dir)) {
      if (!f.endsWith('.jsonl')) continue
      const m = statSync(join(dir, f)).mtimeMs
      if (newest === null || m > newest) newest = m
    }
    return newest
  } catch { return null }
}

/**
 * Newest transcript mtime for `workingDir` across SEVERAL candidate config
 * roots, or null when no candidate has one.
 *
 * Same "probe every root, newest wins" rule the inbound probe uses, and for the
 * same reason: whether a session writes under the shared ~/.claude or under an
 * isolated CLAUDE_CONFIG_DIR is decided by gates (settings, fleet token, dir
 * existence) that a watchdog must not try to re-derive. A root that is not in
 * use simply yields an older timestamp or none.
 *
 * An `undefined` entry means the shared ~/.claude default, so a caller that
 * already has a single known root can pass `[root]` and get the old behaviour.
 */
export function readTranscriptMtimeAcrossConfigDirs(
  workingDir: string,
  configDirs: ReadonlyArray<string | undefined>,
): number | null {
  let newest: number | null = null
  for (const configDir of configDirs) {
    const m = readTranscriptMtimeFromProjectDir(workingDir, configDir)
    if (m != null && (newest === null || m > newest)) newest = m
  }
  return newest
}

/**
 * Breakdown of the user-initiated turns a session ran AFTER a given instant,
 * or null when there is no readable transcript.
 *
 * Why this exists: handoff staleness is measured from the transcript's mtime
 * (see handoffStaleMinutes), and a scheduled-task heartbeat touches the
 * transcript exactly like real work does. Measured on 2026-09-18,
 * twice on 2026-09-20 and again on 2026-09-21: the guard restarted with
 * "handoff written but STALE (~Nm of work after it)" when the whole uncovered
 * window was empty `ledger-live-drain` heartbeats answering "Üres." The fresh
 * session then spent a full round hunting for work that never happened. The
 * calibrated example is 2026-09-21: handoff at 12:06:35, restart at 12:10
 * claiming ~4m of work, window measured here = 2 turns, both heartbeats.
 * (The same guard also calls it RIGHT -- msg 1276 the same morning flagged a
 * weekly summary that really was missing -- so the verdict stays; only the
 * evidence is new.)
 *
 * This does NOT re-decide the staleness verdict -- it ships the EVIDENCE next
 * to it, so the reader (the fresh session, or the supervisor) can tell an
 * uncovered window of real work from a window of heartbeats without measuring
 * it again. A verdict computed from turn labels would be a guess; a count of
 * what ran is a measurement.
 *
 * Counted: user-initiated turns only. Tool results are `type: 'user'` entries
 * too, so an entry whose content carries a tool_result is a continuation of a
 * turn already counted, not a new one.
 */
export interface TranscriptTurnBreakdown {
  /** User-initiated turns strictly after sinceMs. */
  total: number
  /** Of those, prompts injected by the scheduler (heartbeats). */
  heartbeat: number
  /** Distinct scheduled-task sources seen, at most 4, in first-seen order. */
  sources: string[]
}

// Global: the scheduler's preamble names the tag with a LITERAL placeholder
// (`the next <scheduled-task source="..."> block`) before the real block
// arrives in the same message, so the FIRST match is always "..." -- measured
// on the 2026-09-21 12:08 and 12:10 turns. Take the first concrete one.
const HEARTBEAT_SOURCE_RE = /<scheduled-task\s+source="([^"]*)"/g
const HEARTBEAT_MARKER_RE = /SCHEDULED TASK NOTICE|<scheduled-task\s|^\s*\[Heartbeat:/

/** First named scheduled-task source in the text, skipping the preamble's
 *  `...` placeholder; null when the turn names none. */
function concreteHeartbeatSource(text: string): string | null {
  HEARTBEAT_SOURCE_RE.lastIndex = 0
  for (let m = HEARTBEAT_SOURCE_RE.exec(text); m !== null; m = HEARTBEAT_SOURCE_RE.exec(text)) {
    const src = m[1].trim()
    if (src && src !== '...') return src
  }
  return null
}

/** Flatten a transcript entry's message content to plain text for matching. */
function entryText(entry: unknown): string {
  const content = (entry as { message?: { content?: unknown } })?.message?.content
  if (typeof content === 'string') return content
  if (!Array.isArray(content)) return ''
  const parts: string[] = []
  for (const block of content) {
    if (block && typeof block === 'object' && typeof (block as { text?: unknown }).text === 'string') {
      parts.push((block as { text: string }).text)
    }
  }
  return parts.join('\n')
}

/** True when the entry is a tool_result continuation rather than a new turn. */
function isToolResultEntry(entry: unknown): boolean {
  const content = (entry as { message?: { content?: unknown } })?.message?.content
  if (!Array.isArray(content)) return false
  return content.some(b => b && typeof b === 'object' && (b as { type?: unknown }).type === 'tool_result')
}

export function readTurnBreakdownSince(
  workingDir: string,
  sinceMs: number,
  configDir?: string,
): TranscriptTurnBreakdown | null {
  try {
    const dir = projectsDirFor(workingDir, configDir)
    if (!existsSync(dir)) return null
    // Same selection as readTranscriptMtimeFromProjectDir: newest jsonl wins,
    // so the breakdown always describes the transcript the staleness gap was
    // measured against. Two different selections would describe two sessions.
    const jsonls = readdirSync(dir)
      .filter(f => f.endsWith('.jsonl'))
      .map(f => ({ f, mtime: statSync(join(dir, f)).mtimeMs }))
      .sort((a, b) => b.mtime - a.mtime)
    if (jsonls.length === 0) return null
    const lines = readFileSync(join(dir, jsonls[0].f), 'utf-8').split('\n')
    let total = 0
    let heartbeat = 0
    const sources: string[] = []
    // Backwards with an early stop: the window of interest is the tail, and a
    // day-long transcript is megabytes. Stopping at the first entry that
    // predates sinceMs is safe -- Claude Code appends in time order.
    for (let i = lines.length - 1; i >= 0; i--) {
      const line = lines[i].trim()
      if (!line) continue
      let entry: { type?: unknown; timestamp?: unknown }
      try { entry = JSON.parse(line) } catch { continue }
      const ts = typeof entry.timestamp === 'string' ? Date.parse(entry.timestamp) : NaN
      if (!Number.isFinite(ts)) continue
      if (ts <= sinceMs) break
      if (entry.type !== 'user') continue
      if (isToolResultEntry(entry)) continue
      const text = entryText(entry)
      if (!text.trim()) continue
      total++
      if (HEARTBEAT_MARKER_RE.test(text)) {
        heartbeat++
        const src = concreteHeartbeatSource(text)
        if (src && sources.length < 4 && !sources.includes(src)) sources.unshift(src)
      }
    }
    return { total, heartbeat, sources }
  } catch { return null }
}
