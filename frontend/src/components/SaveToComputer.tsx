import { ActionIcon, Tooltip } from '@mantine/core'
import { IconDeviceFloppy } from '@tabler/icons-react'

interface Props {
  /** Адрес, отдающий файл с `Content-Disposition: attachment`. */
  href: string
  label: string
  disabled?: boolean
  size?: 'xs' | 'sm' | 'md'
}

/**
 * Кнопка «сохранить себе».
 *
 * Это обычная ссылка, а не запрос через наш клиент API, и так и задумано:
 * скачивание файла должно вести себя как скачивание — с прогрессом браузера,
 * докачкой при обрыве и записью прямо на диск, а не через память вкладки. Всё,
 * что для этого нужно, сервер уже сказал заголовком `Content-Disposition`.
 *
 * ⚠️ Атрибута `download` здесь нет намеренно. Имя файла назначает сервер, и он
 * знает его лучше: у локальных файлов это имя на диске, у видео с Anime365 —
 * то, которым сайт называет свои файлы сам. А для стороннего домена (видео
 * едет прямо с CDN) браузер этот атрибут всё равно игнорирует.
 *
 * Отдельная кнопка, а не пункт в меню загрузки: «скачать в библиотеку» и
 * «скачать себе» — разные действия с разным исходом, и путать их нельзя.
 * Первое ставит задачу в очередь на сервере, второе просто отдаёт файл.
 */
export function SaveToComputer({ href, label, disabled, size = 'sm' }: Props) {
  return (
    <Tooltip label={label} withArrow>
      <ActionIcon
        component="a"
        href={href}
        variant="subtle"
        size={size}
        aria-label={label}
        data-disabled={disabled || undefined}
        onClick={(event) => {
          if (disabled) event.preventDefault()
        }}
      >
        <IconDeviceFloppy size={size === 'xs' ? 14 : 16} />
      </ActionIcon>
    </Tooltip>
  )
}
