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
