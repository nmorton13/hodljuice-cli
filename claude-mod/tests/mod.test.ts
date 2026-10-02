import { describe, expect, mock, test } from 'claude-code/testing'
import type { On, RenderElement } from 'claude-code'

import { panelState, parseRadioArgs, parseResultsText, parseSaved, parseSearchArgs, parseStatus, resultsText, statusLine } from '../hooks/lib'

const PLAYING = {
  state: 'playing',
  title: 'Noded 0.3.0 with Saifedean Ammous',
  podcast: 'Noded Bitcoin Podcast',
  published: '2017-12-02',
  play_url: 'https://hodljuice.app/e/VUfVU8-9IFM',
  position: 723,
  duration: 2710,
  radio: null,
}

const IDLE = { state: 'idle', title: null, podcast: null, published: null, play_url: null, position: null, duration: null, radio: null }

const PANE_PROPS = { title: 'HodlJuice', isFocused: true, bodyColumns: 80, placement: 'dock', scroll: { bodyRows: 20, offset: 0 }, view: {} }

const SAVED = [{ id: 'WHMoleIi_O8', title: 'Bitcoin Tonight - 040', podcast: 'Pleb UnderGround', published: '2026-09-16', play_url: 'https://hodljuice.app/e/WHMoleIi_O8' }]

