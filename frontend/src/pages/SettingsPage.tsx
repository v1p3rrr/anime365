import {
  Alert,
  Badge,
  Button,
  Card,
  Group,
  Loader,
  Modal,
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
import { useT, type TranslationKey } from '../i18n'
import { useApiError } from '../i18n/apiError'
import { usePermissions } from '../hooks/usePermissions'
import { useUnsavedGuard } from '../hooks/useUnsavedGuard'
import scrollClasses from './ScrollPage.module.css'
import settingsClasses from './SettingsPage.module.css'
import { DisplaySettings } from '../components/DisplaySettings'
import { UsersPanel } from '../components/UsersPanel'

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
  /** Проверяли значение из формы, а не сохранённое на сервере. */
  checked_unsaved?: boolean
  reason: string | null
}

const SOURCE_COLORS: Record<SettingItem['source'], string> = {
  default: 'gray',
  env: 'blue',
  ui: 'teal',
}


/**
 * Подпись или пояснение настройки на языке интерфейса.
 *
 * Сервер отдаёт их только по-русски: реестр настроек живёт в коде бэкенда, и
 * держать там три языка значило бы разложить локализацию по двум местам.
 * Поэтому переводы лежат в словарях клиента по ключу настройки, а серверный
 * текст остаётся запасным вариантом — на случай, если настройку добавили, а
 * перевод ещё нет: русская фраза лучше пустоты.
 */
function useSettingText() {
  const { t } = useT()
  return (
    section: 'settingLabel' | 'settingHelp' | 'settingGroup',
    key: string,
    fallback: string,
  ) => {
    const full = `${section}.${key}` as TranslationKey
    const value = t(full)
    // t() возвращает сам ключ, когда перевода нет.
    return value === full ? fallback : value
  }
}

