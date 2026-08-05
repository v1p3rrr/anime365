import { Alert, Button, Group, Loader, Modal, Stack, Tabs, Text } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { IconSearch } from '@tabler/icons-react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { autodownloadApi, type AutoLang } from '../api/autodownload'
import { useT } from '../i18n'
import { Flag } from './Flag'
import { AutoDownloadLangPanel, type LangDraft } from './AutoDownloadLangPanel'

interface Props {
  seriesId: number
  title: string
  opened: boolean
  onClose: () => void
  /** Языки субтитров, включённые в настройках человека. */
  langs: AutoLang[]
}

const BLANK: LangDraft = {
  mode: 'default',
  priorities: [],
  allow_any_fallback: null,
  allow_any: null,
  ignore_embedded: null,
}

/**
 * Настройка автоскачивания для одного тайтла.
 *
 * Та же панель, что и в общих настройках, — разница только в наборе режимов и
 * в том, что переключатели трёхзначные: у тайтла есть состояние «как в общих
 * настройках», и без него его было бы не отличить от «выключено».
 *
 * Пустой список означает «взять общий», а не «никого»: убрал свой список —
 * вернулась общая логика.
 */
export function TitleAutoDownloadModal({ seriesId, title, opened, onClose, langs }: Props) {
  const { t } = useT()
  const queryClient = useQueryClient()
  const [active, setActive] = useState<AutoLang>(langs[0] ?? 'ru')
  const [draft, setDraft] = useState<Partial<Record<AutoLang, LangDraft>>>({})

  const settings = useQuery({
    queryKey: ['autodownload-series', seriesId],
    queryFn: () => autodownloadApi.series(seriesId),
    enabled: opened,
  })

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['autodownload-series', seriesId] })
    queryClient.invalidateQueries({ queryKey: ['autodownload-configured'] })
  }

  const save = useMutation({
    mutationFn: ({ lang, value }: { lang: AutoLang; value: LangDraft }) =>
      autodownloadApi.setSeries(seriesId, lang, {
        mode: value.mode as 'default' | 'on' | 'off',
        priorities: value.priorities,
        allow_any_fallback: value.allow_any_fallback,
        allow_any: value.allow_any,
        ignore_embedded: value.ignore_embedded,
      }),
    onSuccess: () => {
      notifications.show({ color: 'teal', message: t('autodownload.saved') })
      setDraft({})
      invalidate()
    },
  })

  const clear = useMutation({
    mutationFn: (lang: AutoLang) => autodownloadApi.clearSeries(seriesId, lang),
    onSuccess: () => {
      setDraft({})
      invalidate()
    },
  })

  const run = useMutation({
    mutationFn: () => autodownloadApi.runSeries(seriesId),
    onSuccess: (result) =>
      notifications.show({
        color: 'teal',
        message: t('autodownload.runDone', {
          planned: result.planned,
          queued: result.queued,
        }),
      }),
  })

  const own = settings.data?.own ?? {}
  const current = draft[active] ?? { ...BLANK, ...(own[active] ?? {}) }
  const effective = settings.data?.effective?.[active]

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title={t('autodownload.settingsFor', { title })}
      size="lg"
      centered
    >
      {settings.isLoading ? (
        <Loader size="sm" />
      ) : langs.length === 0 ? (
        <Alert color="blue">{t('autodownload.noLanguages')}</Alert>
      ) : (
        <Stack gap="sm">
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
                modes={settings.data?.modes ?? ['default', 'on', 'off']}
                modePrefix="titleMode"
                inheritable
                // Показываем итог: при режиме «как решено для всех» причина
                // молчания лежит в общих настройках, а не здесь.
                hint={effective ? undefined : t('autodownload.effectiveNothing')}
                onChange={(next) => setDraft({ ...draft, [active]: next })}
              />
            </Tabs.Panel>
          </Tabs>

          <Group justify="space-between" wrap="wrap" gap="xs">
            <Group gap="xs">
              <Button
                variant="light"
                leftSection={<IconSearch size={16} />}
                loading={run.isPending}
                onClick={() => run.mutate()}
              >
                {t('autodownload.runSeries')}
              </Button>
              {own[active] && (
                <Button
                  variant="subtle"
                  color="red"
                  loading={clear.isPending}
                  onClick={() => clear.mutate(active)}
                >
                  {t('autodownload.clearOwn')}
                </Button>
              )}
            </Group>
            <Button
              loading={save.isPending}
              disabled={!draft[active]}
              onClick={() => save.mutate({ lang: active, value: current })}
            >
              {t('common.save')}
            </Button>
          </Group>

          <Text size="xs" c="dimmed">
            {t('autodownload.emptyList')}
          </Text>
        </Stack>
      )}
    </Modal>
  )
}
