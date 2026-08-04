import { Button, Card, Code, Group, Stack, Text, ThemeIcon, Title } from '@mantine/core'
import { IconAlertTriangle, IconError404, IconLayoutGrid } from '@tabler/icons-react'
import { Link, isRouteErrorResponse, useLocation, useRouteError } from 'react-router-dom'

import { Logo } from '../components/Logo'
import { useT } from '../i18n'

/**
 * Экран на несуществующий адрес и на упавшую страницу.
 *
 * Стоит `errorElement` у корневого маршрута, поэтому рисуется вместо всего
 * дерева — без шапки, меню и единого запроса к серверу. Так и нужно: по
 * опечатке или старой закладке сюда попадают в том числе до входа, а страница,
 * которой нужны данные, встретила бы человека вереницей 401 вместо объяснения.
 *
 * Своя разметка, а не `components/ErrorState.tsx`: тот — плашка о неудавшемся
 * запросе внутри работающей страницы, здесь же ошибка и есть вся страница.
 * Общий у них подход: назвать причину и сказать, что делать дальше.
 */
export function ErrorPage() {
  const { t } = useT()
  const error = useRouteError()
  const { pathname } = useLocation()

  // Несовпавший адрес роутер приносит сюда своим ответом со статусом 404, всё
  // остальное — исключение из рендера. Различать обязательно: опечатку в адресе
  // человек исправит сам, а поломку ему остаётся только обойти.
  const notFound = isRouteErrorResponse(error) && error.status === 404
  const detail = error instanceof Error ? error.message : String(error)

  return (
    <Stack align="center" justify="center" style={{ height: '100dvh' }} p="md">
      <Card withBorder padding="lg" style={{ width: '100%', maxWidth: 420 }}>
        <Stack gap="md">
          <Logo height={40} />

          <Group gap="sm" wrap="nowrap" align="flex-start">
            <ThemeIcon variant="light" color={notFound ? 'gray' : 'red'} size={38} radius="md">
              {notFound ? <IconError404 size={22} /> : <IconAlertTriangle size={22} />}
            </ThemeIcon>
            <Stack gap={2} style={{ minWidth: 0 }}>
              <Title order={4}>
                {notFound ? t('errorPage.notFoundTitle') : t('errorPage.crashTitle')}
              </Title>
              <Text size="sm" c="dimmed">
                {notFound ? t('errorPage.notFoundHint') : t('errorPage.crashHint')}
              </Text>
            </Stack>
          </Group>

          {/* Адрес показываем целиком: опечатка в нём обычно видна сразу, и
              поправить её проще, чем гадать, туда ли вообще попал. У поломки
              на этом месте текст ошибки — единственное, что можно приложить к
              жалобе, поэтому он не прячется в консоль. */}
          <Code block style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
            {notFound ? pathname : detail}
          </Code>

          <Group justify="flex-end">
            {notFound ? (
              <Button component={Link} to="/" leftSection={<IconLayoutGrid size={16} />}>
                {t('errorPage.toCatalog')}
              </Button>
            ) : (
              // Обычная ссылка, а не переход роутером: на этом дереве рендер уже
              // падал, и повторный с тем же состоянием модулей упадёт снова.
              // Полная загрузка вдобавок лечит частый случай — старый бандл в
              // кэше браузера после обновления сборки.
              <Button component="a" href="/" leftSection={<IconLayoutGrid size={16} />}>
                {t('errorPage.toCatalog')}
              </Button>
            )}
          </Group>
        </Stack>
      </Card>
    </Stack>
  )
}