/** Stands in for the engine and the hj CLI; records every hj argv the mod runs. */
function world(on: On, player: Record<string, unknown>[]) {
  const runs: string[][] = []
  mock.clock(on, { now: Date.parse('2026-09-27T12:00:00Z') }) // a Sunday: no morning toast
  mock.store(on)
  on('process.run', ($, e) => {
    runs.push([...e.argv])
    const st = player[0]!
    if (e.argv[2] === 'pause') player[0] = { ...st, state: st.state === 'playing' ? 'paused' : 'playing' }
    if (e.argv[2] === 'next') player[0] = { ...st, title: 'The next one' }
    if (e.argv[2] === 'radio') player[0] = { ...PLAYING, title: 'A radio pick' }
    const out = e.argv[2] === 'status' ? JSON.stringify(player[0]) : e.argv[1] === 'saved' ? JSON.stringify(SAVED) : ''
    return { value: { exitCode: 0, stdout: out, stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
  })
  on('command.register', ($, e) => ({ value: { command: e.name } }))
  on('tool.register', ($, e) => ({ value: { tool: `mcp__hodljuice__${e.name}` } }))
  on('ui.toast', () => ({ value: undefined }))
  on('ui.open', () => ({ value: { isPlaced: true as const } }))
  on('session.start', ($, e) => ({ cwd: e.cwd }))
  return runs
}

function run($: any, command: string) {
  return $.command.run({ command, args: '', origin: 'user', presentation: 'line' })
}

async function start($: any) {
  await $.session.start({ cwd: '/tmp', surface: 'terminal', isInteractive: true })
}

describe('panel', () => {
  test('shows the episode, controls and saved episodes', async ($, on) => {
    world(on, [{ ...PLAYING }])
    await start($)
    await run($, 'hj-panel')
    for (const surface of ['terminal', 'desktop'] as const) {
      const ui = await $.ui.mount({ plugin: 'hodljuice', surface, component: 'Pane', requestId: 'hj-panel', props: PANE_PROPS as any })
      expect(await ui.find({ type: 'Text', text: 'Noded Bitcoin Podcast' })).toBeDefined()
      expect(await ui.find({ type: 'Text', text: /^Noded 0\.3\.0 with Saifedean/ })).toBeDefined()
      expect(await ui.find({ type: 'Text', text: /12:03/ })).toBeDefined()
      expect(await ui.find({ type: 'Text', text: /45:10/ })).toBeDefined()
      for (const key of ['pause', 'next', 'prev', 'save', 'open', 'stop', 'play-WHMoleIi_O8']) expect(await ui.find({ key })).toBeDefined()
      await ui.unmount()
    }
  })

  test('buttons run hj ctl', async ($, on) => {
    const runs = world(on, [{ ...PLAYING }])
    await start($)
    await run($, 'hj-panel')
    const ui = await $.ui.mount({ plugin: 'hodljuice', surface: 'terminal', component: 'Pane', requestId: 'hj-panel', props: PANE_PROPS as any })
    await ui.press({ key: 'pause' })
    expect((await ui.find({ key: 'pause' }))?.text).toBe('play')
    await ui.press({ key: 'play-WHMoleIi_O8' })
    expect(runs.some(argv => argv.join(' ') === 'hj ctl pause')).toBe(true)
    expect(runs.some(argv => argv.join(' ') === 'hj ctl play WHMoleIi_O8')).toBe(true)
    await ui.unmount()
  })

  test('idle shows no controls', async ($, on) => {
    world(on, [{ ...IDLE }])
    await start($)
    const ui = await $.ui.mount({ plugin: 'hodljuice', surface: 'terminal', component: 'Pane', requestId: 'hj-panel', props: PANE_PROPS as any })
    expect(await ui.find({ key: 'pause' })).toBeUndefined()
    expect(await ui.find({ type: 'Text', text: /Nothing playing/ })).toBeDefined()
    await ui.press({ key: 'radio' })
    expect(await ui.find({ key: 'pause' })).toBeDefined()
    await ui.unmount()
  })
})

describe('opening and closing the panel', () => {
  test('/hj-panel starts the radio when nothing plays', async ($, on) => {
    const runs = world(on, [{ ...IDLE }])
    await start($)
    await run($, 'hj-panel')
    expect(runs.some(argv => argv.join(' ') === 'hj ctl radio')).toBe(true)
    const ui = await $.ui.mount({ plugin: 'hodljuice', surface: 'terminal', component: 'Pane', requestId: 'hj-panel', props: PANE_PROPS as any })
    expect(await ui.find({ key: 'pause' })).toBeDefined()
    await ui.unmount()
  })

  test('/hj-panel leaves a playing episode alone', async ($, on) => {
    const runs = world(on, [{ ...PLAYING }])
    await start($)
    await run($, 'hj-panel')
    expect(runs.some(argv => argv[2] === 'radio')).toBe(false)
  })

  test('the close button closes the pane', async ($, on) => {
    world(on, [{ ...PLAYING }])
    const closed: string[] = []
    on('ui.close', ($, e) => {
      closed.push(e.id)
      return { value: undefined }
    })
    await start($)
    await run($, 'hj-panel')
    const ui = await $.ui.mount({ plugin: 'hodljuice', surface: 'terminal', component: 'Pane', requestId: 'hj-panel', props: PANE_PROPS as any })
    await ui.press({ key: 'close' })
    expect(closed).toEqual(['hj-panel'])
    await ui.unmount()
  })
})

describe('hint line and quick commands', () => {
  test('the hint line follows the player and clears when idle', async ($, on) => {
    const player: Record<string, unknown>[] = [{ ...PLAYING }]
    world(on, player)
    on('ui.render', { component: 'PromptHint' }, ($, e) => {
      const { Text } = $.ui.resolve(e)
      return h(Text, {}, `${e.props.hint}${e.props.tail ?? ''}`) as RenderElement
    })
    await start($)
    const props = { isDraft: false, isWorking: false, hint: '? for shortcuts' }
    const ui = await $.ui.mount({ plugin: 'hodljuice', surface: 'terminal', component: 'PromptHint', props })
    expect((await ui.find({ type: 'Text' }))?.text).toMatch(/^\? for shortcuts {2}▶ 12:03\/45:10 Noded 0\.3\.0.*$/)
    player[0] = { ...IDLE }
    await run($, 'hj-stop')
    expect((await ui.find({ type: 'Text' }))?.text).toBe('? for shortcuts')
    await ui.unmount()
  })

  test('/hj-next and /hj-pause run hj ctl and report', async ($, on) => {
    const runs = world(on, [{ ...PLAYING }])
    await start($)
    expect((await run($, 'hj-next')).text).toBe('Playing: The next one')
    await run($, 'hj-pause')
    expect(runs.some(argv => argv.join(' ') === 'hj ctl next')).toBe(true)
    expect(runs.some(argv => argv.join(' ') === 'hj ctl pause')).toBe(true)
  })
})

describe('pause when Claude needs you', () => {
  test('pauses on ask and resumes when the turn completes', async ($, on) => {
    const player = [{ ...PLAYING }]
    const runs = world(on, player)
    on('tool.check', () => ({ decision: 'ask' as const }))
    on('turn.complete', () => ({ text: '' }))
    await start($)
    await $.tool.check({ tool: 'Bash', input: { command: 'rm -rf build' }, tool_use_id: 'toolu_1' })
    expect(player[0]!.state).toBe('paused')
    await $.turn.complete({ reason: 'answer', turnId: 't1', answer: '', durationMs: 1000, isAborted: false })
    expect(player[0]!.state).toBe('playing')
    expect(runs.filter(argv => argv[2] === 'pause')).toHaveLength(2)
  })

  test("leaves a paused player alone", async ($, on) => {
    const player = [{ ...PLAYING, state: 'paused' }]
    const runs = world(on, player)
    on('tool.check', () => ({ decision: 'ask' as const }))
    on('turn.complete', () => ({ text: '' }))
    await start($)
    await $.tool.check({ tool: 'Bash', input: { command: 'ls' }, tool_use_id: 'toolu_2' })
    await $.turn.complete({ reason: 'answer', turnId: 't2', answer: '', durationMs: 1000, isAborted: false })
    expect(runs.filter(argv => argv[2] === 'pause')).toHaveLength(0)
    expect(player[0]!.state).toBe('paused')
  })

  test('can be turned off', { options: { pauseOnAsk: false } }, async ($, on) => {
    const player = [{ ...PLAYING }]
    world(on, player)
    on('tool.check', () => ({ decision: 'ask' as const }))
    await start($)
    await $.tool.check({ tool: 'Bash', input: { command: 'ls' }, tool_use_id: 'toolu_3' })
    expect(player[0]!.state).toBe('playing')
  })

  test('ignores allowed calls', async ($, on) => {
    const player = [{ ...PLAYING }]
    world(on, player)
    on('tool.check', () => ({ decision: 'allow' as const }))
    await start($)
    await $.tool.check({ tool: 'Read', input: { file_path: 'a' }, tool_use_id: 'toolu_4' })
    expect(player[0]!.state).toBe('playing')
  })
})

describe('status parsing', () => {
  test('strips escapes and control characters from feed text', () => {
    const st = parseStatus(
      JSON.stringify({ ...PLAYING, title: 'Evil \u001b]8;;https://x.example\u0007link\u001b]8;;\u0007 \u001b[31mred\u001b[0m\nnext', play_url: 'javascript:alert(1)' }),
    )
    expect(st?.title).toBe('Evil link red next')
    expect(st?.play_url).toBeNull()
  })

  test('anything unexpected reads as idle or nothing', () => {
    expect(parseStatus('not json')).toBeNull()
    expect(parseStatus(JSON.stringify({ state: 'exploded' }))?.state).toBe('idle')
  })

  test('status line and saved list', () => {
    const st = parseStatus(JSON.stringify({ ...PLAYING, state: 'paused', radio: {} }))!
    expect(statusLine(st)).toBe('⏸ 12:03/45:10 Noded 0.3.0 with Saifedean Ammous')
    expect(statusLine(parseStatus(JSON.stringify(IDLE)))).toBeUndefined()
    expect(parseSaved(JSON.stringify([...SAVED, { id: 'bad id', title: 'x' }])).map(h => h.id)).toEqual(['WHMoleIi_O8'])
    expect(parseSaved('nope')).toEqual([])
  })

  test('the panel header names the state and the station', () => {
    const st = parseStatus(JSON.stringify(PLAYING))!
    expect(panelState(st)).toBe('Playing')
    expect(panelState({ ...st, state: 'paused' })).toBe('Paused')
    expect(panelState({ ...st, radio: { year: 2018 } })).toBe('📻 Radio · 2018')
    expect(panelState(null)).toBe('Idle')
  })
})

describe('command arguments', () => {
  test('/hj-radio flags', () => {
    expect(parseRadioArgs('--year 2017 --show "What Bitcoin Did" --topic climate')).toEqual({
      argv: ['--year', '2017', '--show', 'What Bitcoin Did', '--topic', 'climate'],
    })
    expect('error' in parseRadioArgs('--topic weather')).toBe(true)
    expect('error' in parseRadioArgs('--year; rm -rf ~')).toBe(true)
    expect('error' in parseRadioArgs('--exec ls')).toBe(true)
  })

  test('/hj year', () => {
    expect(parseSearchArgs('taproot activation 2021')).toEqual({ query: 'taproot activation', year: 2021 })
    expect(parseSearchArgs('2140')).toEqual({ query: '2140' })
  })

  test('/hj results survive the round trip through markdown', () => {
    const hits = [{ id: 'VUfVU8-9IFM', title: 'A [weird] *title* · with dots', podcast: 'Show_name', published: '2017-12-02' }]
    const back = parseResultsText(resultsText('q', hits))
    expect(back).toEqual([{ ...hits[0]!, title: 'A [weird] *title* - with dots' }])
  })
})
