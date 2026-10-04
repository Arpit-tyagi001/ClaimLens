import { demoCase } from '../mocks/fixtures'
import type { CaseFixture, ReviewAction, ReviewLogEntry } from './types'

const MAX_FILE_BYTES = 10 * 1024 * 1024

export function validatePdf(file: File): string | null {
  const looksLikePdf = file.type === 'application/pdf' || file.name.toLowerCase().endsWith('.pdf')
  if (!looksLikePdf) return 'Choose a PDF file.'
  if (file.size > MAX_FILE_BYTES) return 'Each PDF must be 10 MB or smaller.'
  return null
}

// Fixture adapter for case data and review actions whose backend endpoints are pending.
export const caseApi = {
  async getDemoCase(): Promise<CaseFixture> {
    return structuredClone(demoCase)
  },

  async saveReviewAction(findingId: string, action: ReviewAction, note = ''): Promise<ReviewLogEntry> {
    return {
      finding_id: findingId,
      action,
      note,
      timestamp: new Date().toISOString(),
    }
  },
}

export type UploadResult = { case_id: string; status: string }

export async function uploadCase(policy: File, letter: File): Promise<UploadResult> {
  const form = new FormData()
  form.append('policy', policy)
  form.append('letter', letter)

  const res = await fetch('/api/cases', { method: 'POST', body: form })

  if (!res.ok) {
    let message = `Upload failed (${res.status})`
    try {
      const body = await res.json()
      message = body?.error?.message ?? (typeof body?.detail === 'string' ? body.detail : message)
    } catch {
      // response was not JSON, keep the default message
    }
    throw new Error(message)
  }
  return res.json()
}
