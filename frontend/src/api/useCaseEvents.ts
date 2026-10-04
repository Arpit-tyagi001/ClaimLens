import { useEffect, useState } from 'react'

export type StageEvent = {
  seq: number
  stage: string
  status: string
  detail?: string
  ts?: string
}

export function useCaseEvents(caseId: string | null) {
  const [eventState, setEventState] = useState<{ caseId: string; events: StageEvent[] } | null>(null)
  const [connectedCaseId, setConnectedCaseId] = useState<string | null>(null)

  useEffect(() => {
    if (!caseId) return
    const source = new EventSource(`/api/cases/${caseId}/events`)

    const onEvent = (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data) as StageEvent
        setEventState((previous) => {
          const current = previous?.caseId === caseId ? previous.events : []
          if (current.some((event) => event.seq === data.seq)) return previous
          return { caseId, events: [...current, data].sort((a, b) => a.seq - b.seq) }
        })
      } catch {
        // ignore malformed event
      }
    }

    source.onopen = () => setConnectedCaseId(caseId)
    source.onerror = () => setConnectedCaseId((current) => current === caseId ? null : current)
    source.addEventListener('stage', onEvent)
    source.onmessage = onEvent

    return () => source.close()
  }, [caseId])

  const events = eventState?.caseId === caseId ? eventState.events : []
  return { events, connected: Boolean(caseId && connectedCaseId === caseId) }
}
