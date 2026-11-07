# Слой Steam для Horizon

Слой привязан к Nightly `6560641ba51a452f6bc2407baa4eb1b3be517ca8` и исходникам транспорта e4steam `faa25063441b83f589caab505dfefda8932e4fe4`. Он заменяет публикацию интегрированного мира и автоматический поиск через LAN на Steam при запуске с профилем LANMinecraft. Ручной список серверов остаётся доступен. Файлы мира и личные настройки принадлежат штатным механизмам Horizon.

Кнопка «Открыть для сети» в меню паузы сразу публикует мир и возвращает в игру. После подтверждения Steam в чате появляются копируемый адрес `<lobby-id>.steam`, «Пригласить друзей (Steam)» и «Закрыть общий доступ», а при следующем открытии меню на месте кнопки появляется «Жалобы». Команды гостей наследуют разрешение команд владельца мира; сервер сохраняет штатную проверку прав. Действия чата действуют только для текущей публикации у владельца. При ошибке публикации кнопку можно нажать снова. Гость видит настоящее название мира и ник хозяина в списке «Сетевая игра». Локальный TCP-порт ограничен loopback. Подключение допускается после подтверждения Steam и одноразовой привязки к соединению. Имя и UUID гостя определяются по этой привязке. Скины загружаются из локального реестра лаунчера с проверкой размера, SHA256 и формата.

Заголовок окна берётся из имени карточки через `LANMINECRAFT_BUILD_NAME`. Winit и Windows-ресурс клиента используют растр официального знака языка Rust. Исходник, пины, инструмент повторной растеризации и атрибуция находятся в [rust-logo](rust-logo/NOTICE.txt).

## Сборка Windows x64

Понадобятся Python 3.12+, Rust 1.98.1 `x86_64-pc-windows-msvc`, MSVC и Windows SDK. Поместите проверенный `horizon-source.tar.gz`, распакованное дерево `$source` и `$target` в текущий зарегистрированный стенд LANMinecraft. `$steamTransport` указывает на `source/native` из закреплённого checkout e4steam, `$steamDll` на Valve `steam_api64.dll` из `steamworks-sys` 0.13.0. Сверьте подпись Valve и SHA256 в `tools/steam-lock.json`. Целевой каталог Cargo держите отдельно от распакованных исходников.

```powershell
python integration/steam/apply_horizon_overlay.py --source-archive $archive `
    --source $source --steam-transport $steamTransport
$env:CARGO_TARGET_DIR = $target
$env:RUSTFLAGS = '-C target-feature=+crt-static'
$env:HORIZON_VERSION = 'nightly-2026-11-07-6560641'
$env:HORIZON_NIGHTLY = '2026-11-07 6560641'
cargo build --locked --release --manifest-path (Join-Path $source 'Cargo.toml') `
    -p horizon-client --bin horizon-client
$client = Join-Path $target 'release/horizon-client.exe'
python integration/steam/apply_horizon_overlay.py --source-archive $archive `
    --source $source --steam-transport $steamTransport --receipt $receipt `
    --client $client --steam-dll $steamDll
```

`--check` вычисляет SHA выходных исходников без распаковки. Обычное применение принимает чистое дерево из закреплённого архива. Режим квитанции проверяет готовое дерево, Cargo.lock, DLL Valve и разрядность PE. Хеши клиента и DLL входят в квитанцию для проверки упаковщиком. Сценарии приёмки и публикации описаны в [README сборки](../../README.md).

Приглашения из Shift+Tab и Steam rich presence проходят проверку свежего мира друга и точной версии транспорта. Затем клиент штатно сохраняет текущий мир и подключается; сервер подтверждает настоящего владельца лобби и одноразовую привязку гостя. Сценарий приглашений соответствует Java e4steam SteamLobbyManager, но поставка Horizon использует Rust-адаптер и Valve API, а не Java JAR. Два настоящих Steam-аккаунта проверяются отдельно от loopback-проверки команд.

`tools/horizon_steam_module_tests.rs` проверяет сам доставляемый модуль против уже собранных locked-библиотек Horizon: UUID, прежние профили и запрет устаревших/повреждённых действий чата. Для `rustc --test --edition=2024` используйте release `.rlib` из того же `CARGO_TARGET_DIR`, `-L dependency=<target>/release/deps`, native link paths из Cargo build-script artifacts, `-C opt-level=3 -C lto=thin -C target-feature=+crt-static`; передайте `--extern` для `horizon_client`, `horizon_world`, `lanmc_steam_native`, `sha1`, `sha2`. При запуске теста каталог проверенной Valve DLL должен находиться в PATH.
