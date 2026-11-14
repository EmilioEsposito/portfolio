// /eval-links: the local pages used while working on the Zillow lead evals.
//
// Every link is a localhost address. Ports come from .claude/launch.json in
// the checkout (the configurations named `web` and `evals-data`), so each
// worktree shows its own. The page server's port has a fixed default because
// the review page keeps labels in the browser per address.
import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { EvalLink } from '../types'

const PANE = 'eval-links'
const WEB_PORT = 5173
const PAGES_PORT = 8377

const links = atom({ plugin: 'eval-links', key: 'links' } as const, [], {
  shape: 'links-1',
})

type Launch = { configurations?: { name?: string; port?: number }[] }

async function ports($: EngineInterface) {
  let launch: Launch = {}
  try {
    launch = JSON.parse(await $.fs.read('.claude/launch.json')) as Launch
  } catch {
    // No launch file in this checkout: the defaults stand.
  }
  const port = (name: string, fallback: number) =>
    launch.configurations?.find(one => one.name === name)?.port ?? fallback

  return { web: port('web', WEB_PORT), pages: port('evals-data', PAGES_PORT) }
}

async function status($: EngineInterface, href: string): Promise<EvalLink['status']> {
  try {
    return (await $.http.fetch(href, { method: 'HEAD' })).ok ? 'up' : 'down'
  } catch {
    return 'down'
  }
}

async function refresh($: EngineInterface): Promise<EvalLink[]> {
  const { web, pages } = await ports($)
  const startPages = 'uv run python -m api.src.zillow_leads.evals serve'
  const wanted: Omit<EvalLink, 'status'>[] = [
    {
      label: 'Review page',
      href: `http://localhost:${pages}/review.html`,
      note: 'label the latest experiment pass or fail',
      start: startPages,
    },
    {
      label: 'Dataset',
      href: `http://localhost:${pages}/dataset.html`,
      note: 'read the train and dev cases',
      start: startPages,
    },
    {
      label: 'Flow diagram',
      href: `http://localhost:${web}/systems/leasing-evals`,
      note: 'the eval process and the splits',
      start: 'pnpm dev',
    },
  ]
  const found: EvalLink[] = []
  for (const one of wanted) found.push({ ...one, status: await status($, one.href) })
  await update($, links, () => found)

  return found
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'eval-links',
      description: 'Local links to the eval review page, dataset and flow diagram',
    })

    return next(e)
  })

  on('command.run', { command: 'eval-links' }, async $ => {
    const list = await refresh($)
    await $.ui.open({ id: PANE, title: 'Eval links' })

    return {
      text: 'local pages\n' + list
        .map(one =>
          one.status === 'down'
            ? `- ${one.label}: not running. Start it with \`${one.start}\`, then open ${one.href}`
            : `- [${one.label}](${one.href}): ${one.note}`,
        )
        .join('\n'),
    }
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text, Link, Button } = $.ui.resolve(e)
    const list = await read($, links)

    return (
      <Box flexDirection="column">
        {list.length === 0 && <Text dimColor>Run /eval-links to load the links.</Text>}
        {list.map(one => (
          <Box flexDirection="column" marginBottom={1}>
            <Text>
              <Link href={one.href}>{one.label}</Link>
              <Text dimColor>{one.status === 'down' ? '  not running' : ''}</Text>
            </Text>
            <Text dimColor>{one.status === 'down' ? `start: ${one.start}` : one.note}</Text>
          </Box>
        ))}
        {list.length > 0 && <Button onPress={() => void refresh($)}>Check again</Button>}
      </Box>
    )
  })
}
