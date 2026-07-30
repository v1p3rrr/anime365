/**
 * Подписка на серверные события (SSE).
 *
 * Прогресс загрузки меняется несколько раз в секунду, и опрашивать сервер ради
 * него расточительно. EventSource переподключается сам, поэтому обработки
 * разрывов здесь нет — только закрытие при размонтировании.
 */

import { useEffect, useRef, useState } from 'react'

export interface ServerEvent {
  kind: string
  at: string
  [key: string]: unknown
}

type Handler = (event: ServerEvent) => void

export function useServerEvents(kinds: string[], onEvent: Handler): boolean {
  const [connected, setConnected] = useState(false)
  // Держим обработчик в ref: иначе смена колбэка пересоздавала бы соединение.
  const handlerRef = useRef(onEvent)
  handlerRef.current = onEvent

  const kindsKey = kinds.join(',')

  useEffect(() => {
    const source = new EventSource('/api/events')

    source.onopen = () => setConnected(true)
    source.onerror = () => setConnected(false)

    const listeners = kindsKey.split(',').filter(Boolean).map((kind) => {
      const listener = (event: MessageEvent) => {
        try {
          handlerRef.current(JSON.parse(event.data) as ServerEvent)
        } catch {
          // Битое сообщение не должно ронять подписку.
        }
      }
      source.addEventListener(kind, listener)
      return [kind, listener] as const
    })

    return () => {
      for (const [kind, listener] of listeners) source.removeEventListener(kind, listener)
      source.close()
    }
  }, [kindsKey])

  return connected
}
