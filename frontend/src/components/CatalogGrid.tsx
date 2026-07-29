import { useVirtualizer } from '@tanstack/react-virtual'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'

import type { CatalogCard as CardData } from '../api/client'
import { CatalogCard } from './CatalogCard'

interface Props {
  ids: number[]
  cards: Map<number, CardData>
  onRangeChange: (start: number, end: number) => void
}

const CARD_MIN_WIDTH = 168
const GAP = 12
// Постер 2:3 при ширине колонки + текстовый блок фиксированной высоты.
const cardHeight = (columnWidth: number) => Math.round((columnWidth * 3) / 2) + 76

/**
 * Виртуализированная сетка поверх полного списка id.
 *
 * Виртуализируем строки, а не отдельные карточки: число колонок зависит от ширины,
 * и построчный подход позволяет пересчитывать раскладку одним измерением контейнера.
 */
export function CatalogGrid({ ids, cards, onRangeChange }: Props) {
  const parentRef = useRef<HTMLDivElement>(null)
  const [columns, setColumns] = useState(1)
  const [columnWidth, setColumnWidth] = useState(CARD_MIN_WIDTH)

  useLayoutEffect(() => {
    const element = parentRef.current
    if (!element) return

    const recalc = () => {
      const width = element.clientWidth
      const count = Math.max(1, Math.floor((width + GAP) / (CARD_MIN_WIDTH + GAP)))
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

  const virtualRows = virtualizer.getVirtualItems()

  useEffect(() => {
    if (virtualRows.length === 0) return
    const first = virtualRows[0].index * columns
    const last = (virtualRows[virtualRows.length - 1].index + 1) * columns - 1
    onRangeChange(first, Math.min(last, ids.length - 1))
  }, [virtualRows, columns, ids.length, onRangeChange])

  return (
    <div
      ref={parentRef}
      style={{
        height: 'calc(100vh - 210px)',
        overflowY: 'auto',
        // Горизонтальный скролл недопустим: сетка обязана укладываться в ширину.
        overflowX: 'hidden',
        contain: 'strict',
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
                <CatalogCard key={id} card={cards.get(id)} />
              ))}
            </div>
          )
        })}
      </div>
    </div>
  )
}
