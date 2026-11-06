# Minecraft Rust Edition

Неофициальная сборка для Windows x64 на [Horizon](https://github.com/horizon-rust/horizon), нативной реализации Minecraft Java Edition 26.3 на Rust. Для подготовки игровых ресурсов нужна установленная лицензионная копия Minecraft Java Edition 26.3. Поставка содержит исполняемый клиент Horizon и инструменты подготовки. Игровые ресурсы создаются локально.

## Запуск

В LANMinecraft выберите «Minecraft Rust Edition» и нажмите «Играть». Лаунчер устанавливает проверенную поставку и запускает `native/launch-rust-edition.exe`. Подготовка ресурсов, запуск игры и ожидание её завершения принадлежат сборке.

Из отдельно распакованной поставки запустите `native/launch-rust-edition.exe`. По умолчанию миры, настройки и журналы записываются в `game`, подготовленные ресурсы в `cache` рядом с `native`. Если 26.3 установлен вне стандартной папки, укажите свой каталог с `versions`, `libraries` и `assets`.

```powershell
.\native\launch-rust-edition.exe --minecraft-dir 'C:\путь\к\.minecraft'
```

Параметры `--game-dir DIR` и `--java-home DIR` задают собственные папку игры и Java 25. Java нужна на этапе подготовки, сама игра работает как нативный процесс. Первый запуск проверяет исходные файлы 26.3, при необходимости получает закреплённый JDK и официальный серверный JAR для подготовки. Рабочие каталоги профиля, данных приложений и временных файлов игры и подготовки находятся внутри кеша сборки. При запуске из LANMinecraft этот кеш находится под `Minecraft/Launcher/Runtimes`. Для автономного запуска доступны одиночная игра и штатное ручное подключение к совместимому серверу в offline-режиме. Сеть Steam использует профиль из LANMinecraft. Сборка сохраняет пользовательские настройки Horizon.

## Подготовка и проверка релиза

Для инструментов сборки нужны Python 3.12 или новее, Git и OpenSSL 3. Rust нужен для сборки исходников, а игрок запускает готовый Windows x64 клиент.

- `tools/upstream-lock.json` закрепляет Nightly, исходники, архив Windows, размер, SHA-256, коммит и Minecraft/Java. `tools/upstream/` хранит подписанные исходные контрольные суммы. `tools/steam-lock.json` закрепляет исходники общего адаптера и DLL Steamworks. Плавающий тег `nightly` и удаляемые upstream assets не подходят в качестве постоянного адреса активной поставки.
- `integration/steam/apply_horizon_overlay.py` проверяет точный архив исходников, применяет Steam-слой и связывает проверенный `horizon-client.exe` с исходниками и DLL. `launcher/standalone` содержит исходники программы запуска.
- `tools/stage_runtime.py` проверяет Ed25519, размеры и SHA-256 исходных архивов, затем собирает новый независимый кандидат в указанной папке. Без параметров Steam и bootstrap он собирает только upstream-эталон для QA. Оригинальные файлы Mojang, подготовленный кеш и тестовые миры не входят в candidate.

```powershell
python tools/stage_runtime.py --archives $archives --stage $candidate `
    --patched-client $client --steam-dll $steamDll --overlay-receipt $receipt `
    --bootstrap $bootstrap --steam-repo $steamRepo
```

- Проверьте настоящий клиент с `tools/smoke_runtime.py`, автономный bootstrap и обычный запуск из LANMinecraft. `tools/generate_manifest.py --propose-inventory $smokeJson --candidate $candidate` связывает пустую страницу загруженных модов и SHA-256 файлов `native/` для `library/loaded-mods.json`. Превью `library/preview.jpg` берите из реального технического мира проверенного клиента. После приёмки перенесите четыре файла `library` в уже подготовленный candidate и положите проверенную квитанцию overlay в `tools/overlay-receipt.json`.
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
