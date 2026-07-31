import { useEffect } from 'react'
import { useBlocker } from 'react-router-dom'

/**
 * Не даёт уйти со страницы, потеряв несохранённые изменения.
 *
 * Закрывает два разных пути ухода, и оба нужны:
 *
 *  * переход внутри приложения, включая кнопки «назад» и «вперёд» браузера, —
 *    через `useBlocker`. Он работает только с data-роутером, ради этого
 *    роутинг и переведён на `createBrowserRouter` (см. `routes.tsx`).
 *
 *    ⚠️ Оговорка, проверенная на живой странице: «назад» перехватывается
 *    только для записей истории, **созданных роутером**. Внутри роутера он
 *    вычисляет `delta` и отматывает переход обратно; для чужой записи —
 *    например, если страницу перед этим перезагрузили целиком, — `delta`
 *    неизвестна, и блокировщик молча пропускает переход. Это поведение самой
 *    библиотеки, а не наша недоработка: в исходниках стоит явная проверка
 *    `blockerKey && delta != null`. На практике дыра невелика — уход с сайта
 *    и перезагрузку ловит `beforeunload` ниже;
 *  * закрытие вкладки и перезагрузку — через `beforeunload`. Свой текст туда
 *    подставить нельзя: браузеры показывают собственную фразу, чтобы страница
 *    не пугала выдуманным сообщением. Поэтому здесь только сам факт запроса.
 *
 * Возвращает объект блокировки, когда переход перехвачен: у него есть
 * `proceed()` — уйти и `reset()` — остаться. Решение принимает вызывающий,
 * потому что только он знает, можно ли ещё и сохранить.
 */
export function useUnsavedGuard(dirty: boolean) {
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      dirty && currentLocation.pathname !== nextLocation.pathname,
  )

  useEffect(() => {
    if (!dirty) return
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      // Старые браузеры ждали непустой returnValue; вреда от него нет.
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])

  return blocker
}
