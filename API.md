# Интерфейсы SAFAR STUDIO v5.0

## Аккаунт артиста

На Studio Pro и Label владелец может выпустить ключ в аккаунте. Префикс ключа — `sfp_`. В базе хранится SHA-256. Запросы на домен Next.js используют `Authorization: Bearer <ключ артиста>`. Сервисный токен Modal доступен только серверу владельца.

| Метод и путь Next.js | Назначение |
|---|---|
| GET `/api/me`, GET `/api/jobs` | Аккаунт, баланс, последние 100 сессий |
| POST `/api/jobs` | Уникальный UUID `id`, вид `generate`, `repaint`, `train` или `review`. Ответ 202 |
| GET `/api/jobs/{id}` | Статус, стадия, телеметрия, словарь доставки или возврат |
| POST `/api/uploads` | Получить `id` и подписанный URL. PUT сырых байтов напрямую в Modal. До 180 MiB и 600 секунд, моно/стерео |
| GET `/api/files/{id}/{file}` | Подписанный URL формата, доступного владельцу. Срок 30 минут |
| GET `/api/license/{id}` | PDF лицензии |
| POST `/api/batch` | Label, массив `jobs` из 1–10 заданий со стабильными UUID. Поэлементный результат для повторной отправки |
| POST `/api/share` | Две готовые сессии `a`, `b`. Label может добавить `brand` |
| POST `/api/share/revoke` | Закрыть клиентскую ссылку по её `token` |

Для генерации передай `brief`, `lyrics`, `bpm`, `key`, `language`, `model` и `duration_sec`. Выбор `sketch` / `studio` соответствует Turbo / XL. `preset` принимает `streaming`, `club`, `apple`. Поле `mode` принимает `song_with_vocals`, `instrumental`, `cover`, `style_transfer`, `vocal_to_bgm`. Для последних трёх обязателен `upload_id` или `parent_id`. Принцип повторной отправки — тот же UUID и те же параметры.

Для Repaint передай `kind=repaint`, исходник, границы в секундах и `instruction`. Для LoRA передай `kind=train`, `name`, `model`, список из 5–10 уникальных `upload_ids` и `rights_confirmed=true`. Готовый ID адаптера укажи в `lora_id` для той же базовой модели. `clean_intervals` принимает пары [начало, конец] в секундах для Clean-версии.

## Закрытый GPU API

Передавай `SAFAR_GPU_TOKEN` через заголовок Bearer. Публичные пользователи пользуются Next.js API. Прямой доступ к Modal предназначен для доверенного бэкенда и обходит тарифный ledger.

| Метод и путь Modal | Контракт |
|---|---|
| GET `/health` | Подтверждает доступ к API и конфигурацию. Реальную генерацию проверяет отдельная ячейка Colab |
| POST `/api/generate`, `/api/repaint` | Старые поля запросов и синхронный результат v4.1. Для очереди добавь `Prefer: respond-async` |
| POST `/api/jobs`, `/api/lora/train` | Устойчивый идентификатор, ответ 202. Каждый запрос принадлежит UUID `owner` |
| GET `/api/jobs/{id}` | `queued/running/done/failed`, стадия, GPU, секунды, результат |
| POST `/api/export/sync-pack` | Вернуть готовый ZIP для `owner` и `job_id` |
| POST `/api/tickets` | Подписать разрешение на одно конкретное чтение файла либо запись аудио |
| GET / PUT `/media/{ticket}` | Только операция, владелец и ресурс из билета |

Синхронный generate возвращает `master_wav_b64`, `stems_b64`, `asr_transcript`, длительность и частоту. Сохранились старые имена `drums`, `bass`, `lead` и `chords`. Полный набор из шести доступен через `Stems.zip` и отдельные WAV. Синхронный Repaint принимает `base_wav_b64`. Для длинных результатов выбирай асинхронное API и прямые ссылки на файлы. Длинные синхронные запросы Modal могут использовать HTTP 303 и требуют клиента, следующего редиректам. [Modal request timeouts](https://modal.com/docs/guide/webhook-timeouts)

## Дополнительные движки

`engine.models.MusicEngineAdapter` задаёт интерфейс `render(job, folder, source, task, instruction)` с выходным путём WAV. Доверенный Python-пакет регистрирует фабрику в entry-point группе `safar.music_engines`. Основные режимы маршрутизируются переменными образа `SAFAR_ENGINE_SKETCH` и `SAFAR_ENGINE_STUDIO`. Адаптер сообщает словарь `provenance` с полями `model`, `model_license`, `model_revision`. Дополнительные семейства DiffRhythm и YuE не скачиваются при штатном деплое. Для их подключения нужен адаптер выбранного чекпойнта с проверенными правами и его зависимости в GPU-образе.
