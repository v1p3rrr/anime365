import {
  Alert,
  Badge,
  Button,
  Card,
  Group,
  Loader,
  NumberInput,
  PasswordInput,
  Select,
  Stack,
  Switch,
  Text,
  TextInput,
  Title,
  Tooltip,
} from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { IconAlertTriangle, IconCheck, IconDeviceFloppy, IconRotate } from '@tabler/icons-react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'

import { api } from '../api/client'
import { ErrorState } from '../components/ErrorState'
import scrollClasses from './ScrollPage.module.css'

interface SettingItem {
  key: string
  type: 'str' | 'int' | 'bool' | 'path' | 'enum' | 'str_list'
  group: string
  label: string
  help: string
  env: string
  /** Откуда взято действующее значение: из кода, из переменной окружения или отсюда. */
  source: 'default' | 'env' | 'ui'
  secret: boolean
  /** Меняется только на сервере: allowlist зеркал из браузера править нельзя. */
  server_only: boolean
  /** Замер по последнему реальному прогону, а не число, зашитое в справку. */
  hint: string | null
  choices: string[] | null
  value: unknown
  default?: unknown
  is_set?: boolean
}

interface SettingsResponse {
  groups: Array<{ key: string; label: string; settings: SettingItem[] }>
}

interface TokenCheck {
  ok: boolean
  logged_in?: boolean
  premium?: boolean
  name?: string
  premium_until?: string
  reason: string | null
}

const SOURCE_LABELS: Record<SettingItem['source'], string> = {
  default: 'по умолчанию',
  env: 'из переменной окружения',
  ui: 'изменено здесь',
}

const SOURCE_COLORS: Record<SettingItem['source'], string> = {
  default: 'gray',
  env: 'blue',
  ui: 'teal',
}

export function SettingsPage() {
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState<Record<string, string>>({})

  const query = useQuery({
    queryKey: ['settings'],
    queryFn: () => api.get<SettingsResponse>('/settings'),
  })

  // Правки сбрасываем при перезагрузке списка, иначе форма показывает старое.
  useEffect(() => {
    setDraft({})
  }, [query.dataUpdatedAt])

  const save = useMutation({
    mutationFn: () =>
      api.put<{ applied: string[]; errors: string[] }>('/settings', {
        updates: Object.entries(draft).map(([key, value]) => ({ key, value })),
      }),
    onSuccess: (result) => {
      if (result.errors.length > 0) {
        notifications.show({
          color: 'red',
          title: 'Часть настроек не сохранена',
          message: result.errors.join('; '),
        })
      } else {
        notifications.show({ color: 'teal', message: 'Настройки сохранены' })
      }
      queryClient.invalidateQueries({ queryKey: ['settings'] })
    },
    onError: (error: Error) =>
      notifications.show({ color: 'red', title: 'Не удалось сохранить', message: error.message }),
  })

  const reset = useMutation({
    mutationFn: (key: string) => api.post<unknown>(`/settings/reset/${key}`),
    onSuccess: () => {
      notifications.show({ color: 'teal', message: 'Значение сброшено' })
      queryClient.invalidateQueries({ queryKey: ['settings'] })
    },
  })

  const checkToken = useMutation({
    mutationFn: () => api.post<TokenCheck>('/settings/test-token'),
    onSuccess: (result) => {
      notifications.show({
        color: result.ok ? 'teal' : 'red',
        title: result.ok ? 'Токен рабочий' : 'Проблема с токеном',
        message: result.ok
          ? `${result.name}, подписка до ${result.premium_until}`
          : (result.reason ?? 'неизвестная причина'),
      })
    },
    onError: (error: Error) =>
      notifications.show({ color: 'red', title: 'Проверка не удалась', message: error.message }),
  })

  if (query.isLoading) {
    return (
      <Group justify="center" p="xl">
        <Loader />
      </Group>
    )
  }

  if (query.isError) {
    return (
      <ErrorState
        error={query.error}
        title="Не удалось загрузить настройки"
        onRetry={() => query.refetch()}
      />
    )
  }

  const hasChanges = Object.keys(draft).length > 0
  const passwordItem = query.data?.groups
    .flatMap((g) => g.settings)
    .find((s) => s.key === 'ui_password')

  return (
    <Stack gap="md" className={scrollClasses.scrollPage}>
      {/* wrap обязателен: на 375px заголовок и кнопка в одну строку не влезают,
          и кнопка уезжала за правый край. */}
      <Group justify="space-between" wrap="wrap" gap="xs">
        <Title order={3}>Настройки</Title>
        <Button
          leftSection={<IconDeviceFloppy size={16} />}
          onClick={() => save.mutate()}
          loading={save.isPending}
          disabled={!hasChanges}
        >
          Сохранить{hasChanges ? ` (${Object.keys(draft).length})` : ''}
        </Button>
      </Group>

      {passwordItem && !passwordItem.is_set && (
        <Alert color="red" title="Пароль не задан" icon={<IconAlertTriangle size={18} />}>
          Вход открыт всем, у кого есть доступ к порту. Через интерфейс доступен ваш
          премиум-токен и запись в файловую систему сервера — задайте пароль, прежде чем
          выставлять сервис в сеть.
        </Alert>
      )}

      <Alert color="gray" p="xs">
        <Text size="xs">
          Значение берётся из первого доступного источника: изменённое здесь, затем переменная
          окружения из docker-compose, затем значение по умолчанию. У каждой настройки видно,
          откуда взято действующее значение, и есть сброс.
        </Text>
      </Alert>

      {query.data?.groups.map((group) => (
        <Card key={group.key} withBorder padding="sm">
          <Group justify="space-between" mb="xs">
            <Text fw={600}>{group.label}</Text>
            {group.key === 'api' && (
              <Button
                size="compact-xs"
                variant="light"
                onClick={() => checkToken.mutate()}
                loading={checkToken.isPending}
              >
                Проверить токен
              </Button>
            )}
          </Group>

          <Stack gap="sm">
            {group.settings.map((item) => (
              <SettingField
                key={item.key}
                item={item}
                draftValue={draft[item.key]}
                onChange={(value) => setDraft((prev) => ({ ...prev, [item.key]: value }))}
                onReset={() => reset.mutate(item.key)}
              />
            ))}
          </Stack>
        </Card>
      ))}
    </Stack>
  )
}

