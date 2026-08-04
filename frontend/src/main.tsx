import React from 'react'
import ReactDOM from 'react-dom/client'
import { MantineProvider } from '@mantine/core'
import { Notifications } from '@mantine/notifications'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider } from 'react-router-dom'

import '@mantine/core/styles.css'
import '@mantine/notifications/styles.css'

import { router } from './routes'
import { I18nProvider } from './i18n'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Каталог лежит в локальной БД и меняется по расписанию, а не постоянно —
      // перезапрашивать его при каждом фокусе окна незачем.
      refetchOnWindowFocus: false,
      staleTime: 60_000,
      // ⚠️ По умолчанию TanStack выбрасывает данные неиспользуемого запроса
      // через ПЯТЬ МИНУТ. Для нас это мало: человек уходит в очередь или в
      // библиотеку, возвращается через десять минут — и страница тайтла грузится
      // заново, с пустым экраном и ожиданием. Именно это и выглядело как
      // «кеша нет вообще».
      //
      // Полчаса — компромисс. Дольше держать незачем: каталог обновляется по
      // расписанию, а вкладку на сутки никто не оставляет. Память при этом не
      // разъедается: карточка весит около 400 байт, тысяча карточек и полсотни
      // тайтлов — это единицы мегабайт.
      gcTime: 30 * 60 * 1000,
      retry: 1,
    },
  },
})

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    {/* auto — следуем настройке системы, пока пользователь не выберет тему
        явно кнопкой в шапке. До первой отрисовки атрибут выставляет
        public/color-scheme.js, иначе тёмная тема мигала бы белым. */}
    <MantineProvider defaultColorScheme="auto">
      <Notifications />
      <QueryClientProvider client={queryClient}>
        <I18nProvider>
          <RouterProvider router={router} />
        </I18nProvider>
      </QueryClientProvider>
    </MantineProvider>
  </React.StrictMode>,
)
