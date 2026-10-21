import { expect, mock, test } from 'claude-code/testing'

const SOCKET = '/run/bundle-ctl/ctl.sock'
const BAND = {
  plugin: 'agentbar',
  component: 'AbovePrompt',
  props: { hasSurvey: false, isWorking: false, maxRows: 10, bodyColumns: 120, scroll: { offset: 0, bodyRows: 10 }, view: {} },
} as const
const REQUESTS = [
  { name: 'g12', where: 'GITHUB', action: 'push 2 commits to master-x--seb of odoo-dev/odoo', created: 1000, later: false },
  { name: 'proposal master-x--seb', where: 'HOST', action: 'change files this bundle only reads', created: 1060, later: true },
]

function answer(on: Parameters<Parameters<typeof test>[1]>[1], whoami: object, asked: string[]) {
  on('process.run', (_, e) => {
    const url = e.argv[e.argv.length - 1] ?? ''
    asked.push(url)
    const body = url.endsWith('/whoami') ? whoami : { requests: REQUESTS }
    return { value: { exitCode: 0, stdout: JSON.stringify(body), stderr: '' } }
  })
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('ui.render', () => ({ type: 'Text', props: {}, children: ['the band of the engine'] }))
}

test('a bundle agent shows its requests and links to each, to its row and to its tabs', async ($, on) => {
  const clock = mock.clock(on, { now: 1_300_000 })
  const asked: string[] = []
  on('fs.exists', (_, e) => ({ value: e.path === SOCKET }))
  answer(on, { bundle: 'master-x--seb', caller: 'master-x--seb' }, asked)

  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  await clock.advance(10_000)
  expect(asked).toContain('http://bundle-ctl/hok')

  const ui = await $.ui.mount({ ...BAND, surface: 'terminal' })
  const links = await ui.findAll({ type: 'Link' })
  expect(links.map(link => link.props.href)).toEqual([
    'odoo-bundle://hok/g12',
    'odoo-bundle://hok/proposal%20master-x--seb',
    'odoo-bundle://list/master-x--seb',
    'odoo-bundle://tabs/master-x--seb',
  ])
  expect(await ui.find({ type: 'Text', text: /GITHUB  push 2 commits .* 5 min$/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /4 min, later$/ })).toBeDefined()
  await ui.unmount()

  // VS Code would draw these links as plain text: its band is the engine's.
  const code = await $.ui.mount({ ...BAND, surface: 'vscode' })
  expect(await code.find({ type: 'Link' })).toBeUndefined()
  await code.unmount()
})

test('an ops agent asks for the requests of its session, and has no tabs link', async ($, on) => {
  const clock = mock.clock(on, { now: 1_300_000 })
  const asked: string[] = []
  on('fs.exists', (_, e) => ({ value: e.path === SOCKET || e.path === '/run/host-ctl' }))
  on('session.id', () => ({ value: 's1' }))
  answer(on, { caller: 'host' }, asked)

  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  await clock.advance(10_000)
  expect(asked).toContain('http://bundle-ctl/hok?session=s1')

  const ui = await $.ui.mount({ ...BAND, surface: 'terminal' })
  const links = await ui.findAll({ type: 'Link' })
  expect(links.map(link => link.props.href).slice(2)).toEqual(['odoo-bundle://list/ops-s1'])
  await ui.unmount()
})

test('a session of the host draws no band', async ($, on) => {
  const clock = mock.clock(on, { now: 1_300_000 })
  const asked: string[] = []
  on('fs.exists', () => ({ value: false }))
  answer(on, { caller: 'host' }, asked)

  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  await clock.advance(10_000)
  expect(asked).toEqual([])

  const ui = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await ui.find({ type: 'Link' })).toBeUndefined()
  await ui.unmount()
})
