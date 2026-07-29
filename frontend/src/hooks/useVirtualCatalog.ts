/**
 * Загрузка карточек окнами поверх полного списка id.
 *
 * Полный список id приходит одним запросом (29 тысяч чисел — примерно 200 КБ), поэтому
 * виртуальный список сразу знает точную длину: полоса прокрутки правильная, можно прыгнуть
 * в любое место, а позиция при возврате со страницы тайтла восстанавливается по индексу.
 * Бесконечная догрузка этого не даёт и приносит дубли на границах страниц.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

import { catalogApi, type CatalogCard } from '../api/client'

const WINDOW_SIZE = 100

export function useVirtualCatalog(ids: number[]) {
  const [cards, setCards] = useState<Map<number, CatalogCard>>(new Map())
  // Окна, которые уже запрошены: иначе быстрый скролл выстрелит десяток одинаковых запросов.
  const requested = useRef<Set<number>>(new Set())

  // Смена фильтров means новый список id — весь накопленный кэш и пометки сбрасываем.
  useEffect(() => {
    setCards(new Map())
    requested.current = new Set()
  }, [ids])

  const ensureRange = useCallback(
    (startIndex: number, endIndex: number) => {
      const firstWindow = Math.floor(startIndex / WINDOW_SIZE)
      const lastWindow = Math.floor(endIndex / WINDOW_SIZE)

      for (let w = firstWindow; w <= lastWindow; w++) {
        if (requested.current.has(w)) continue
        const slice = ids.slice(w * WINDOW_SIZE, (w + 1) * WINDOW_SIZE)
        if (slice.length === 0) continue

        requested.current.add(w)
        catalogApi
          .cards(slice)
          .then((loaded) => {
            setCards((prev) => {
              const next = new Map(prev)
              for (const card of loaded) next.set(card.id, card)
              return next
            })
          })
          .catch(() => {
            // Разрешаем повторную попытку при следующем заходе в это окно.
            requested.current.delete(w)
          })
      }
    },
    [ids],
  )

  return { cards, ensureRange }
}
