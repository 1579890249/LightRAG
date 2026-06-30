import { describe, expect, test } from 'bun:test'

const readAppRouterSource = () => Bun.file(new URL('./AppRouter.tsx', import.meta.url)).text()

describe('AppRouter standalone no-header routes', () => {
  test('exposes documents as a standalone route without the header', async () => {
    const source = await readAppRouterSource()

    expect(source).toContain('path="/documents"')
    expect(source).toContain('<App initialTab="documents" hideHeader />')
  })

  test('keeps the knowledge graph standalone route without the header', async () => {
    const source = await readAppRouterSource()

    expect(source).toContain('path="/knowledge-graph"')
    expect(source).toContain('<App initialTab="knowledge-graph" hideHeader />')
  })

  test('exposes retrieval as a standalone route without the header', async () => {
    const source = await readAppRouterSource()

    expect(source).toContain('path="/retrieval"')
    expect(source).toContain('<App initialTab="retrieval" hideHeader />')
  })
})
