import { createBrowserRouter } from 'react-router-dom'

import { App } from './App'
import { CatalogPage } from './pages/CatalogPage'
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
 */
export const router = createBrowserRouter([
  {
    path: '/',
    element: <App />,
    children: [
      { index: true, element: <CatalogPage /> },
      { path: 'ongoing', element: <CatalogPage onlyAiring /> },
      { path: 'series/:id', element: <TitlePage /> },
      { path: 'queue', element: <QueuePage /> },
      { path: 'library', element: <LibraryPage /> },
      { path: 'settings', element: <SettingsPage /> },
    ],
  },
])
