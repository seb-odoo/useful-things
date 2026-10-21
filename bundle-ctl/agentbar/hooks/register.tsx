import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { Agent, Request } from '../types'

const EVERY = 10_000
const OPS_DOOR = '/run/host-ctl'
const SHOWN = 4
const SOCKET = '/run/bundle-ctl/ctl.sock'

const agent = atom({ plugin: 'agentbar', key: 'agent' } as const, null as Agent | null)
const requests = atom({ plugin: 'agentbar', key: 'requests' } as const, [] as Request[])
const now = atom({ plugin: 'agentbar', key: 'now' } as const, 0)

function formatAge(seconds: number): string {
  const total = Math.max(0, Math.round(seconds))
  if (total < 60) {
    return `${total} s`
  }
  if (total < 3600) {
    return `${Math.floor(total / 60)} min`
  }
  if (total < 86400) {
    return `${Math.floor(total / 3600)} h`
  }
  return `${Math.floor(total / 86400)} d`
}

async function ask($: EngineInterface, path: string): Promise<Record<string, unknown> | null> {
  const argv = ['curl', '-s', '--max-time', '5', '--unix-socket', SOCKET, `http://bundle-ctl${path}`]
  try {
    const { exitCode, stdout } = await $.process.run(argv, { timeoutMs: 8000 })
    return exitCode === 0 ? JSON.parse(stdout) : null
  } catch {
    return null
  }
}

async function findAgent($: EngineInterface): Promise<Agent | null> {
  if (!(await $.fs.exists(SOCKET))) {
    return null
  }
  const answer = await ask($, '/whoami')
  if (typeof answer?.bundle === 'string') {
    return { key: answer.bundle, bundle: answer.bundle, session: null }
  }
  if (answer?.caller !== 'host' || !(await $.fs.exists(OPS_DOOR))) {
    return null
  }
  const session = await $.session.id()
  return { key: `ops-${session}`, bundle: null, session }
}

function toRequest(raw: Record<string, unknown>): Request {
  return {
    name: String(raw.name ?? ''),
    where: String(raw.where ?? ''),
    action: String(raw.action ?? ''),
    created: typeof raw.created === 'number' ? raw.created : 0,
    later: raw.later === true,
  }
}

async function poll($: EngineInterface) {
  let found = await read($, agent)
  if (!found) {
    found = await findAgent($)
    if (!found) {
      return
    }
    const seen = found
    await update($, agent, () => seen)
  }
  const path = found.bundle ? '/hok' : `/hok?session=${encodeURIComponent(found.session ?? '')}`
  const answer = await ask($, path)
  const list = Array.isArray(answer?.requests) ? answer.requests.map(toRequest) : []
  if (JSON.stringify(list) !== JSON.stringify(await read($, requests))) {
    await update($, requests, () => list)
  }
  if (list.length > 0) {
    const at = (await $.clock.now()) / 1000
    await update($, now, () => at)
  }
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    $.clock.every(EVERY, () => poll($))
    void poll($)
    return next(e)
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const found = await read($, agent)
    // VS Code draws a link that is not https as plain text.
    if (!found || e.props.hasSurvey || e.surface !== 'terminal') {
      return next(e)
    }
    const list = await read($, requests)
    const at = await read($, now)
    const { Box, Link, Text } = $.ui.resolve(e)
    const links = [
      <Link key="list" href={`odoo-bundle://list/${found.key}`}>
        📋 agent list
      </Link>,
    ]
    if (found.bundle) {
      links.push(
        '   ',
        <Link key="tabs" href={`odoo-bundle://tabs/${found.bundle}`}>
          🦊 tabs
        </Link>,
      )
    }
    if (list.length > SHOWN) {
      links.push(`   and ${list.length - SHOWN} more in hok`)
    }
    return (
      <Box flexDirection="column">
        {list.slice(0, SHOWN).map(request => (
          <Text key={request.name} wrap="truncate-end" dimColor={request.later}>
            {request.later ? '🔁 ' : '🔑 '}
            <Link href={`odoo-bundle://hok/${encodeURIComponent(request.name)}`}>{request.name}</Link>
            {`  ${request.where}  ${request.action}  ${formatAge(at - request.created)}`}
            {request.later ? ', later' : ''}
          </Text>
        ))}
        <Text dimColor={list.length === 0}>{links}</Text>
      </Box>
    )
  })
}
