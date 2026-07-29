import { AppShell, Group, NavLink, Text, Badge } from '@mantine/core'
import {
  IconLayoutGrid,
  IconDownload,
  IconFolders,
  IconSettings,
  IconBroadcast,
} from '@tabler/icons-react'
import { Link, Route, Routes, useLocation } from 'react-router-dom'

import { CatalogPage } from './pages/CatalogPage'
import { TitlePage } from './pages/TitlePage'
import { QueuePage } from './pages/QueuePage'
import { LibraryPage } from './pages/LibraryPage'
import { SettingsPage } from './pages/SettingsPage'

const NAV = [
  { to: '/', label: 'Каталог', icon: IconLayoutGrid },
  { to: '/ongoing', label: 'Онгоинги', icon: IconBroadcast },
  { to: '/queue', label: 'Очередь', icon: IconDownload },
  { to: '/library', label: 'Библиотека', icon: IconFolders },
  { to: '/settings', label: 'Настройки', icon: IconSettings },
]

export function App() {
  const { pathname } = useLocation()

  return (
    <AppShell header={{ height: 56 }} navbar={{ width: 220, breakpoint: 'sm' }} padding="md">
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between">
          <Text fw={600}>Anime365 Downloader</Text>
          <Badge variant="light" color="gray">
            v0.1.0
          </Badge>
        </Group>
      </AppShell.Header>

      <AppShell.Navbar p="xs">
        {NAV.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            component={Link}
            to={to}
            label={label}
            leftSection={<Icon size={18} />}
            active={pathname === to}
          />
        ))}
      </AppShell.Navbar>

      <AppShell.Main>
        <Routes>
          <Route path="/" element={<CatalogPage />} />
          <Route path="/ongoing" element={<CatalogPage onlyAiring />} />
          <Route path="/series/:id" element={<TitlePage />} />
          <Route path="/queue" element={<QueuePage />} />
          <Route path="/library" element={<LibraryPage />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Routes>
      </AppShell.Main>
    </AppShell>
  )
}
