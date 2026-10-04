import { useEffect, useState } from 'react'

export type StageEvent = {
  seq: number
  stage: string
  status: string
  detail?: string
  ts?: string
}

export function useCaseEvents(caseId: string | null) {
  const [events, setEvents] = useState<StageEvent[]>([])
  const [connected, setConnected] = useState(false)

  useEffect(() => {
    if (!caseId) return
    setEvents([])
    const source = new EventSource(`/api/cases/${caseId}/events`)

    const onEvent = (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data) as StageEvent
        setEvents((prev) =>
          prev.some((p) => p.seq === data.seq) ? prev : [...prev, data].sort((a, b) => a.seq - b.seq),
        )
      } catch {
        // ignore malformed event
      }
    }

    source.onopen = () => setConnected(true)
    source.onerror = () => setConnected(false)
    source.addEventListener('stage', onEvent)
    source.onmessage = onEvent

    return () => source.close()
  }, [caseId])

  return { events, connected }
}