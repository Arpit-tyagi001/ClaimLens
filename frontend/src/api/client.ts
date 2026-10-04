import { demoCase } from '../mocks/fixtures'
import type { CaseFixture, ReviewAction, ReviewLogEntry } from './types'

const MAX_FILE_BYTES = 10 * 1024 * 1024

export function validatePdf(file: File): string | null {
  const looksLikePdf = file.type === 'application/pdf' || file.name.toLowerCase().endsWith('.pdf')
  if (!looksLikePdf) return 'Choose a PDF file.'
  if (file.size > MAX_FILE_BYTES) return 'Each PDF must be 10 MB or smaller.'
  return null
}

// Mock adapter used until M1's OpenAPI contract and backend are available.
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
