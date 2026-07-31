import {
  ActionIcon,
  Menu,
  Tooltip,
  useComputedColorScheme,
  useMantineColorScheme,
  type MantineColorScheme,
} from '@mantine/core'
import {
  IconDeviceDesktop,
  IconMoon,
  IconSun,
  type Icon as TablerIcon,
} from '@tabler/icons-react'

import { useT, type TranslationKey } from '../i18n'

/**
 * Переключатель темы.
 *
 * Три значения, а не два: «как в системе» — это не то же самое, что светлая
 * или тёмная. Пользователь, у которого ОС сама переключается по времени суток,
 * при двухпозиционном тумблере теряет эту связь навсегда после первого клика.
 *
 * Выбор хранится в браузере, как язык и настройки отображения: сайт могут
 * открыть несколько человек, и тема — личное дело каждого. Ключ в localStorage
 * ставит сам Mantine, его же читает `public/color-scheme.js` до отрисовки.
 */
const OPTIONS: Array<{
  value: MantineColorScheme
  labelKey: TranslationKey
  icon: TablerIcon
}> = [
  { value: 'light', labelKey: 'nav.themeLight', icon: IconSun },
  { value: 'dark', labelKey: 'nav.themeDark', icon: IconMoon },
  { value: 'auto', labelKey: 'nav.themeAuto', icon: IconDeviceDesktop },
]

export function ThemeToggle() {
  const { t } = useT()
  const { colorScheme, setColorScheme } = useMantineColorScheme()
  // Что реально показано на экране: при `auto` это зависит от системы, и
  // иконка обязана показывать результат, а не слово «auto».
  const computed = useComputedColorScheme('dark')

  // Кнопка показывает действующую тему, а галочка в меню — выбранный режим.
  // Это разные вещи ровно в режиме «как в системе», и склеивать их нельзя.
  const CurrentIcon = computed === 'dark' ? IconMoon : IconSun

  return (
    <Menu position="bottom-end" withinPortal shadow="md">
      <Menu.Target>
        <Tooltip label={t('nav.theme')}>
          <ActionIcon variant="subtle" aria-label={t('nav.theme')}>
            <CurrentIcon size={18} />
          </ActionIcon>
        </Tooltip>
      </Menu.Target>
      <Menu.Dropdown>
        {OPTIONS.map(({ value, labelKey, icon: Icon }) => (
          <Menu.Item
            key={value}
            leftSection={<Icon size={16} />}
            onClick={() => setColorScheme(value)}
            // Выделяем выбранный режим цветом, а не галочкой справа: галочка
            // сдвигала бы ширину пунктов и заставляла меню дёргаться.
            c={colorScheme === value ? 'var(--mantine-primary-color-filled)' : undefined}
            fw={colorScheme === value ? 600 : undefined}
          >
            {t(labelKey)}
          </Menu.Item>
        ))}
      </Menu.Dropdown>
    </Menu>
  )
}
