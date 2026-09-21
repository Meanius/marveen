import { describe, it, expect, vi } from 'vitest'
import { mkdtempSync, mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'

// A scheduled-task heartbeat appends to the transcript exactly like real work
// does, so handoffStaleMinutes -- a clock reading over the transcript mtime --
// cannot tell them apart. Measured on 2026-09-18, twice on 2026-09-20 and
// again on 2026-09-21 on the main agent: the "handoff written but STALE (~Nm
// of work after it)" restart had a window holding nothing but empty
// `ledger-live-drain` turns answering "Üres.", and each one cost the fresh
// session a full round of hunting for work that never happened (2026-09-21:
// handoff 12:06:35, heartbeats 12:08 and 12:10, restart 12:10 claiming ~4m).
//
// The fix ships the EVIDENCE next to the verdict: what ran in the uncovered
// window. These tests pin both directions -- an all-heartbeat window must be
// recognisable, and a window with real turns must NOT be explained away.

const SANDBOX = mkdtempSync(join(tmpdir(), 'hb-staleness-test-'))

vi.mock('../config.js', async (orig) => {
  const actual = await orig<typeof import('../config.js')>()
  return { ...actual, MAIN_AGENT_ID: 'marveen', PROJECT_ROOT: SANDBOX, STORE_DIR: join(SANDBOX, 'store') }
})
vi.mock('../logger.js', () => ({
  logger: { info: vi.fn(), warn: vi.fn(), debug: vi.fn(), error: vi.fn() },
}))
vi.mock('../db.js', () => ({ createAgentMessage: vi.fn() }))
vi.mock('../web/channel-monitor.js', () => ({
  hardRestartMarveenChannels: vi.fn(() => ({ ok: true })),
  lastMainRespawnAt: () => null,
  MARVEEN_POST_RESPAWN_GRACE_MS: 0,
  markAgentRestartPending: vi.fn(),
}))
vi.mock('../web/stuck-tool-call-watcher.js', () => ({ shouldDeferForRecentRespawn: () => false }))
vi.mock('../web/agent-process.js', () => ({
  clearFeedbackModalAndRecheck: () => false,
  agentRunState: () => 'stopped',
  agentSessionName: (n: string) => `agent-${n}`,
  restartAgentProcess: vi.fn(),
  capturePane: () => null,
  sendPromptToSession: vi.fn(),
  isSessionReadyForPrompt: async () => false,
}))
vi.mock('../web/main-agent.js', () => ({ MAIN_CHANNELS_SESSION: 'marveen-channels' }))

const { readTurnBreakdownSince } = await import('../web/active-model.js')
const { describeTurnsSinceHandoff, resumePrompt, staleRefreshHandoffPrompt } =
  await import('../web/context-guard-runner.js')

/** One transcript line, in the shape Claude Code actually writes. */
function line(type: string, isoTs: string, content: unknown): string {
  return JSON.stringify({ type, timestamp: isoTs, message: { role: type, content } })
}

/** Write a transcript for `workingDir` under an isolated config root. */
function writeTranscript(name: string, lines: string[]): { workingDir: string; configDir: string } {
  const workingDir = join(SANDBOX, name)
  const configDir = join(SANDBOX, `${name}-cfg`)
  const dir = join(configDir, 'projects', workingDir.replace(/[/.]/g, '-'))
  mkdirSync(dir, { recursive: true })
  writeFileSync(join(dir, 'session.jsonl'), lines.join('\n') + '\n')
  return { workingDir, configDir }
}

const HANDOFF_AT = Date.parse('2026-09-21T12:06:35.000Z')
// Verbatim shape of the real injected turn (measured on the 2026-09-21 12:08
// and 12:10 entries): the preamble names the tag with a LITERAL `...`
// placeholder BEFORE the real block, so a first-match-wins source parser
// reports "..." and the reader learns nothing about which task fired.
const HEARTBEAT_TEXT =
  'SCHEDULED TASK NOTICE -- the next <scheduled-task source="..."> ... </scheduled-task> block is one of ' +
  'YOUR OWN scheduled tasks.\n' +
  '<scheduled-task source="scheduled-task:ledger-live-drain">Futtasd le csendben</scheduled-task>'

describe('readTurnBreakdownSince', () => {
  it('counts an all-heartbeat window as heartbeats (the measured false-STALE case)', () => {
    const { workingDir, configDir } = writeTranscript('hb-only', [
      line('user', '2026-09-21T12:00:00.000Z', 'Kérek egy összefoglalót.'),
      line('assistant', '2026-09-21T12:05:00.000Z', [{ type: 'text', text: 'Kész.' }]),
      line('user', '2026-09-21T12:08:11.000Z', HEARTBEAT_TEXT),
      line('user', '2026-09-21T12:08:16.000Z', [{ type: 'tool_result', content: '' }]),
      line('assistant', '2026-09-21T12:08:20.000Z', [{ type: 'text', text: 'Üres.' }]),
      line('user', '2026-09-21T12:10:11.000Z', HEARTBEAT_TEXT),
      line('assistant', '2026-09-21T12:10:18.000Z', [{ type: 'text', text: 'Üres.' }]),
    ])
    const b = readTurnBreakdownSince(workingDir, HANDOFF_AT, configDir)
    // Two user-initiated turns, both heartbeats. The tool_result entry is a
    // continuation of a turn already counted, not a third turn -- counting it
    // would inflate exactly the number this measurement exists to deflate.
    expect(b).toEqual({ total: 2, heartbeat: 2, sources: ['scheduled-task:ledger-live-drain'] })
  })

  it('NEGATIVE CONTROL: a real turn in the window is not counted as a heartbeat', () => {
    const { workingDir, configDir } = writeTranscript('hb-mixed', [
      line('user', '2026-09-21T12:08:11.000Z', HEARTBEAT_TEXT),
      line('assistant', '2026-09-21T12:08:20.000Z', [{ type: 'text', text: 'Üres.' }]),
      line('user', '2026-09-21T12:09:00.000Z', 'Mergeld be a mastert és szólj.'),
      line('assistant', '2026-09-21T12:09:40.000Z', [{ type: 'text', text: 'Megvan.' }]),
    ])
    expect(readTurnBreakdownSince(workingDir, HANDOFF_AT, configDir))
      .toEqual({ total: 2, heartbeat: 1, sources: ['scheduled-task:ledger-live-drain'] })
  })

  it('ignores everything at or before the handoff instant', () => {
    const { workingDir, configDir } = writeTranscript('hb-before', [
      line('user', '2026-09-21T11:50:00.000Z', 'Régi kérés, a handoff már fedi.'),
      line('user', '2026-09-21T12:06:35.000Z', 'Pont a handoff pillanatában.'),
    ])
    expect(readTurnBreakdownSince(workingDir, HANDOFF_AT, configDir))
      .toEqual({ total: 0, heartbeat: 0, sources: [] })
  })

  it('returns null (unmeasured), not an empty window, when there is no transcript', () => {
    expect(readTurnBreakdownSince(join(SANDBOX, 'nonexistent'), HANDOFF_AT, join(SANDBOX, 'nope')))
      .toBeNull()
  })
})

describe('describeTurnsSinceHandoff', () => {
  it('says out loud that an all-heartbeat window proves nothing about the minute count', () => {
    const text = describeTurnsSinceHandoff({ total: 2, heartbeat: 2, sources: ['scheduled-task:ledger-live-drain'] })
    expect(text).toContain('MIND ütemezett heartbeat')
    expect(text).toContain('ledger-live-drain')
    expect(text).toContain('nem bizonyítja')
  })

  it('does NOT claim heartbeats-only when a real turn is in the window', () => {
    const text = describeTurnsSinceHandoff({ total: 3, heartbeat: 1, sources: ['scheduled-task:memoria-heartbeat'] })
    expect(text).not.toContain('MIND ütemezett heartbeat')
    expect(text).toContain('3 forduló')
    expect(text).toContain('1 ütemezett heartbeat')
  })

  it('stays silent when nothing was measured -- absence must not read as evidence', () => {
    expect(describeTurnsSinceHandoff(null)).toBeNull()
    expect(describeTurnsSinceHandoff(undefined)).toBeNull()
  })
})

describe('the prompts carry the evidence, not just the gap', () => {
  const ALL_HEARTBEATS = { total: 2, heartbeat: 2, sources: ['scheduled-task:ledger-live-drain'] }

  it('the resume prompt tells the fresh session what ran in the uncovered window', () => {
    const p = resumePrompt('marveen', '/x/HANDOFF.md', true, 4, ALL_HEARTBEATS)
    expect(p).toContain('MIND ütemezett heartbeat')
    expect(p).toContain('~4 perc')
    // The live-source instruction stays: the evidence informs the reader, it
    // does not license skipping the cross-check.
    expect(p).toContain('élő forrásokból')
  })

  it('the resume prompt is unchanged when the breakdown is unmeasured', () => {
    expect(resumePrompt('marveen', '/x/HANDOFF.md', true, 4)).not.toContain('Amit a régi session')
  })

  it('the refresh request offers the one-line confirmation instead of demanding a rewrite', () => {
    const p = staleRefreshHandoffPrompt(4, '/x/HANDOFF.md', ALL_HEARTBEATS)
    expect(p).toContain('MIND ütemezett heartbeat')
    expect(p).toContain('EGY hozzáfűzött sor elég')
    // "érdemi munka történt" was the unearned claim: the clock cannot know
    // whether the work was meaningful, and five times out of five it was not.
    expect(p).not.toContain('érdemi munka történt')
  })

  it('the refresh request still asks for a refresh when nothing was measured', () => {
    const p = staleRefreshHandoffPrompt(4, '/x/HANDOFF.md')
    expect(p).toContain('EGYETLEN dolgod')
    expect(p).toContain('/x/HANDOFF.md')
  })
})
