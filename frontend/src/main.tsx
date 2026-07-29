import React from 'react'
import ReactDOM from 'react-dom/client'
import { MantineProvider } from '@mantine/core'
import { Notifications } from '@mantine/notifications'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter } from 'react-router-dom'

import '@mantine/core/styles.css'
import '@mantine/notifications/styles.css'

import { App } from './App'

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
    <MantineProvider defaultColorScheme="dark">
      <Notifications />
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </QueryClientProvider>
    </MantineProvider>
  </React.StrictMode>,
)
