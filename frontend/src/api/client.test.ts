import { afterEach, describe, expect, it, vi } from 'vitest'

import { api } from './client'

describe('import settings requests', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('bypasses browser caches when checking AI configuration', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ llm_configured: true }),
    })
    vi.stubGlobal('fetch', fetchMock)

    await api.getImportSettings()

    expect(fetchMock).toHaveBeenCalledOnce()
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ cache: 'no-store' })
  })
})
