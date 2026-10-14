import { describe, it, expect } from 'vitest'
import { mkdtempSync, writeFileSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { BASH_EGRESS_DENY, mergeBashEgressDeny, removeBashEgressDeny, liftBashEgressDeny, mainAgentShellEgressOpen } from '../web/agent-scaffold.js'

// The owner's opt-out for the main agent (2026-10-04): the switch lives in the
// operator file, and lifting removes exactly the module's own rules.
describe('main agent shell egress opt-out', () => {
  it('reads the switch from store/egress-allowlist.json and defaults to closed', () => {
    const dir = mkdtempSync(join(tmpdir(), 'egress-optout-'))
    expect(mainAgentShellEgressOpen(dir)).toBe(false)
    writeFileSync(join(dir, 'egress-allowlist.json'), JSON.stringify({ domains: [] }))
    expect(mainAgentShellEgressOpen(dir)).toBe(false)
    writeFileSync(join(dir, 'egress-allowlist.json'), JSON.stringify({ main_agent_shell_egress: 'open' }))
    expect(mainAgentShellEgressOpen(dir)).toBe(true)
  })

  it('removes only the egress rules and keeps the operator\'s own deny entries', () => {
    const settings: Record<string, unknown> = { permissions: { deny: ['Bash(rm -rf /*)'] } }
    expect(mergeBashEgressDeny(settings)).toBe(true)
    expect((settings.permissions as { deny: string[] }).deny.length).toBe(1 + BASH_EGRESS_DENY.length)
    expect(removeBashEgressDeny(settings)).toBe(true)
    expect((settings.permissions as { deny: string[] }).deny).toEqual(['Bash(rm -rf /*)'])
    expect(removeBashEgressDeny(settings)).toBe(false)
  })

  it('lifts the rules from the main agent config dir file', () => {
    const dir = mkdtempSync(join(tmpdir(), 'egress-lift-'))
    writeFileSync(join(dir, 'settings.json'), JSON.stringify({ permissions: { deny: [...BASH_EGRESS_DENY] }, other: 1 }))
    expect(liftBashEgressDeny(dir)).toBe(true)
    const after = JSON.parse(readFileSync(join(dir, 'settings.json'), 'utf-8'))
    expect(after.permissions.deny).toEqual([])
    expect(after.other).toBe(1)
    expect(liftBashEgressDeny(dir)).toBe(false)
  })
})
