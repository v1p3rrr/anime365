import { Alert, Button, Group, Modal, Radio, Select, Stack, Text, TextInput } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { IconLibraryPlus } from '@tabler/icons-react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { libraryApi } from '../api/library'
import { useT } from '../i18n'
import { useApiError } from '../i18n/apiError'

interface Props {
  /** Что добавляем. Обычно один тайтл, но список — чтобы годилось и для группы. */
  seriesIds: number[]
  /** Подставляется в имя новой коллекции. */
  suggestedName?: string
}

/**
 * Добавление тайтла в коллекцию Jellyfin — в существующую или новую.
 *
 * **Запасной путь, а не основной.** Обычно Jellyfin собирает коллекции сам, по
 * данным TMDB — проверено на живой библиотеке: у коллекции Heaven's Feel стоит
 * `Tmdb: 390636`, то есть она возникла автоматически. Но про часть тайтлов TMDB
 * не знает вовсе, и тогда связать их иначе как вручную нельзя.
 *
 * Коллекция принимает элементы любого типа (`ids` в API типом не ограничен),
 * поэтому в одну можно сложить и сериал, и фильм.
 *
 * Существующие коллекции показываем списком намеренно: у Jellyfin нет запрета
 * на две коллекции с одинаковым именем, и без выбора мы бы их молча плодили.
 *
 * Права проверяет сервер (`LIBRARY`); кнопка здесь — удобство, а не защита.
 */
export function CollectionButton({ seriesIds, suggestedName = '' }: Props) {
  const { t } = useT()
  const apiError = useApiError()
  const queryClient = useQueryClient()
  const [opened, setOpened] = useState(false)
  const [mode, setMode] = useState<'existing' | 'new'>('existing')
  const [collectionId, setCollectionId] = useState<string | null>(null)
  const [name, setName] = useState(suggestedName)

  // Спрашиваем про конкретный тайтл: ответ помечает коллекции, где он уже
  // состоит. Без этого его добавляют повторно, не зная об этом.
  const single = seriesIds.length === 1 ? seriesIds[0] : undefined
  const collections = useQuery({
    queryKey: ['jellyfin-collections', single ?? null],
    queryFn: () => libraryApi.jellyfinCollections(single),
    enabled: opened,
    staleTime: 60_000,
  })

  const available = collections.data?.available ?? false
  const rows = collections.data?.collections ?? []
  const already = rows.filter((item) => item.contains)
  const options = rows.map((item) => ({
    value: item.id,
    // Пометка прямо в подписи: отдельная колонка в выпадающем списке Mantine
    // не рисуется, а знать об этом надо до нажатия, а не после.
    label: item.contains ? `${item.name} · ${t('collection.alreadyIn')}` : item.name,
  }))

  const report = (added: number, missing: number[]) => {
    setOpened(false)
    queryClient.invalidateQueries({ queryKey: ['jellyfin-collections'] })
    notifications.show({
      color: missing.length > 0 ? 'yellow' : 'teal',
      title: t('collection.done'),
      // Про не попавшие тайтлы говорим отдельно: добавить половину и
      // отчитаться успехом значило бы соврать.
      message:
        t('collection.added', { count: added }) +
        (missing.length > 0 ? ' · ' + t('collection.missing', { count: missing.length }) : ''),
    })
  }

  const submit = useMutation({
    mutationFn: async () => {
      if (mode === 'new') {
        const result = await libraryApi.createCollection(name.trim(), seriesIds)
        return { added: result.created, missing: result.missing }
      }
      const result = await libraryApi.addToCollection(collectionId as string, seriesIds)
      return { added: result.added, missing: result.missing }
    },
    onSuccess: (result) => report(result.added, result.missing),
  })

  const canSubmit =
    mode === 'new' ? name.trim().length > 0 : collectionId !== null && options.length > 0

  return (
    <>
      <Button
        size="compact-sm"
        variant="light"
        color="grape"
        leftSection={<IconLibraryPlus size={14} />}
        onClick={() => {
          setName(suggestedName)
          setOpened(true)
        }}
      >
        {t('collection.button')}
      </Button>

      <Modal opened={opened} onClose={() => setOpened(false)} title={t('collection.title')} centered>
        <Stack gap="sm">
          <Text size="sm" c="dimmed">
            {t('collection.explain')}
          </Text>

          {collections.isLoading && <Text size="sm">{t('common.loading')}</Text>}

          {collections.data && !available && (
            <Alert color="yellow">
              {collections.data.unreachable
                ? t('collection.unreachable')
                : t('collection.disabled')}
            </Alert>
          )}

          {/* Где тайтл уже состоит — сразу, до раскрытия списка. */}
          {already.length > 0 && (
            <Alert color="blue">
              {t('collection.memberOf', { names: already.map((item) => item.name).join(', ') })}
            </Alert>
          )}

          {available && (
            <>
              <Radio.Group value={mode} onChange={(value) => setMode(value as 'existing' | 'new')}>
                <Stack gap={6}>
                  <Radio
                    value="existing"
                    label={t('collection.useExisting')}
                    // Выбирать нечего — предлагать этот путь незачем.
                    disabled={options.length === 0}
                  />
                  <Radio value="new" label={t('collection.createNew')} />
                </Stack>
              </Radio.Group>

              {mode === 'existing' ? (
                <Select
                  data={options}
                  value={collectionId}
                  onChange={setCollectionId}
                  placeholder={
                    options.length === 0 ? t('collection.noneYet') : t('collection.pick')
                  }
                  disabled={options.length === 0}
                  searchable
                />
              ) : (
                <TextInput
                  label={t('collection.name')}
                  value={name}
                  onChange={(event) => setName(event.currentTarget.value)}
                  data-autofocus
                />
              )}
            </>
          )}

          {submit.isError && <Alert color="red">{apiError(submit.error)}</Alert>}

          <Group justify="flex-end">
            <Button variant="default" onClick={() => setOpened(false)}>
              {t('common.cancel')}
            </Button>
            <Button
              loading={submit.isPending}
              disabled={!available || !canSubmit}
              onClick={() => submit.mutate()}
            >
              {mode === 'new' ? t('collection.create') : t('collection.add')}
            </Button>
          </Group>
        </Stack>
      </Modal>
    </>
  )
}
