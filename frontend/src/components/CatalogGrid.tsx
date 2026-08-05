import { useVirtualizer } from '@tanstack/react-virtual'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'

import type { CatalogCard as CardData } from '../api/client'
import { CatalogCard, type BadgeKey } from './CatalogCard'

interface Props {
  ids: number[]
  cards: Map<number, CardData>
  onRangeChange: (start: number, end: number) => void
  /** Какие значки наличия показывать — настройка пользователя из браузера. */
  visibleBadges: BadgeKey[]
  /** Есть ли что подгружать дальше. В режиме страниц всегда false. */
  hasMore?: boolean
  /** Просит следующую порцию. Вызывается при подходе к концу списка. */
  onLoadMore?: () => void
  /**
   * При смене значения прокрутка возвращается наверх.
   *
   * Нужно при переходе по номерам страниц: список меняется целиком, а позиция
   * прокрутки остаётся, и новая страница открывается с середины — выглядит так,
   * будто переход не сработал.
   */
  scrollResetKey?: string | number
}

// За сколько рядов до конца просить следующую порцию.
const LOAD_AHEAD_ROWS = 4

//: Ниже этой ширины карточка перестаёт читаться. 150 вместо прежних 168 —
//: ровно чтобы на телефоне помещались две штуки: при 375 пикселях экрана за
//: вычетом полей остаётся около 343, и прежний порог не дотягивал до двух
//: колонок буквально пары пикселей. По одному тайтлу в ряд листать невозможно.
const CARD_MIN_WIDTH = 150
const GAP = 12

//: Сколько колонок держим на узком экране, даже если формула даёт меньше.
//: Ниже 300 пикселей две карточки уже не читаются — там честная одна.
const MIN_COLUMNS_WIDTH = 300
// Постер 2:3 при ширине колонки + текстовый блок фиксированной высоты.
const cardHeight = (columnWidth: number) => Math.round((columnWidth * 3) / 2) + 76

/**
 * Виртуализированная сетка поверх полного списка id.
 *
 * Виртуализируем строки, а не отдельные карточки: число колонок зависит от ширины,
 * и построчный подход позволяет пересчитывать раскладку одним измерением контейнера.
 */
export function CatalogGrid({
  ids,
  cards,
  onRangeChange,
  visibleBadges,
  hasMore = false,
  onLoadMore,
  scrollResetKey,
}: Props) {
  const parentRef = useRef<HTMLDivElement>(null)
  const [columns, setColumns] = useState(1)
  const [columnWidth, setColumnWidth] = useState(CARD_MIN_WIDTH)

  useLayoutEffect(() => {
    const element = parentRef.current
    if (!element) return

    const recalc = () => {
      const width = element.clientWidth
      const fits = Math.floor((width + GAP) / (CARD_MIN_WIDTH + GAP))
      // На телефоне важнее охват, чем размер карточки: одна в ряд превращает
      // каталог в бесконечную ленту.
      const count = Math.max(width >= MIN_COLUMNS_WIDTH ? 2 : 1, fits)
      setColumns(count)
      setColumnWidth(Math.floor((width - GAP * (count - 1)) / count))
    }

    recalc()
    const observer = new ResizeObserver(recalc)
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  const rowCount = Math.ceil(ids.length / columns)
  const rowHeight = cardHeight(columnWidth) + GAP

  const virtualizer = useVirtualizer({
    count: rowCount,
    getScrollElement: () => parentRef.current,
    estimateSize: () => rowHeight,
    // Запас строк сверху и снизу, чтобы при быстрой прокрутке не мелькали заглушки.
    overscan: 3,
  })

  // Высота строки зависит от ширины колонки, а её измеряет ResizeObserver уже после
  // первого рендера. Виртуализатор кэширует размеры и сам про это не узнает: без
  // принудительного пересчёта он продолжает считать строки по старой высоте, из-за
  // чего рисует их меньше, чем помещается, и снизу остаётся пустая полоса.
  useEffect(() => {
    virtualizer.measure()
  }, [virtualizer, rowHeight])

  useEffect(() => {
    parentRef.current?.scrollTo({ top: 0 })
  }, [scrollResetKey])

  const virtualRows = virtualizer.getVirtualItems()

  useEffect(() => {
    if (virtualRows.length === 0) return
    const first = virtualRows[0].index * columns
    const last = (virtualRows[virtualRows.length - 1].index + 1) * columns - 1
    onRangeChange(first, Math.min(last, ids.length - 1))

    // Подгружаем заранее, а не по достижении самого низа: запрос успевает
    // вернуться, пока пользователь докручивает, и прокрутка не спотыкается.
    // Порог в строках, а не в карточках — на широком экране ряд длиннее.
    const rowsLeft = rowCount - 1 - virtualRows[virtualRows.length - 1].index
    if (hasMore && rowsLeft <= LOAD_AHEAD_ROWS) onLoadMore?.()
  }, [virtualRows, columns, ids.length, onRangeChange, rowCount, hasMore, onLoadMore])

  return (
    <div
      ref={parentRef}
      style={{
        // Занимаем всё оставшееся место в колонке-родителе. Фиксированная высота
        // вида calc(100vh - N) не годится: N зависит от высоты шапки и фильтров,
        // а она разная на разной ширине — снизу оставалась пустая полоса,
        // а последний ряд карточек обрезался.
        flex: 1,
        minHeight: 0,
        overflowY: 'auto',
        // Горизонтальный скролл недопустим: сетка обязана укладываться в ширину.
        overflowX: 'hidden',
        // Именно layout и paint, без size: strict включает size-containment,
        // из-за которого элемент перестаёт растягиваться по flex и снизу
        // остаётся пустая полоса.
        contain: 'layout paint',
      }}
    >
      <div style={{ height: virtualizer.getTotalSize(), position: 'relative', width: '100%' }}>
        {virtualRows.map((row) => {
          const startIndex = row.index * columns
          const rowIds = ids.slice(startIndex, startIndex + columns)

          return (
            <div
              key={row.key}
              style={{
                position: 'absolute',
                top: 0,
                left: 0,
                width: '100%',
                height: row.size,
                transform: `translateY(${row.start}px)`,
                display: 'grid',
                gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`,
                gap: GAP,
                paddingBottom: GAP,
              }}
            >
              {rowIds.map((id) => (
                <CatalogCard key={id} card={cards.get(id)} visibleBadges={visibleBadges} />
              ))}
            </div>
          )
        })}
      </div>
    </div>
  )
}
