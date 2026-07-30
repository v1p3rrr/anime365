import {
  ActionIcon,
  AppShell,
  Badge,
  Drawer,
  Group,
  Select,
  Text,
  Tooltip,
  UnstyledButton,
} from '@mantine/core'
import { useDisclosure, useLocalStorage, useMediaQuery } from '@mantine/hooks'
import {
  IconBroadcast,
  IconDownload,
  IconFolders,
  IconLayoutGrid,
  IconLayoutSidebar,
  IconLogout,
  IconSettings,
} from '@tabler/icons-react'
import { Link, Route, Routes, useLocation } from 'react-router-dom'

import { useAuth } from './hooks/useAuth'
import { LOCALES, useT, type Locale } from './i18n'
import { CatalogPage } from './pages/CatalogPage'
import { LibraryPage } from './pages/LibraryPage'
import { LoginPage } from './pages/LoginPage'
import { QueuePage } from './pages/QueuePage'
import { SettingsPage } from './pages/SettingsPage'
import { TitlePage } from './pages/TitlePage'
import classes from './App.module.css'

const NAV = [
  { to: '/', key: 'nav.catalog', icon: IconLayoutGrid },
  { to: '/ongoing', key: 'nav.ongoing', icon: IconBroadcast },
  { to: '/queue', key: 'nav.queue', icon: IconDownload },
  { to: '/library', key: 'nav.library', icon: IconFolders },
  { to: '/settings', key: 'nav.settings', icon: IconSettings },
] as const

const WIDE = 200
const NARROW = 56

export function App() {
  const { pathname } = useLocation()
  const auth = useAuth()
  const { t, locale, setLocale } = useT()
  // Свёрнуто по умолчанию: в узком окне развёрнутое меню съедало почти всю ширину.
  const [collapsed, setCollapsed] = useLocalStorage({
    key: 'a365.v1.navCollapsed',
    defaultValue: true,
    // Без этого значение читается отложенно, уже после первого рендера, и эффект
    // чтения затирает то, что успел выставить клик, — первое нажатие не срабатывало.
    getInitialValueInEffect: false,
  })
  // На мобильном меню не занимает место постоянно, а выезжает поверх контента.
  const isMobile = useMediaQuery('(max-width: 48em)')
  const [mobileOpened, { toggle: toggleMobile, close: closeMobile }] = useDisclosure(false)

  const width = collapsed ? NARROW : WIDE

  // Пока пароль не введён, приложения нет вовсе: иначе каждая страница успела бы
  // отправить свои запросы и собрать по 401 в уведомлениях.
  if (!auth.authenticated) {
    return <LoginPage onSuccess={auth.refresh} />
  }

  const navItems = (showLabels: boolean, onNavigate?: () => void) =>
    NAV.map(({ to, key, icon: Icon }) => {
      const label = t(key)
      const active = pathname === to
      const item = (
        <UnstyledButton
          key={to}
          component={Link}
          to={to}
          onClick={onNavigate}
          className={classes.navLink}
          data-active={active || undefined}
          data-collapsed={!showLabels || undefined}
        >
          <Icon size={19} stroke={1.6} style={{ flexShrink: 0 }} />
          {showLabels && (
            <Text size="sm" truncate>
              {label}
            </Text>
          )}
        </UnstyledButton>
      )

      // В свёрнутом виде остаются только иконки, поэтому подпись уходит в подсказку.
      return showLabels ? (
        item
      ) : (
        <Tooltip key={to} label={label} position="right">
          {item}
        </Tooltip>
      )
    })

  return (
    <AppShell
      header={{ height: 52 }}
      // На узкой ширине боковая панель полностью убирается, а меню открывается
      // отдельным Drawer с затемнением. Штатный мобильный режим AppShell выезжает
      // панелью без подложки, и рядом с ней остаётся виден каталог — выглядит так,
      // будто содержимое продублировалось.
      navbar={{ width, breakpoint: 'sm', collapsed: { mobile: true } }}
      padding="sm"
    >
      <AppShell.Header>
        <Group h="100%" px="sm" justify="space-between" wrap="nowrap">
          <Group gap="xs" wrap="nowrap" style={{ minWidth: 0 }}>
            <Tooltip
              label={
                isMobile
                  ? t('nav.menu')
                  : collapsed
                    ? t('nav.expandMenu')
                    : t('nav.collapseMenu')
              }
            >
              <ActionIcon
                variant="subtle"
                onClick={() => (isMobile ? toggleMobile() : setCollapsed((v) => !v))}
                aria-label={t('nav.toggleMenu')}
              >
                <IconLayoutSidebar size={18} />
              </ActionIcon>
            </Tooltip>
            <Text fw={600} size="sm" truncate>
              Anime365 Downloader
            </Text>
          </Group>
          <Group gap="xs" wrap="nowrap">
            <Badge variant="light" color="gray" visibleFrom="xs">
              v0.1.0
            </Badge>
            {/* Язык — личная настройка: сайт могут открыть несколько человек. */}
            <Tooltip label={t('nav.language')}>
              <Select
                size="xs"
                w={110}
                data={LOCALES}
                value={locale}
                onChange={(value) => value && setLocale(value as Locale)}
                allowDeselect={false}
                aria-label={t('nav.language')}
              />
            </Tooltip>
            {auth.passwordRequired && (
              <Tooltip label={t('nav.logout')}>
                <ActionIcon
                  variant="subtle"
                  aria-label={t('nav.logout')}
                  onClick={() => auth.logout().then(auth.refresh)}
                >
                  <IconLogout size={18} />
                </ActionIcon>
              </Tooltip>
            )}
          </Group>
        </Group>
      </AppShell.Header>

      <AppShell.Navbar p={4}>{navItems(!collapsed)}</AppShell.Navbar>

      <Drawer
        opened={isMobile === true && mobileOpened}
        onClose={closeMobile}
        size={WIDE}
        padding={4}
        withCloseButton={false}
        title={
          <Text fw={600} size="sm" px={6}>
            Anime365 Downloader
          </Text>
        }
        // Затемнение обязательно: без него сквозь меню видно каталог.
        overlayProps={{ backgroundOpacity: 0.55, blur: 2 }}
      >
        {navItems(true, closeMobile)}
      </Drawer>

      {/* Main занимает высоту окна, чтобы страницы могли растягивать содержимое,
          а не вычитать высоту шапки вручную. Отступ под шапку Mantine добавляет
          сам через padding, поэтому берём border-box. */}
      <AppShell.Main
        style={{
          boxSizing: 'border-box',
          height: '100dvh',
          display: 'flex',
          flexDirection: 'column',
          minHeight: 0,
        }}
      >
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
