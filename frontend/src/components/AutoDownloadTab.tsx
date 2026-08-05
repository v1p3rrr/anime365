import {
  Alert,
  Badge,
  Button,
  Card,
  Group,
  Loader,
  Stack,
  Tabs,
  Text,
  Title,
} from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { IconInfoCircle, IconSearch } from '@tabler/icons-react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'

import { autodownloadApi, type AutoLang, type LangRule } from '../api/autodownload'
import { usePermissions } from '../hooks/usePermissions'
import { usePreferences } from '../stores/preferences'
import { useT } from '../i18n'
import { Flag } from './Flag'
import { PosterThumb } from './PosterThumb'
import { AutoDownloadLangPanel, type LangDraft } from './AutoDownloadLangPanel'
import classes from './StripedRows.module.css'

/** Какой язык субтитров стоит за каким типом перевода в настройках человека. */
const LANG_BY_TYPE: Record<string, AutoLang> = {
  sub_ru: 'ru',
  sub_en: 'en',
  sub_ja: 'ja',
}

function useSubtitleLangs(): AutoLang[] {
  // Языки берём из пользовательских настроек: показывать вкладку языка,
  // который человек у себя выключил, значит предлагать настроить то, чего он
  // видеть не хочет. Порядок фиксированный, а не порядок в настройках, —
  // иначе вкладки менялись бы местами при правке фильтров каталога.
  const { prefs } = usePreferences()
  const enabled = new Set(prefs.types.map((type) => LANG_BY_TYPE[type]).filter(Boolean))
  return (['ru', 'en', 'ja'] as AutoLang[]).filter((lang) => enabled.has(lang))
}

const EMPTY: LangRule = {
  mode: 'off',
  priorities: [],
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
 */
export function AutoDownloadTab() {
  const { t } = useT()
  const { can } = usePermissions()
  const queryClient = useQueryClient()
  const langs = useSubtitleLangs()
  const [active, setActive] = useState<AutoLang>(langs[0] ?? 'ru')
  const [draft, setDraft] = useState<Partial<Record<AutoLang, LangDraft>>>({})

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
    mutationFn: autodownloadApi.runAll,
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
    },
  })

  if (rules.isLoading) return <Loader size="sm" />
  if (langs.length === 0) {
    return <Alert color="blue">{t('autodownload.noLanguages')}</Alert>
  }

  const current = draft[active] ?? {
    ...(rules.data?.rules[active] ?? EMPTY),
  }

  return (
    <Stack gap="md">
      <Group justify="space-between" wrap="wrap" gap="xs">
        <Title order={4}>{t('autodownload.title')}</Title>
        {can('downloads') && (
          <Button
            variant="light"
            leftSection={<IconSearch size={16} />}
            loading={run.isPending}
            onClick={() => run.mutate()}
          >
            {t('autodownload.runAll')}
          </Button>
        )}
      </Group>
      <Text size="xs" c="dimmed">
        {t('autodownload.runAllHint')}
      </Text>

      <Tabs value={active} onChange={(value) => value && setActive(value as AutoLang)}>
        <Tabs.List>
          {langs.map((lang) => (
            <Tabs.Tab key={lang} value={lang} leftSection={<Flag lang={lang} />}>
              {lang.toUpperCase()}
            </Tabs.Tab>
          ))}
        </Tabs.List>

        <Tabs.Panel value={active} pt="sm">
          <AutoDownloadLangPanel
            lang={active}
            value={current}
            modes={rules.data?.modes ?? ['off', 'configured', 'all']}
            modePrefix="mode"
            onChange={(next) => setDraft({ ...draft, [active]: next })}
          />
          <Group justify="flex-end" mt="sm">
            <Button
              loading={save.isPending}
              disabled={!draft[active]}
              onClick={() => save.mutate({ lang: active, value: current })}
            >
              {t('common.save')}
            </Button>
          </Group>
        </Tabs.Panel>
      </Tabs>

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
                  to={`/title/${row.series_id}`}
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