export function SettingsPage() {
  const queryClient = useQueryClient()
  const { t } = useT()
  const apiError = useApiError()
  const settingText = useSettingText()
  const { can, isAdmin, ready } = usePermissions()
  const [draft, setDraft] = useState<Record<string, string>>({})
  // Черновик пользователей живёт в своей панели, но уход со страницы один на
  // всех — иначе правки по пользователям пропадали бы без предупреждения.
  const [usersDirty, setUsersDirty] = useState(false)
  const hasChanges = Object.keys(draft).length > 0
  // ⚠️ Хуки объявляем до любых ранних return: ниже по функции есть выходы для
  // загрузки и для урезанного вида без прав, и хук после них вызывался бы не
  // на каждом рендере. React это ловит ошибкой #310 и роняет всю страницу.
  const guard = useUnsavedGuard(hasChanges || usersDirty)

  // Без права `settings` запрос не отправляется вовсе. Иначе страница показывала
  // бы ошибку «недостаточно прав» на месте блока, которого этому пользователю
  // и не полагается видеть, — отказ вместо отсутствия. Ждём ответа про права
  // (`ready`), потому что до него `can()` отвечает утвердительно.
  const allowed = ready && can('settings')
  const query = useQuery({
    queryKey: ['settings'],
    queryFn: () => api.get<SettingsResponse>('/settings'),
    enabled: allowed,
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
          title: t('settings.savePartial'),
          message: result.errors.join('; '),
        })
      } else {
        notifications.show({ color: 'teal', message: t('settings.saved') })
      }
      queryClient.invalidateQueries({ queryKey: ['settings'] })
    },
    onError: (error: Error) =>
      notifications.show({ color: 'red', title: t('settings.saveFailed'), message: apiError(error) }),
  })

  const reset = useMutation({
    mutationFn: (key: string) => api.post<unknown>(`/settings/reset/${key}`),
    onSuccess: () => {
      notifications.show({ color: 'teal', message: t('settings.resetDone') })
      queryClient.invalidateQueries({ queryKey: ['settings'] })
    },
  })

  const checkToken = useMutation({
    // Проверяем то, что сейчас в поле, а не только сохранённое: иначе
    // непроверенный токен пришлось бы сначала сохранить.
    mutationFn: () =>
      api.post<TokenCheck>('/settings/test-token', {
        token: draft.access_token?.trim() || null,
      }),
    onSuccess: (result) => {
      const suffix = result.checked_unsaved ? t('settings.tokenUnsaved') : ''
      notifications.show({
        color: result.ok ? 'teal' : 'red',
        title: result.ok ? t('settings.tokenOk') : t('settings.tokenBad'),
        message: result.ok
          ? t('settings.tokenOkMessage', {
              name: result.name ?? '',
              until: result.premium_until ?? '',
            }) + suffix
          : (result.reason ?? t('common.unknown')) + suffix,
        autoClose: result.ok ? 5000 : 12000,
      })
    },
    onError: (error: Error) =>
      notifications.show({
        color: 'red',
        title: t('settings.checkFailed'),
        message: apiError(error),
        autoClose: 12000,
      }),
  })

  const checkJellyfin = useMutation({
    mutationFn: () => api.post<{ ok: boolean; reason?: string; message?: string }>(
      '/settings/test-jellyfin',
    ),
    onSuccess: (result) =>
      notifications.show({
        color: result.ok ? 'teal' : 'red',
        title: result.ok ? t('settings.jellyfinOk') : t('settings.jellyfinBad'),
        message: result.ok ? (result.message ?? '') : (result.reason ?? t('common.unknown')),
        autoClose: result.ok ? 5000 : 12000,
      }),
    onError: (error: Error) =>
      notifications.show({
        color: 'red',
        title: t('settings.checkFailed'),
        message: apiError(error),
        autoClose: 12000,
      }),
  })

  if (!ready || query.isLoading) {
    return (
      <Group justify="center" p="xl">
        <Loader />
      </Group>
    )
  }

  if (query.isError) {
    // Личные настройки показываем и здесь: они лежат в браузере и от реестра
    // на сервере не зависят. Отбирать к ним доступ из-за чужой поломки незачем.
    return (
      <Stack gap="md" className={scrollClasses.scrollPage}>
        <ErrorState
          error={query.error}
          title={t('settings.loadFailed')}
          onRetry={() => query.refetch()}
        />
        <DisplaySettings />
      </Stack>
    )
  }

  const passwordItem = query.data?.groups
    .flatMap((g) => g.settings)
    .find((s) => s.key === 'ui_password')

  return (
    <Stack gap="md" className={scrollClasses.scrollPage}>
      {/* wrap обязателен: на 375px заголовок и кнопка в одну строку не влезают,
          и кнопка уезжала за правый край. */}
      <Group
        justify="space-between"
        wrap="wrap"
        gap="xs"
        className={settingsClasses.stickyHeader}
      >
        <Title order={3}>{t('settings.title')}</Title>
        {/* Кнопка сохранения принадлежит серверным настройкам: личные
            применяются сразу, пользователей применяет своя панель. Без права
            на серверные сохранять здесь нечего — и кнопки быть не должно. */}
        {allowed && (
          <Button
            leftSection={<IconDeviceFloppy size={16} />}
            onClick={() => save.mutate()}
            loading={save.isPending}
            disabled={!hasChanges}
          >
            {hasChanges ? t('settings.saveCount', { count: Object.keys(draft).length }) : t('common.save')}
          </Button>
        )}
      </Group>

      {/* Уход с несохранёнными правками. Три исхода, а не два: «сохранить и
          уйти» — то, чего пользователь чаще всего и хотел, и без этой кнопки
          ему пришлось бы остаться, нажать сохранение и повторить переход. */}
      <Modal
        opened={guard.state === 'blocked'}
        onClose={() => guard.reset?.()}
        title={t('settings.leaveTitle')}
        centered
      >
        <Stack gap="sm">
          <Text size="sm">
            {hasChanges
              ? t('settings.leaveHint', { count: Object.keys(draft).length })
              : t('settings.leaveHintUsers')}
          </Text>
          <Group justify="flex-end">
            <Button variant="default" onClick={() => guard.reset?.()}>
              {t('settings.leaveStay')}
            </Button>
            <Button color="red" variant="light" onClick={() => guard.proceed?.()}>
              {t('settings.leaveDiscard')}
            </Button>
            {/* Сохранить отсюда можно только серверные настройки: черновик
                пользователей применяет своя панель, и делать вид, что кнопка
                сохранит и его, значило бы соврать. */}
            {hasChanges && (
            <Button
              loading={save.isPending}
              onClick={() =>
                save.mutateAsync().then(
                  () => guard.proceed?.(),
                  // Не ушли — значит сохранение не прошло, и уходить нельзя:
                  // иначе правки потерялись бы молча. Текст ошибки покажет
                  // onError самой мутации.
                  () => undefined,
                )
              }
            >
              {t('settings.leaveSave')}
            </Button>
            )}
          </Group>
        </Stack>
      </Modal>

      {passwordItem && !passwordItem.is_set && (
        <Alert color="red" title={t('settings.noPassword')} icon={<IconAlertTriangle size={18} />}>
          {t('settings.noPasswordHint')}
        </Alert>
      )}

      {/* Про три слоя значений — справка к серверным настройкам. Без них
          она объясняла бы то, чего на странице нет. */}
      {allowed && (
        <Alert color="gray" p="xs">
          <Text size="xs">
            {t('settings.layersHint')}
          </Text>
        </Alert>
      )}

      {/* Личные настройки идут первыми: их меняют чаще всего, они применяются
          сразу и не участвуют в общем сохранении. */}
      <DisplaySettings />

      {/* Пользователи — после личных настроек, но до серверных: заводить их
          приходится редко, а серверные настройки длиннее и оттеснили бы форму
          вниз, где её никто не найдёт.

          Панель только для администратора: `/api/users` требует именно его, и
          обычному пользователю она отвечала бы отказом на каждый рендер. */}
      {isAdmin && <UsersPanel onDirtyChange={setUsersDirty} />}

      {/* Серверные настройки — под правом. Личные (язык, отображение) выше
          и остаются всем: они хранятся в браузере и никого не касаются. */}
      {allowed &&
        query.data?.groups.map((group) => (
        <Card key={group.key} withBorder padding="sm">
          <Group justify="space-between" mb="xs">
            <Text fw={600}>{settingText('settingGroup', group.key, group.label)}</Text>
            {group.key === 'features' && (
              <Button
                size="compact-xs"
                variant="light"
                onClick={() => checkJellyfin.mutate()}
                loading={checkJellyfin.isPending}
              >
                {t('settings.testJellyfin')}
              </Button>
            )}
            {group.key === 'api' && (
              <Button
                size="compact-xs"
                variant="light"
                onClick={() => checkToken.mutate()}
                loading={checkToken.isPending}
              >
                {t('settings.testToken')}
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

/** «default» → «Default»: ключи словаря именованы sourceDefault/sourceEnv/sourceUi. */
function sourceSuffix(source: SettingItem['source']): string {
  return source.charAt(0).toUpperCase() + source.slice(1)
}

function SettingField({ item, draftValue, onChange, onReset }: FieldProps) {
  const { t } = useT()
  const settingText = useSettingText()
  const label = (
    // wrap, а не nowrap: на узком экране бейджи сжимались до «ПО УМОЛЧА…»
    // и переставали что-либо сообщать. Пусть лучше переносятся на строку ниже.
    <Group gap={6} wrap="wrap">
      <Text size="sm">{settingText('settingLabel', item.key, item.label)}</Text>
      <Tooltip label={t('settings.envVar', { name: item.env })}>
        <Badge size="xs" variant="light" color={SOURCE_COLORS[item.source]}>
          {t(`settings.source${sourceSuffix(item.source)}` as TranslationKey)}
        </Badge>
      </Tooltip>
      {item.server_only && (
        <Badge size="xs" variant="light" color="orange">
          {t('settings.serverOnly')}
        </Badge>
      )}
      {/* Индикатор «значение задано» живёт в подписи, а не в rightSection поля:
          там его держит сам Mantine под кнопку показать/скрыть, и подменять её
          нельзя — иначе секретное поле остаётся в последнем состоянии навсегда. */}
      {item.secret && (
        <Badge
          size="xs"
          variant="light"
          color={item.is_set ? 'teal' : 'gray'}
          leftSection={item.is_set ? <IconCheck size={10} /> : undefined}
        >
          {item.is_set ? t('settings.isSet') : t('settings.notSet')}
        </Badge>
      )}
      {item.source === 'ui' && (
        <Tooltip label={t('settings.resetHint')}>
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
      {settingText('settingHelp', item.key, item.help)}
      {item.help && ' '}
      <Text component="span" size="xs" c="dimmed" fs="italic">
        {item.hint}
      </Text>
    </>
  ) : (
    settingText('settingHelp', item.key, item.help)
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
        // Плейсхолдер обязан различать «на сервере пусто» и «значение есть,
        // но показать его нельзя»: иначе непонятно, надо ли вообще что-то вводить.
        placeholder={
          item.is_set
            ? t('settings.secretPlaceholderSet')
            : t('settings.secretPlaceholderEmpty')
        }
        value={draftValue ?? ''}
        disabled={locked}
        onChange={(event) => onChange(event.currentTarget.value)}
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
