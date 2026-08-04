/**
 * Загрузка карточек каталога окнами.
 *
 * ## Почему это лежит в кеше запросов, а не в состоянии компонента
 *
 * Раньше карточки копились в `useState<Map>`. Работало это ровно до ухода со
 * страницы: компонент размонтировался, и **все загруженные карточки исчезали**.
 * Возврат в каталог означал повторную загрузку каждого видимого окна с пустой
 * сеткой на это время. Пользователь так и описал: «перешёл на другую вкладку,
 * вернулся — опять долго грузится, а не кеш какой-нибудь».
 *
 * Это был не «нет кеша в браузере», а наша ошибка: половина данных каталога
 * просто не попадала в кеш, который у нас и так есть.
 *
 * Теперь каждое окно — обычный запрос. Ключ — **сами идентификаторы окна**, а не
 * его номер и не фильтры. Так выходит точнее: два разных набора фильтров,
 * давшие один и тот же кусок списка, делят кеш, а сдвиг выборки на одну позицию
 * честно считается другими данными.
 *
 * ## Почему окна накапливаются, а не пересоздаются
 *
 * Прокрутка добавляет окна в список; при смене выборки список сбрасывается.
 * Кеш при этом остаётся: вернувшись к прежним фильтрам, сетка получит карточки
 * из него мгновенно, без единого запроса.
 */

import { useQueries } from '@tanstack/react-query'
import { useCallback, useRef, useState } from 'react'

import { catalogApi, type CatalogCard } from '../api/client'

const WINDOW_SIZE = 100

/** Карточки меняются редко: правит их только синхронизация и наши же загрузки. */
const CARDS_STALE_TIME = 5 * 60 * 1000

export function useVirtualCatalog(ids: number[]) {
  const [windows, setWindows] = useState<number[]>([])
  const currentIds = useRef<number[]>(ids)

  // ⚠️ Сброс делается во время рендера, а НЕ в эффекте.
  // Эффекты дочерних компонентов выполняются раньше родительских, поэтому сетка
  // успевала запросить окно по новому списку, а эффект очистки затирал пометку
  // «окно запрошено». Карточки оставались пустыми до следующей прокрутки.
  if (currentIds.current !== ids) {
    currentIds.current = ids
    setWindows([])
  }

  const results = useQueries({
    queries: windows.map((index) => {
      const slice = ids.slice(index * WINDOW_SIZE, (index + 1) * WINDOW_SIZE)
      return {
        queryKey: ['catalog-cards', slice],
        queryFn: () => catalogApi.cards(slice),
        enabled: slice.length > 0,
        staleTime: CARDS_STALE_TIME,
      }
    }),
    // `combine` пересчитывается только при изменении результатов — без него
    // Map собиралась бы заново на каждый кадр прокрутки.
    combine: (queries) => {
      const cards = new Map<number, CatalogCard>()
      for (const query of queries) {
        for (const card of query.data ?? []) cards.set(card.id, card)
      }
      return { cards, isFetching: queries.some((query) => query.isFetching) }
    },
  })

  const ensureRange = useCallback((startIndex: number, endIndex: number) => {
    const first = Math.floor(startIndex / WINDOW_SIZE)
    const last = Math.floor(endIndex / WINDOW_SIZE)

    setWindows((prev) => {
      const missing: number[] = []
      for (let index = first; index <= last; index++) {
        if (!prev.includes(index)) missing.push(index)
      }
      // Возвращаем прежний массив, когда добавлять нечего: новый вызвал бы
      // перерисовку на каждом кадре прокрутки.
      return missing.length === 0 ? prev : [...prev, ...missing]
    })
  }, [])

  return { cards: results.cards, isFetching: results.isFetching, ensureRange }
}
