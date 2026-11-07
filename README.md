# Minecraft Rust Edition

Неофициальная сборка для Windows x64 на [Horizon](https://github.com/horizon-rust/horizon), нативной реализации Minecraft Java Edition 26.3 на Rust. Поставка содержит исполняемый клиент Horizon и инструменты подготовки. Bootstrap самостоятельно получает закреплённые официальные файлы Minecraft 26.3 и Java 25, проверяет контрольные суммы и локально создаёт игровые ресурсы. Установка другой сборки или Java Edition заранее не требуется.

## Запуск

В LANMinecraft выберите «Minecraft Rust Edition» и нажмите «Играть». Лаунчер устанавливает проверенную поставку и запускает `native/launch-rust-edition.exe`. Подготовка ресурсов, запуск игры и ожидание её завершения принадлежат сборке.

Из отдельно распакованной поставки запустите `native/launch-rust-edition.exe`. По умолчанию миры, настройки и журналы записываются в `game`, подготовленные ресурсы в `cache` рядом с `native`. Первый запуск требует доступа к официальным источникам загрузок. Если у вас уже есть файлы 26.3, их можно использовать как проверяемый источник с каталогами `versions`, `libraries` и `assets`.

```powershell
.\native\launch-rust-edition.exe --minecraft-dir 'C:\путь\к\.minecraft'
```

Параметры `--game-dir DIR` и `--java-home DIR` задают собственные папку игры и Java 25. Java нужна на этапе подготовки, сама игра работает как нативный процесс. Закреплённый SHA-1 version JSON удостоверяет клиент, библиотеки и индекс ресурсов, индекс удостоверяет каждый объект. Сборка сохраняет проверенные исходные файлы в `cache/minecraft`, повторно использует их и восстанавливает повреждённые файлы при подготовке. Существующая установка служит источником только для чтения, недостающие файлы загружаются автоматически. Рабочие каталоги профиля, данных приложений и временных файлов игры и подготовки находятся внутри кеша сборки. При запуске из LANMinecraft этот кеш находится под `Minecraft/Launcher/Runtimes`. Для автономного запуска доступны одиночная игра и штатное ручное подключение к совместимому серверу в offline-режиме. Сеть Steam использует профиль из LANMinecraft. Сборка сохраняет пользовательские настройки Horizon.

Для подготовки можно указать собственный официальный серверный JAR через `--server-jar PATH`. Bootstrap проверяет его размер и SHA-1 по закреплённым данным 26.3. Если указанный файл отсутствует или не проходит проверку, подготовка завершается ошибкой. Без этого параметра сборка проверяет свой кеш, затем ищет `server.jar` или `26.3-server.jar` рядом с проверенным `versions/26.3/26.3.json`. Если подходящей копии нет, сборка загружает закреплённый серверный JAR с Mojang. Локальные исходные файлы используются только для чтения.

При повторном запуске bootstrap проверяет SHA-256 подготовленных файлов параллельно. Создаваемый игрой `horizon-data.bin` сверяется с хешами исходных ресурсов. Повреждение этого архива приводит к пересозданию только архива.

Кнопка «Пресет управления» предлагает 62 штатных назначения Minecraft Java Edition 26.3. Сборка доставляет `launcher/controls-preset.txt` в отдельном небольшом архиве. После подтверждения лаунчер меняет назначения, сохраняя остальные настройки. `configureddefaults/options.txt` задаёт версию формата для нового профиля.

Уведомления LANMinecraft отображаются встроенным слоем native UI поверх меню и
полноэкранной игры. Отдельный мод не устанавливается. Тексты, стиль и очередь
принадлежат лаунчеру; слой только читает его ограниченный UTF-8 Properties
контракт из `LANMINECRAFT_NOTICE_DIRECTORY`, рисует системным шрифтом Windows и
подтверждает настоящие показанные кадры. Игровой GUI scale, фокус и ввод не
меняются, `session.json` остаётся файлом лаунчера. Для этой поставки требуется
capability `native-launcher-notices-v1` поддерживающего лаунчера.

## Подготовка и проверка релиза

Перед обновлением движка прочитать [AGENTS.md](AGENTS.md) и
[защиту обязательных слоёв и приёмки](tools/UPDATE-ACCEPTANCE.md). Упаковщик
блокирует выпуск без действующей source-квитанции и отчётов точного candidate.
Для локальной проверки обновления сначала использовать `--prepare-qa`;
он создаёт `candidate-manifest.json`, а выпускной манифест появляется после приёмки.

Для инструментов сборки нужны Python 3.12 или новее, Git и OpenSSL 3. Rust нужен для сборки исходников, а игрок запускает готовый Windows x64 клиент. Bootstrap собирается для `x86_64-pc-windows-msvc` со статическим C runtime.

```powershell
Push-Location launcher/standalone
cargo build --release --locked
Pop-Location
```

- `tools/upstream-lock.json` закрепляет Nightly, исходники, архив Windows, размер, SHA-256, коммит и Minecraft/Java. `tools/upstream/` хранит подписанные исходные контрольные суммы. `tools/steam-lock.json` закрепляет исходники общего адаптера и DLL Steamworks. Плавающий тег `nightly` и удаляемые upstream assets не подходят в качестве постоянного адреса активной поставки.
- `integration/steam/apply_horizon_overlay.py` проверяет точный архив исходников, применяет Steam-слой и связывает проверенный `horizon-client.exe` с исходниками и DLL. `launcher/standalone` содержит исходники программы запуска.
- `tools/stage_runtime.py` проверяет Ed25519, размеры и SHA-256 исходных архивов, затем собирает новый независимый кандидат в указанной папке. Без параметров Steam и bootstrap он собирает только upstream-эталон для QA. Оригинальные файлы Mojang, подготовленный кеш и тестовые миры не входят в candidate.

```powershell
python tools/stage_runtime.py --archives $archives --stage $candidate `
    --patched-client $client --steam-dll $steamDll --overlay-receipt $receipt `
    --bootstrap $bootstrap --steam-repo $steamRepo
```

- Проверьте настоящий клиент с `tools/smoke_runtime.py`, автономный bootstrap и обычный запуск из LANMinecraft. При изменении bootstrap `--reuse-world` повторяет проверку на техническом мире из предыдущего прогона. `tools/generate_manifest.py --propose-inventory $smokeJson --candidate $candidate` связывает пустую страницу загруженных модов и SHA-256 файлов `native/` для `library/loaded-mods.json`. Превью `library/preview.jpg` берите из реального технического мира проверенного клиента. После приёмки перенесите четыре файла `library` в уже подготовленный candidate и положите проверенную квитанцию overlay в `tools/overlay-receipt.json`.
  Флаг `--check-pause-menu` проверяет возвращение в игру после «Открыть для сети» и переход к «Жалобы» при повторном открытии меню тем же клиентом через локальный loopback. Для проверки самостоятельной подготовки передайте `--bootstrap` и пустой `--cache`, опустив `--minecraft` и `--java`. Сценарий изолирует поиск внешних установок. Проверка подключения двух Steam-аккаунтов выполняется отдельно.
- `tools/generate_manifest.py --propose-lock --candidate $candidate --library-tool $libraryTool` выводит проект distribution lock для явной проверки. После проверки закрепите его в `tools/distribution-lock.json`. Обычная генерация повторно проверяет точный состав, исходный рецепт и наблюдение модов. Она принимает чистый Git checkout указанного коммита.

```powershell
$revision = git rev-parse HEAD
python tools/generate_manifest.py --candidate $candidate --out $dist `
    --revision $revision --library-tool $libraryTool
python tools/generate_manifest.py --candidate $candidate --out $dist `
    --revision $revision --library-tool $libraryTool --verify-only
```

`$libraryTool` указывает на `tools/pack_library.py` совместимого checkout LANMinecraft. Кандидат, archives и dist храните в одном зарегистрированном временном стенде LANMinecraft. Архив `minecraft-rust-edition-windows-x64-<sha256>.zip` служит и пакетом для лаунчера, и самостоятельной поставкой. Публикуйте архивы по SHA, затем `pack-manifest.json`. После независимой проверки опубликованных файлов добавьте запись каталога `minecraft-9` с путём `Minecraft Rust Edition`.

### Неизменяемые входы Horizon

Для повторной сборки после ротации `nightly` сохраните четыре оригинальных upstream-файла в отдельном выпуске репозитория сборки с тегом `mirrorTag` из `tools/upstream-lock.json`.

- `horizon-windows-x64.zip`
- `horizon-source.tar.gz`
- `SHA256SUMS`
- `SHA256SUMS.sig`

Тег зеркала закрепляет точный исходный снимок Horizon. Обновление использует новый тег. Для `--archives` загрузите из него два архива. `stage_runtime.py` сверяет подпись Ed25519 по исходному тегу и проверяет архивы по закреплённым размерам и SHA-256. Зеркало содержит только исходные файлы Horizon. Подготовленные ресурсы Minecraft, технические миры и итоговые файлы LANMinecraft находятся за пределами этого выпуска.

Оригинальный код Horizon распространяется по MIT. Лицензия и уведомления о сторонних компонентах входят в `native`, атрибуция логотипа Rust находится в `native/third_party/rust/NOTICE.txt`. Minecraft и его игровые материалы принадлежат Mojang и Microsoft.

### Команды гостей и Steam

`tools/steam_runtime_qa.py --client $client --controller $controller --resources $resources --out $qa` запускает два настоящих Windows-клиента в отдельных технических профилях. Проверяет однокнопочную публикацию, отключённого диктора, выполнение `/gamemode survival` и `/gamemode creative` гостем при разрешённых командах владельца и отказ в мире без команд. Результат loopback не подтверждает Steam-приглашения двух аккаунтов. Для них отдельно проверяют Shift+Tab, принятие приглашения, подключение и повторное открытие мира с прежним профилем.
