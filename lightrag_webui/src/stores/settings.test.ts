import { describe, expect, test } from 'bun:test'

const readSettingsSource = () => Bun.file(new URL('./settings.ts', import.meta.url)).text()

describe('settings defaults', () => {
  test('enables graph edge events by default so edge properties can be selected', async () => {
    const source = await readSettingsSource()

    expect(source).toContain('enableEdgeEvents: true')
  })
})
