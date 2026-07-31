import { useCallback, useEffect, useRef } from 'react'

/**
 * Пишет фактическую ширину элемента в CSS-переменную на нём же.
 *
 * Нужно там, где потомок обязан быть шириной с видимую область, а не с
 * содержимое: внутри горизонтально прокручиваемой таблицы `width: 100%`
 * означает ширину таблицы, и растянутая на неё панель уезжает за край окна.
 * Выразить «ширина контейнера прокрутки» средствами CSS нельзя, поэтому
 * измеряем и отдаём числом.
 *
 * Возвращается callback-ref, а не объектный: страница сначала рисует состояние
 * загрузки, и к моменту единственного запуска эффекта нужного узла ещё нет —
 * замер молча не происходил бы вовсе. Callback-ref вызывается ровно тогда,
 * когда узел появляется в DOM, сколько бы раз это ни случилось.
 *
 * `clientWidth`, а не `getBoundingClientRect().width`: нужна ширина без
 * вертикальной полосы прокрутки, иначе панель окажется под ней.
 */
export function useVisibleWidthVar(cssVar: string) {
  const observerRef = useRef<ResizeObserver | null>(null)

  useEffect(() => {
    // Отключаем наблюдение при размонтировании: иначе оно переживёт страницу.
    return () => observerRef.current?.disconnect()
  }, [])

  return useCallback(
    (node: HTMLDivElement | null) => {
      observerRef.current?.disconnect()
      observerRef.current = null
      if (node === null) return

      const apply = () => node.style.setProperty(cssVar, `${node.clientWidth}px`)
      apply()

      // Ширина меняется не только от размера окна: сворачивание бокового меню
      // и появление вертикальной полосы прокрутки события `resize` не вызывают.
      if (typeof ResizeObserver === 'undefined') return
      observerRef.current = new ResizeObserver(apply)
      observerRef.current.observe(node)
    },
    [cssVar],
  )
}
