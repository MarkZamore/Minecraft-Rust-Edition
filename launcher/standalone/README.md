# Native bootstrap presentation contract

`native/presentation.json` contains the pack's existing `name` and optional
`instanceConfig` paths. Its optional `environmentDefaults` object supplies
environment defaults only for the actual game child. Preparation tools, Java,
the bootstrap process and unrelated applications are unaffected.

Every key and value must be a string. There may be at most 16 entries. Names
are 1–64 ASCII bytes matching `[A-Za-z_][A-Za-z0-9_]*`; values are at most 1024
UTF-8 bytes and contain no control characters. Names and values together may
occupy at most 8192 UTF-8 bytes. Identical or ASCII case-insensitive duplicate
names are rejected while parsing. An absent field or empty object is valid;
`null` is invalid. The complete object is validated before resource preparation.

The game command inherits the parent environment. Any inherited variable,
including an empty or non-Unicode value, has priority. Explicit settings and
removals already made on the child command also have priority, using the
standard library's Windows environment-name comparison. Defaults never mutate
the parent environment. If future code clears the game command's environment,
its inherited-variable lookup must be adjusted explicitly as well.

The generic bootstrap contains no pack-specific compatibility names or values;
those belong to the pack's checked presentation data. Build and test with the
locked dependencies, Windows x64 and `RUSTFLAGS=-C target-feature=+crt-static`.

## Shared native runtime

The launcher verifies an immutable runtime archive and starts its bootstrap
under `Launcher/Shared/NativeRuntimes/<archive SHA-256>/native`. It passes two
absolute directories to the bootstrap:

| Environment variable | Directory |
| --- | --- |
| `LANMINECRAFT_NATIVE_PACK_ROOT` | The installed pack containing `portable-pack.json` and the `native/` overlay |
| `LANMINECRAFT_NATIVE_PREPARED_CACHE` | The `Launcher/Shared/NativePrepared` cache, separate from the runtime and all pack locations |

The bootstrap compares `portable-pack.json`'s `nativeRuntime.sharedRuntime`
with the shared archive folder and `native/runtime-family.json`. The match
covers the runtime ID, runtime version, target, Minecraft version and archive
SHA-256. The launcher checks the contents of the archive against its inventory.
The pack supplies `presentation.json` and its managed mod packages. The game
client and preparer run from the shared runtime folder. The client receives
the validated pack root for managed overlay discovery. `--game-dir`, Steam
identity and `LANMINECRAFT_NATIVE_CACHE` retain the instance's game directory,
configuration and session lock.

The shared preparation key hashes the recipe schema, pinned default Java
archive, preparer, research lock, and every file copied to the preparer's work
tree. A completed generation stores the verified loose resources and their
SHA-256 receipt. Initialization, repair and generated archive reconciliation
take exclusive access. Concurrent clients hold shared leases through exit.
The old pack-local caches have a different receipt format and are preserved
for legacy launches. Ordinary standalone launches with neither shared-runtime
variable continue using the executable-adjacent layout and pack-local cache.
