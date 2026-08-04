import { createBrowserRouter } from 'react-router-dom'

import { App } from './App'
import { RequirePermission } from './components/RequirePermission'
import { CatalogPage } from './pages/CatalogPage'
import { ErrorPage } from './pages/ErrorPage'
import { LibraryPage } from './pages/LibraryPage'
import { QueuePage } from './pages/QueuePage'
import { SettingsPage } from './pages/SettingsPage'
import { TitlePage } from './pages/TitlePage'

/**
 * Маршруты отдельным деревом, а не JSX внутри шапки.
 *
 * Так требует data-роутер (`createBrowserRouter`), а он нужен ради `useBlocker`:
 * только с ним можно перехватить уход со страницы настроек с несохранёнными
 * изменениями. При обычном `BrowserRouter` хук недоступен, и кнопку «назад»
 * пришлось бы ловить руками через popstate — ненадёжно и с миганием адреса.
 *
 * `App` здесь — layout: рисует шапку и навигацию, а страницу подставляет через
 * `<Outlet />`.
 *
 * `RequirePermission` — на случай прихода по прямому адресу: пункт меню без
 * права уже спрятан, но закладку это не отменяет. Страница настроек не обёрнута
 * намеренно: там же живут личные настройки отображения и язык интерфейса,
 * нужные любому, — серверную часть она прячет сама.
 *
 * `errorElement` на корне, а не отдельным маршрутом `path: '*'`: так один экран
 * ловит и несовпавший адрес, и исключение из любой страницы, и рисуется вместо
 * всего дерева — без шапки и без запросов, которые до входа всё равно отдали бы
 * 401. Без него на опечатку в адресе показывалась отладочная страница роутера
 * («Hey developer 👋»), выглядящая как поломка приложения.
 */
export const router = createBrowserRouter([
  {
    path: '/',
    element: <App />,
    errorElement: <ErrorPage />,
    children: [
      {
        index: true,
        element: (
          <RequirePermission need="catalog">
            <CatalogPage />
          </RequirePermission>
        ),
      },
      {
        path: 'ongoing',
        element: (
          <RequirePermission need="catalog">
            <CatalogPage onlyAiring />
          </RequirePermission>
        ),
      },
      {
        path: 'series/:id',
        element: (
          <RequirePermission need="catalog">
            <TitlePage />
          </RequirePermission>
        ),
      },
      {
        path: 'queue',
        element: (
          <RequirePermission need="downloads">
            <QueuePage />
          </RequirePermission>
        ),
      },
      {
        path: 'library',
        element: (
          <RequirePermission need="catalog">
            <LibraryPage />
          </RequirePermission>
        ),
      },
      { path: 'settings', element: <SettingsPage /> },
    ],
  },
])
