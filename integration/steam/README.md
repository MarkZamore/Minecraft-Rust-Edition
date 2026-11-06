# Слой Steam для Horizon

Слой привязан к Nightly `66f3981f522b24c250b21523a70080965b940424` и исходникам транспорта e4steam `a4a362c313e99386dd46c02d7d5fd67aa6057948`. Он заменяет публикацию интегрированного мира и автоматический поиск через LAN на Steam при запуске с профилем LANMinecraft. Ручной список серверов остаётся доступен. Файлы мира и личные настройки принадлежат штатным механизмам Horizon.

Кнопка «Открыть для сети» в меню паузы сразу публикует мир. После подтверждения Steam на её месте появляется «Жалобы». При ошибке публикации кнопку можно нажать снова. Гость видит настоящее название мира и ник хозяина в списке «Сетевая игра». Локальный TCP-порт ограничен loopback. Подключение допускается после подтверждения Steam и одноразовой привязки к соединению. Имя и UUID гостя определяются по этой привязке. Скины загружаются из локального реестра лаунчера с проверкой размера, SHA256 и формата.

Заголовок окна берётся из имени карточки через `LANMINECRAFT_BUILD_NAME`. Winit и Windows-ресурс клиента используют растр официального знака языка Rust. Исходник, пины, инструмент повторной растеризации и атрибуция находятся в [rust-logo](rust-logo/NOTICE.txt).

## Сборка Windows x64

Понадобятся Python 3.12+, Rust 1.98.1 `x86_64-pc-windows-msvc`, MSVC и Windows SDK. Поместите проверенный `horizon-source.tar.gz`, распакованное дерево `$source` и `$target` в текущий зарегистрированный стенд LANMinecraft. `$steamTransport` указывает на `source/native` из закреплённого checkout e4steam, `$steamDll` на Valve `steam_api64.dll` из `steamworks-sys` 0.13.0. Сверьте подпись Valve и SHA256 в `tools/steam-lock.json`. Целевой каталог Cargo держите отдельно от распакованных исходников.

```powershell
python integration/steam/apply_horizon_overlay.py --source-archive $archive `
    --source $source --steam-transport $steamTransport
$env:CARGO_TARGET_DIR = $target
$env:RUSTFLAGS = '-C target-feature=+crt-static'
$env:HORIZON_VERSION = 'nightly-2026-11-05-66f3981'
$env:HORIZON_NIGHTLY = '2026-11-05 66f3981'
cargo build --locked --release --manifest-path (Join-Path $source 'Cargo.toml') `
    -p horizon-client --bin horizon-client
$client = Join-Path $target 'release/horizon-client.exe'
python integration/steam/apply_horizon_overlay.py --source-archive $archive `
    --source $source --steam-transport $steamTransport --receipt $receipt `
    --client $client --steam-dll $steamDll
```

`--check` вычисляет SHA выходных исходников без распаковки. Обычное применение принимает чистое дерево из закреплённого архива. Режим квитанции проверяет готовое дерево, Cargo.lock, DLL Valve и разрядность PE. Хеши клиента и DLL входят в квитанцию для проверки упаковщиком. Сценарии приёмки и публикации описаны в [README сборки](../../README.md).
