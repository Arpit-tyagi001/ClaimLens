import { useState } from 'react'
import type { CaseStage } from './types'

const pause = (milliseconds: number) => new Promise((resolve) => window.setTimeout(resolve, milliseconds))

// Fixture-driven stage events for the standalone demo. Replace with SSE from M1.
export function useCaseEvents() {
  const [stages, setStages] = useState<CaseStage[]>([])
  const [running, setRunning] = useState(false)

  const play = async (sequence: CaseStage[]) => {
    setRunning(true)
    for (const stage of sequence) {
      await pause(420)
      setStages((current) => current.includes(stage) ? current : [...current, stage])
    }
    setRunning(false)
  }

  const extract = () => play(['UPLOADED', 'EXTRACTING', 'AWAITING_FACTS'])
  const analyze = () => play(['INVESTIGATING', 'VERIFYING', 'READY_FOR_REVIEW'])

  return { stages, running, extract, analyze }
}