interface FieldProps {
  item: SettingItem
  draftValue: string | undefined
  onChange: (value: string) => void
  onReset: () => void
}

function SettingField({ item, draftValue, onChange, onReset }: FieldProps) {
  const label = (
    // wrap, а не nowrap: на узком экране бейджи сжимались до «ПО УМОЛЧА…»
    // и переставали что-либо сообщать. Пусть лучше переносятся на строку ниже.
    <Group gap={6} wrap="wrap">
      <Text size="sm">{item.label}</Text>
      <Tooltip label={`Переменная окружения: ${item.env}`}>
        <Badge size="xs" variant="light" color={SOURCE_COLORS[item.source]}>
          {SOURCE_LABELS[item.source]}
        </Badge>
      </Tooltip>
      {item.server_only && (
        <Badge size="xs" variant="light" color="orange">
          только на сервере
        </Badge>
      )}
      {item.source === 'ui' && (
        <Tooltip label="Вернуть значение из переменной окружения или по умолчанию">
          <Button size="compact-xs" variant="subtle" color="gray" onClick={onReset}>
            <IconRotate size={12} />
          </Button>
        </Tooltip>
      )}
    </Group>
  )

  // Такое поле показываем, но не даём править: значение важно видеть (это
  // allowlist, по которому проверяется base_url), а менять его из браузера
  // нельзя по смыслу защиты.
  const locked = item.server_only

  // Справка статична, подсказка — замер по последнему прогону на этой машине.
  // Разделяем их визуально, чтобы было видно, где описание, а где факт.
  const description = item.hint ? (
    <>
      {item.help}
      {item.help && ' '}
      <Text component="span" size="xs" c="dimmed" fs="italic">
        {item.hint}
      </Text>
    </>
  ) : (
    item.help
  )

  if (item.type === 'bool') {
    const checked = draftValue !== undefined ? draftValue === 'true' : Boolean(item.value)
    return (
      <Switch
        label={label}
        description={description}
        checked={checked}
        disabled={locked}
        onChange={(event) => onChange(String(event.currentTarget.checked))}
      />
    )
  }

  if (item.secret) {
    return (
      <PasswordInput
        label={label}
        description={description}
        placeholder={item.is_set ? 'задано, введите новое чтобы изменить' : 'не задано'}
        value={draftValue ?? ''}
        disabled={locked}
        onChange={(event) => onChange(event.currentTarget.value)}
        rightSection={item.is_set ? <IconCheck size={14} color="teal" /> : undefined}
      />
    )
  }

  if (item.type === 'enum' && item.choices) {
    return (
      <Select
        label={label}
        description={description}
        data={item.choices}
        value={draftValue ?? String(item.value ?? '')}
        disabled={locked}
        onChange={(value) => onChange(value ?? '')}
        allowDeselect={false}
      />
    )
  }

  if (item.type === 'int') {
    return (
      <NumberInput
        label={label}
        description={description}
        value={draftValue !== undefined ? Number(draftValue) : Number(item.value ?? 0)}
        disabled={locked}
        onChange={(value) => onChange(String(value))}
      />
    )
  }

  // str_list показываем строкой через запятую — так же, как в переменной окружения.
  const current = Array.isArray(item.value) ? item.value.join(',') : String(item.value ?? '')
  return (
    <TextInput
      label={label}
      description={description}
      value={draftValue ?? current}
      disabled={locked}
      onChange={(event) => onChange(event.currentTarget.value)}
    />
  )
}
