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
