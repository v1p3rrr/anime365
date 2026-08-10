import {
  ActionIcon,
  Autocomplete,
  Badge,
  Button,
  Group,
  Stack,
  Text,
  Tooltip,
} from '@mantine/core'
import {
  IconArrowDown,
  IconArrowUp,
  IconCheck,
  IconPlus,
  IconRotate,
  IconTrash,
} from '@tabler/icons-react'
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { autodownloadApi, type AutoLang } from '../api/autodownload'
import { useT } from '../i18n'
import classes from './StripedRows.module.css'

interface Props {
  lang: AutoLang
  /** Нормализованные имена групп: выше — приоритетнее. */
  value: string[]
  onChange: (next: string[]) => void
  /** Список погашен переключателем «любая группа»: значения сохраняются. */
  disabled?: boolean
  /**
   * Порядок ничего не значит — прячем номера и стрелки.
   *
   * Так работает чёрный список: запрет либо есть, либо нет, и «первый
   * запрещённее второго» не значит ничего. Стрелки там были бы приглашением
   * к бессмысленному действию.
   */
  unordered?: boolean
}

/**
 * Упорядоченный список групп перевода.
 *
 * Порядок — это и есть смысл списка: первая строка приоритетнее второй, и
 * дальше по каскаду. Поэтому перемещение стрелками, а не перетаскиванием:
 * перетаскивание на телефоне конфликтует с прокруткой, а список короткий.
 *
 * Поле ввода принимает и выбор из подсказки, и произвольную строку. Второе
 * обязательно: каталог групп копится по мере открытия тайтлов, и нужной там
 * может ещё не быть — а пользователь про неё уже знает.
 */
export function AuthorPriorityList({
  lang,
  value,
  onChange,
  disabled,
  unordered,
}: Props) {
  const { t } = useT()
  const [draft, setDraft] = useState('')

  const suggestions = useQuery({
    queryKey: ['autodownload-authors', lang],
    queryFn: () => autodownloadApi.authors(lang),
    staleTime: 5 * 60 * 1000,
  })

  const known = suggestions.data?.authors ?? []
  // Ключи нормализованы сервером, поэтому сверяем по ним, а не по показанному
  // имени: «Wakanim» и «Wakanim [1080p]» — одна и та же строка списка.
  const chosen = new Set(value)

  const add = (name: string) => {
    const trimmed = name.trim()
    if (!trimmed) return
    const match = known.find(
      (item) => item.name.toLowerCase() === trimmed.toLowerCase(),
    )
    // Ключ знает сервер: своё приведение здесь разошлось бы с его правилами.
    // Для выбранного из подсказки берём готовый, для введённого руками —
    // отправляем как есть, сервер нормализует при сохранении.
    const key = match ? match.key : trimmed
    if (!chosen.has(key)) onChange([...value, key])
    setDraft('')
  }

  const move = (index: number, delta: number) => {
    const next = [...value]
    const target = index + delta
    if (target < 0 || target >= next.length) return
    ;[next[index], next[target]] = [next[target], next[index]]
    onChange(next)
  }

  const label = (key: string) =>
    known.find((item) => item.key === key)?.name ?? key

  return (
    <Stack gap={6}>
      <Group gap="xs" wrap="nowrap" align="flex-end">
        <Autocomplete
          style={{ flex: 1 }}
          disabled={disabled}
          label={t('autodownload.addGroup')}
          placeholder={t('autodownload.addGroupPlaceholder')}
          value={draft}
          onChange={setDraft}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              event.preventDefault()
              add(draft)
            }
          }}
          data={known.map((item) => item.name)}
          // Подсказка показывает всех, пока не начали печатать: список групп
          // и нужен для того, чтобы вспомнить название, а не подтвердить его.
          limit={50}
          renderOption={({ option }) => {
            const item = known.find((row) => row.name === option.value)
            const picked = item ? chosen.has(item.key) : false
            return (
              <Group gap="xs" wrap="nowrap" style={{ width: '100%' }}>
                {picked && <IconCheck size={14} />}
                <Text size="sm">{option.value}</Text>
                {item && (
                  <Badge size="xs" variant="light" ml="auto">
                    {item.seen}
                  </Badge>
                )}
              </Group>
            )
          }}
        />
        <Button
          disabled={disabled || draft.trim().length === 0}
          onClick={() => add(draft)}
          leftSection={<IconPlus size={16} />}
        >
          {t('autodownload.add')}
        </Button>
      </Group>

      {value.length === 0 ? (
        <Text size="xs" c="dimmed">
          {unordered ? t('autodownload.emptyBlacklist') : t('autodownload.emptyList')}
        </Text>
      ) : (
        <Stack gap={2} opacity={disabled ? 0.5 : 1}>
          {value.map((key, index) => (
            <Group key={key} className={classes.row} gap="xs" wrap="nowrap">
              {!unordered && (
                <Badge size="sm" variant="light" circle>
                  {index + 1}
                </Badge>
              )}
              <Text size="sm" style={{ flex: 1, wordBreak: 'break-word' }}>
                {label(key)}
              </Text>
              {!unordered && (
                <>
                  <ActionIcon
                    variant="subtle"
                    size="sm"
                    disabled={disabled || index === 0}
                    onClick={() => move(index, -1)}
                    aria-label={t('autodownload.moveUp')}
                  >
                    <IconArrowUp size={15} />
                  </ActionIcon>
                  <ActionIcon
                    variant="subtle"
                    size="sm"
                    disabled={disabled || index === value.length - 1}
                    onClick={() => move(index, 1)}
                    aria-label={t('autodownload.moveDown')}
                  >
                    <IconArrowDown size={15} />
                  </ActionIcon>
                </>
              )}
              <ActionIcon
                variant="subtle"
                size="sm"
                color="red"
                disabled={disabled}
                onClick={() => onChange(value.filter((item) => item !== key))}
                aria-label={t('autodownload.removeGroup')}
              >
                <IconTrash size={15} />
              </ActionIcon>
            </Group>
          ))}
        </Stack>
      )}

      {value.length > 0 && (
        <Group justify="flex-end">
          <Tooltip label={t('autodownload.resetListHint')}>
            <Button
              size="compact-xs"
              variant="subtle"
              color="red"
              disabled={disabled}
              leftSection={<IconRotate size={14} />}
              onClick={() => onChange([])}
            >
              {t('autodownload.resetList')}
            </Button>
          </Tooltip>
        </Group>
      )}
    </Stack>
  )
}
