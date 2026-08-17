import {
  Alert,
  Badge,
  Button,
  Card,
  Group,
  Loader,
  Menu,
  Stack,
  Text,
  Title,
} from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { IconChevronDown, IconInfoCircle, IconSearch } from '@tabler/icons-react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'

import { autodownloadApi, type AutoLang, type LangRule } from '../api/autodownload'
import { usePermissions } from '../hooks/usePermissions'
import { usePreferences } from '../stores/preferences'
import { useT } from '../i18n'
import { PosterThumb } from './PosterThumb'
import { AutoDownloadLangTabs } from './AutoDownloadLangTabs'
import { AutoDownloadMisses } from './AutoDownloadMisses'
import type { LangDraft } from './AutoDownloadLangPanel'
import classes from './StripedRows.module.css'

/** Какой язык субтитров стоит за каким типом перевода в настройках человека. */
const LANG_BY_TYPE: Record<string, AutoLang> = {
  sub_ru: 'ru',
  sub_en: 'en',
  sub_ja: 'ja',
}

/**
 * Языки, включённые у человека в настройках субтитров.
 *
 * Порядок фиксированный, а не порядок в настройках: иначе вкладки менялись бы
 * местами при правке фильтров каталога.
 */
export function useSubtitleLangs(): AutoLang[] {
  const { prefs } = usePreferences()
  const enabled = new Set(prefs.types.map((type) => LANG_BY_TYPE[type]).filter(Boolean))
  return (['ru', 'en', 'ja'] as AutoLang[]).filter((lang) => enabled.has(lang))
}

const EMPTY: LangDraft = {
  mode: 'off',
  priorities: [],
  blacklist: [],
  allow_any_fallback: false,
  allow_any: false,
  ignore_embedded: false,
}

/**
 * Вкладка «Автозагрузка» в библиотеке.
 *
 * Настройка раздельная по языкам — это решение пользователя: русские субтитры
 * можно хотеть всем подряд, английские только там, где включил сам, а японские
 * не качать вовсе.
 *
 * Сами вкладки языков вынесены в общий компонент: тот же самый стоит в окне на
 * странице тайтла. Две почти одинаковые копии пришлось бы чинить дважды.
 */
export function AutoDownloadTab() {
  const { t } = useT()
  const { can } = usePermissions()
  const queryClient = useQueryClient()
  const langs = useSubtitleLangs()

  const rules = useQuery({
    queryKey: ['autodownload-rules'],
    queryFn: autodownloadApi.rules,
  })

  const configured = useQuery({
    queryKey: ['autodownload-configured'],
    queryFn: autodownloadApi.configured,
  })

  const save = useMutation({
    mutationFn: ({ lang, value }: { lang: AutoLang; value: LangDraft }) =>
      autodownloadApi.setRule(lang, {
        mode: value.mode as LangRule['mode'],
        priorities: value.priorities,
        blacklist: value.blacklist,
        allow_any_fallback: Boolean(value.allow_any_fallback),
        allow_any: Boolean(value.allow_any),
        ignore_embedded: Boolean(value.ignore_embedded),
      }),
    onSuccess: () => {
      notifications.show({ color: 'teal', message: t('autodownload.saved') })
      queryClient.invalidateQueries({ queryKey: ['autodownload-rules'] })
    },
  })

  const run = useMutation({
    mutationFn: (onlyConfigured: boolean) => autodownloadApi.runAll(onlyConfigured),
    onSuccess: (result) => {
      notifications.show({
        color: result.skipped ? 'yellow' : 'teal',
        message: result.skipped
          ? t('autodownload.runOff')
          : t('autodownload.runDone', {
              planned: result.planned,
              queued: result.queued,
            }),
        autoClose: 8000,
      })
      queryClient.invalidateQueries({ queryKey: ['autodownload-misses'] })
    },
  })

  if (rules.isLoading) return <Loader size="sm" />

  return (
    <Stack gap="md">
      <Group justify="space-between" wrap="wrap" gap="xs">
        <Title order={4}>{t('autodownload.title')}</Title>
        {can('downloads') && (
          // Охват выбирается при нажатии, а не берётся из глобального режима:
          // язык может стоять в режиме «всем», но пройтись разово хочется лишь
          // по тем тайтлам, где приоритеты заданы руками.
          <Menu position="bottom-end">
            <Menu.Target>
              <Button
                variant="light"
                leftSection={<IconSearch size={16} />}
                rightSection={<IconChevronDown size={14} />}
                loading={run.isPending}
              >
                {t('autodownload.runAll')}
              </Button>
            </Menu.Target>
            <Menu.Dropdown>
              <Menu.Item onClick={() => run.mutate(false)}>
                {t('autodownload.runScopeAll')}
              </Menu.Item>
              <Menu.Item onClick={() => run.mutate(true)}>
                {t('autodownload.runScopeConfigured')}
              </Menu.Item>
            </Menu.Dropdown>
          </Menu>
        )}
      </Group>
      <Text size="xs" c="dimmed">
        {t('autodownload.runAllHint')}
      </Text>

      <AutoDownloadLangTabs
        langs={langs}
        saved={(lang) => ({ ...EMPTY, ...(rules.data?.rules[lang] ?? {}) })}
        onSave={(lang, value) => save.mutate({ lang, value })}
        saving={save.isPending}
        modes={rules.data?.modes ?? ['off', 'configured', 'all']}
        modePrefix="mode"
      />

      <AutoDownloadMisses />

      <Card withBorder padding="sm">
        <Title order={5} mb="xs">
          {t('autodownload.configuredTitle')}
        </Title>
        {configured.data?.series.length ? (
          <Stack gap={2}>
            {configured.data.series.map((row) => (
              <Group key={row.series_id} className={classes.row} gap="sm" wrap="nowrap">
                <PosterThumb url={row.poster_url} />
                <Text
                  size="sm"
                  component={Link}
                  to={`/series/${row.series_id}`}
                  style={{ flex: 1, wordBreak: 'break-word' }}
                >
                  {row.title_ru || row.title_romaji || `#${row.series_id}`}
                </Text>
                <Group gap={4} wrap="wrap">
                  {Object.entries(row.languages).map(([lang, info]) => (
                    <Badge
                      key={lang}
                      size="xs"
                      variant="light"
                      color={info.mode === 'off' ? 'red' : 'teal'}
                    >
                      {lang.toUpperCase()}
                      {info.has_own_list ? ` · ${t('autodownload.ownList')}` : ''}
                    </Badge>
                  ))}
                </Group>
              </Group>
            ))}
          </Stack>
        ) : (
          <Alert color="gray" icon={<IconInfoCircle size={16} />} p="xs">
            <Text size="xs">{t('autodownload.configuredEmpty')}</Text>
          </Alert>
        )}
      </Card>
    </Stack>
  )
}

export { LANG_BY_TYPE }
