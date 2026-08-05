import {
  ActionIcon,
  AppShell,
  Badge,
  Drawer,
  Group,
  Loader,
  Select,
  Stack,
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
import { useQueryClient } from '@tanstack/react-query'
import { Link, Outlet, useLocation } from 'react-router-dom'

import { Logo } from './components/Logo'
import { ThemeToggle } from './components/ThemeToggle'
import { useAuth } from './hooks/useAuth'
import { usePermissions } from './hooks/usePermissions'
import { LOCALES, useT, type Locale } from './i18n'
import { LoginPage } from './pages/LoginPage'
import { MustChangePasswordPage } from './pages/MustChangePasswordPage'
import classes from './App.module.css'

// `need` — право, без которого раздела не видно вовсе. Прятать, а не
// показывать неработающим: кнопка, отвечающая отказом, читается как поломка.
//
// Настройки видны и без права `settings`: там же лежат личные настройки
// отображения и язык интерфейса, которые нужны любому. Серверную часть
// страница прячет сама.
const NAV = [
  { to: '/', key: 'nav.catalog', icon: IconLayoutGrid, need: 'catalog' },
  { to: '/ongoing', key: 'nav.ongoing', icon: IconBroadcast, need: 'catalog' },
  { to: '/queue', key: 'nav.queue', icon: IconDownload, need: 'downloads' },
  { to: '/library', key: 'nav.library', icon: IconFolders, need: 'catalog' },
  { to: '/settings', key: 'nav.settings', icon: IconSettings, need: null },
] as const

const WIDE = 200
const NARROW = 56

export function App() {
  const { pathname } = useLocation()
  const auth = useAuth()
  const { t, locale, setLocale } = useT()
  const { can, me } = usePermissions()
  const queryClient = useQueryClient()
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

  // Три состояния, а не два. Пока статус входа не пришёл, не показываем ни
  // приложение, ни форму: приложение мигало бы у тех, кто не вошёл, а форма —
  // у тех, у кого пароля нет вовсе.
  //
  // Мигание приложения не выдавало данных (сервер отказывает каждому запросу),
  // но выдавало устройство интерфейса и порождало пачку заведомо обречённых
  // запросов. Показывать неизвестному то, что ему не предназначено, не стоит
  // даже пустым.
  if (auth.isLoading) {
    return (
      <Stack align="center" justify="center" style={{ height: '100dvh' }}>
        <Logo height={44} />
        <Loader size="sm" />
      </Stack>
    )
  }

  // Пока пароль не введён, приложения нет вовсе: иначе каждая страница успела бы
  // отправить свои запросы и собрать по 401 в уведомлениях.
  if (!auth.authenticated) {
    return <LoginPage onSuccess={auth.refresh} />
  }

  // Вошли с временным паролём — приложения тоже нет. Это не назидание:
  // сервер в таком состоянии отвечает отказом почти на весь API, и показать
  // меню значило бы предложить полтора десятка заведомо обречённых страниц.
  if (me?.must_change_password) {
    return <MustChangePasswordPage onDone={() => queryClient.invalidateQueries({ queryKey: ['me'] })} />
  }

  const navItems = (showLabels: boolean, onNavigate?: () => void) =>
    NAV.filter((item) => item.need === null || can(item.need)).map(({ to, key, icon: Icon }) => {
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
      header={{ height: 60 }}
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
            {/* Логотип — ссылка в каталог. Привычка общая для всех сайтов, и
                ждать её человек будет независимо от того, есть ли пункт меню. */}
            <UnstyledButton component={Link} to="/" aria-label={t('nav.catalog')}>
              <Logo height={40} />
            </UnstyledButton>
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
            <ThemeToggle />
            {auth.passwordRequired && (
              <Tooltip label={t('nav.logout')}>
                <ActionIcon
                  variant="subtle"
                  aria-label={t('nav.logout')}
                  // Только выход. `refresh()` здесь стоял зря и вредил: он
                  // снимает признак «вышли», и до ответа сервера приложение
                  // снова считалось живым — страницы успевали отправить свои
                  // запросы и собрать по 401 вместо формы входа.
                  onClick={() => auth.logout()}
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
          <UnstyledButton
            component={Link}
            to="/"
            onClick={closeMobile}
            aria-label={t('nav.catalog')}
          >
            <Logo height={30} />
          </UnstyledButton>
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
        {/* Страницы подставляет роутер: маршруты объявлены в routes.tsx,
            потому что data-роутер требует их деревом, а не JSX внутри шапки. */}
        <Outlet />
      </AppShell.Main>
    </AppShell>
  )
}
