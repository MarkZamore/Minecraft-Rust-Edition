"""The f00125f replacement slots required by the current Rust Mods native pack."""

import json
import re
from pathlib import Path

from apply import sha
from slots import TEMPERATURE_METHOD, TEMPERATURE_SLOT, key


MARKER = b"#[horizon_mod_api_macros::replaceable]"
INTERNALS = "crates/horizon-mod-api-gen/internals.txt"
TEMPERATURE = "crates/horizon-world/src/worldgen/biome_temperature.rs"
MACROS = "horizon-mod-api-macros"
MANIFESTS = {
    "horizon-core": ((MACROS, "libm"),),
    # The SDK point scanner requires this direct dependency when a marked
    # crate is outside horizon-engine-slots' transitive dependencies.
    "horizon-world": (("horizon-engine-slots", "horizon-threads"), (MACROS, "horizon-threads")),
}

FUNCTIONS = {
    "crates/horizon-client/src/mobs/render.rs": (
        "253e7483d9c26926fc0a65698f9ee8f0c7647d0da7c157fca656b4b70e327cb4",
        (
            ("horizon_client_lib::mobs::render::draw_normals",
             "fn draw_normals("),
            ("horizon_client_lib::mobs::render::render_with",
             "pub fn render_with("),
            ("horizon_client_lib::mobs::render::cape",
             "pub fn cape("),
        ),
    ),
    "crates/horizon-client/src/animate_tick.rs": (
        "eaaf5b8acc4f1a687fd403ef3888ca737e2e7d5f5b55450f5d3126151346ab5a",
        (
            ("horizon_client_lib::animate_tick::add",
             "fn add(engine: &mut ParticleEngine, ty: T, p: [f64; 3], v: [f64; 3]) {"),
            ("horizon_client_lib::animate_tick::make_campfire_smoke",
             "pub fn make_campfire_smoke("),
        ),
    ),
    "crates/horizon-client/src/title/draw.rs": (
        "b5991168070ae8e1b56f5ea6b0aea3d1a34253128c7acd8af8b48277b1451ae2",
        (
            ("horizon_client_lib::title::draw::Title::draw_with_items",
             "    pub fn draw_with_items("),
        ),
    ),
    "crates/horizon-client/src/title.rs": (
        "640f3df2bfafe21d7c10273d9a5a150ddb1ca1571bdfd10de7e4e69d596ebcc4",
        (
            ("horizon_client_lib::title::Title::click",
             "    pub fn click(&mut self, x: f32, y: f32, size: (f32, f32)) -> (Action, bool) {"),
        ),
    ),
    "crates/horizon-client/src/motion.rs": (
        "c0d2e9da0709ff7d990c91cd688f95aab0a0cc160e04f1a4045dbbe61832170f",
        (
            ("horizon_client_lib::motion::MotionWorld<'_,'_,'_>::legacy_orbital_local_tick",
             "    fn legacy_orbital_local_tick("),
        ),
    ),
    "crates/horizon-client/src/vanillagen.rs": (
        "0cab8ab71c3106435efa651304202ff493dd0268bf67d3addc4d2668cb3566b1",
        (
            ("horizon_client_lib::vanillagen::select_worldgen_overrides",
             "fn select_worldgen_overrides(overrides: &WorldgenOverrides) -> &WorldgenOverrides {"),
        ),
    ),
    "crates/horizon-client-render/src/chat.rs": (
        "81e416bf5144e11c87c04aca2c716ae13225dfb966f2a88d3818810f55535fa3",
        (
            ("horizon_client_render::chat::Chat::draw_at",
             "    pub fn draw_at("),
        ),
    ),
    "crates/horizon-client-render/src/gfx/reuse.rs": (
        "f418314fe469fcaaa24c2ddcfab417be542029d58de3f44e2ca1b23927cb4764",
        (
            ("horizon_client_render::gfx::reuse::On<'_>::create_shader_module",
             "    pub fn create_shader_module("),
        ),
    ),
    "crates/horizon-client-render/src/mesher.rs": (
        "3bdc66c8eac88ec4ac936d580a78ddba7ee147ce582118f97d8570d66deb2153",
        (
            ("horizon_client_render::mesher::Mesher<'_>::mesh_reusing",
             "    pub fn mesh_reusing("),
        ),
    ),
    "crates/horizon-client-render/src/render_slots.rs": (
        "b3ce0ed4bd7c877767b2b32323d4ee36b9faed7012b9e772f6b47fdde59a4249",
        (
            ("horizon_client_render::render_slots::world_drawn",
             "pub fn world_drawn(world: &mut WorldDrawn<'_>) {"),
            ("horizon_client_render::render_slots::legacy_render_needs",
             "fn legacy_render_needs(needs: &mut RenderNeeds) {"),
        ),
    ),
    "crates/horizon-client-render/src/block_entity_render.rs": (
        "1b4697628dd112ee3f56e689359627995a86e417e06ba4c07318ddd4e568b2d7",
        (
            ("horizon_client_render::block_entity_render::BlockEntityRenderer::render_one",
             "    fn render_one("),
        ),
    ),
    "crates/horizon-client-render/src/gfx/native_material.rs": (
        "6cbcd766cd6150c07ff1b9c6a5ecce6b148191800d9a89d27ba2c0a06666eef0",
        (
            ("horizon_client_render::gfx::native_material::module",
             "fn module("),
        ),
    ),
    "crates/horizon-client-render/src/particles.rs": (
        "777962cac7906f8ef9d32d06e284bbcbec0f4b0c1bb4933475c3a5c7f0d008f5",
        (
            ("horizon_client_render::particles::ParticleEngine::add_with_default",
             "    fn add_with_default("),
        ),
    ),
    "crates/horizon-client-render/src/options_ui/layout.rs": (
        "8ef2e7c305cbc497efdfbc8a3c18ebeb32b55947630c29b1ce2e543845fbd639",
        (
            ("horizon_client_render::options_ui::layout::OptionsUi::layout",
             "    pub(super) fn layout(&mut self, s: &Settings, w: f32, h: f32) -> (Vec<Widget>, f32, f32, f32) {"),
        ),
    ),
    "crates/horizon-client-render/src/gfx.rs": (
        "4ff6b7262846e6dbcdc978f752818cb2e8d91899ac6937407db94f5274f5284d",
        (
            ("horizon_client_render::gfx::legacy_orbital_frame",
             "fn legacy_orbital_frame(frame: &Frame<'_>) {"),
        ),
    ),
    "crates/horizon-client-render/src/camera.rs": (
        "fd76ce232386dff8d1e4ac704251dfb7665bacfc89778540bbf531a3b8a974be",
        (
            ("horizon_client_render::camera::view_matrix_default",
             "fn view_matrix_default(yaw: f32, pitch: f32) -> glam::Mat4 {"),
        ),
    ),
    "crates/horizon-core/src/random/hash.rs": (
        "0f88a127a1ba2705d3d6c0ebd009324d6ba86ffa2d27edf2d2a84765e690e4a3",
        (
            ("horizon_core::random::hash::seed_from_hash",
             "pub fn seed_from_hash(name: &str) -> [u64; 2] {"),
        ),
    ),
    "crates/horizon-world/src/worldgen/overrides.rs": (
        "d4360b1bca606023feb480b98fb85e250d828679f5ce85b5dbdf87fc6f0acc15",
        (
            ("horizon_world::worldgen::overrides::WorldgenOverrides::apply",
             "    pub fn apply("),
            ("horizon_world::worldgen::overrides::WorldgenOverrides::level_height",
             "    pub fn level_height(&self, level: &str) -> Option<LevelHeight> {"),
        ),
    ),
    "crates/horizon-world/src/worldgen/biome_temperature.rs": (
        "e298ecf6245e1f32951765c67cf320f4611717943b9d5cdbe876e0f3ea3e1ffc",
        (
            (TEMPERATURE_SLOT,
             "fn height_adjusted(settings: BiomeTemperatureSettings, position: BlockPos, sea_level: i32) -> f32 {"),
        ),
    ),
    "crates/horizon-world/src/worldgen/structure/legacy.rs": (
        "434a576619ddf2636a46325e6ae475b091e72761a78bf9845edab8ae9d16134e",
        (
            ("horizon_world::worldgen::structure::legacy::make_bounding_box",
             "pub fn make_bounding_box("),
        ),
    ),
    "crates/horizon-world/src/worldgen/generator_decoration.rs": (
        "99a607070e9c69a4090514b187a495561e63e87b7c84c93b4a29213f8f565158",
        (
            ("horizon_world::worldgen::generator_decoration::Decoration::decorate",
             "    pub(super) fn decorate("),
        ),
    ),
}

AUXILIARY_SHA256 = {
    "crates/horizon-mod-api-gen/internals.txt": "dab29dbb9943d7a33ff16706c8fd8742e4fd7218492bc3d726aab8a7296740ad",
    "crates/horizon-core/Cargo.toml": "978c19587ea68fbc2c2359bddd602e293183d70e21bbb941a70f5fde599dc1a6",
    "crates/horizon-world/Cargo.toml": "e4053b61250726c62bbc2c9bea01669cde251da84cc2afbe4c4d2b3141c5cc30",
    "Cargo.lock": "f30f8e8a44fb1f9333d726c335ec80a61a6a108f2f10f8666d6a4272ad9330db",
}


def replace_exact(data: bytes, original: bytes, updated: bytes, name: str) -> bytes:
    if data.count(original) != 1:
        raise ValueError(f"Lower native slot anchor changed: {name}")
    return data.replace(original, updated, 1)


def checked_input(source: Path, layers: dict[str, bytes], path: str, digest: str) -> bytes:
    data = layers[path] if path in layers else (source / path).read_bytes()
    if sha(data) != digest:
        raise ValueError(f"Lower native slot source changed: {path}")
    return data


def temperature_delegate(data: bytes) -> bytes:
    # The stock SDK excludes by-value receivers. Preserve the public method and
    # keep Tectonic's existing first STORE of `start` in the slotted function.
    signature = b"    pub fn height_adjusted(self, position: BlockPos, sea_level: i32) -> f32 {\n"
    start = data.index(signature)
    end = data.index(b"\n    }\n}", start) + len(b"\n    }\n}")
    old = data[start:end]
    body = old[len(signature):-len(b"\n    }\n}")]
    body = replace_exact(body, b"self.modifier.modify(position, self.base_temperature)",
                         b"settings.modifier.modify(position, settings.base_temperature)", TEMPERATURE)
    dedented = b"\n".join(line[4:] for line in body.split(b"\n"))
    new = (signature + b"        height_adjusted(self, position, sea_level)\n    }\n}\n\n"
           b"fn height_adjusted(settings: BiomeTemperatureSettings, position: BlockPos, sea_level: i32) -> f32 {\n"
           + dedented + b"\n}")
    return replace_exact(data, old, new, TEMPERATURE)


def expected_targets() -> dict[str, str]:
    return {key(target): path for path, (_, functions) in FUNCTIONS.items()
            for target, _ in functions}


def changes(source: Path, layers: dict[str, bytes]) -> dict[str, bytes]:
    result = {}
    for path, (digest, functions) in FUNCTIONS.items():
        data = checked_input(source, layers, path, digest)
        if path == TEMPERATURE:
            data = temperature_delegate(data)
        for target, anchor in functions:
            anchor = anchor.encode("ascii")
            indent = anchor[:len(anchor) - len(anchor.lstrip())]
            data = replace_exact(data, b"\n" + anchor, b"\n" + indent + MARKER + b"\n" + anchor, target)
        result[path] = data

    catalog = checked_input(source, layers, INTERNALS, AUXILIARY_SHA256[INTERNALS])
    catalog = replace_exact(catalog, TEMPERATURE_METHOD.encode("ascii"),
                            TEMPERATURE_SLOT.encode("ascii"), INTERNALS)
    original_entries = set(catalog.decode("ascii").splitlines())
    additions = sorted(target for _, functions in FUNCTIONS.values() for target, _ in functions
                       if target not in original_entries)
    result[INTERNALS] = catalog + (("\n# Rust Mods lower function replacements.\n" +
                                  "\n".join(additions) + "\n").encode("ascii"))

    for package, dependencies in MANIFESTS.items():
        path = f"crates/{package}/Cargo.toml"
        deps = "".join(f'{name} = {{ path = "../{name}" }}\n' for name, _ in dependencies)
        manifest = checked_input(source, layers, path, AUXILIARY_SHA256[path])
        result[path] = replace_exact(manifest, b"[dependencies]\n",
                                     b"[dependencies]\n" + deps.encode("ascii"), path)
    lock = checked_input(source, layers, "Cargo.lock", AUXILIARY_SHA256["Cargo.lock"])
    for name, dependencies in MANIFESTS.items():
        package = re.search(fr'(?m)^name = "{name}"\nversion = "0\.1\.0"\ndependencies = \[\n.*?\n\]$',
                            lock.decode("utf-8"), re.S)
        if package is None:
            raise ValueError(f"Lower native slot lock package changed: {name}")
        before = package.group().encode("ascii")
        after = before
        for dep, existing_dep in dependencies:
            anchor = f' "{existing_dep}",\n'.encode("ascii")
            after = replace_exact(after, anchor, f' "{dep}",\n'.encode("ascii") + anchor, name)
        lock = replace_exact(lock, before, after, "Cargo.lock")
    result["Cargo.lock"] = lock
    return result


def require_exports(path: Path, build: str, layers: dict[str, bytes]) -> None:
    table = json.loads(path.read_bytes())
    if table["build"]["commit"] != build:
        raise ValueError("Lower native function exports belong to another SDK build")
    missing = []
    for target, source in expected_targets().items():
        refusal = table["refused"].get(target)
        if refusal is not None:
            missing.append(f"{target}: {refusal['why']}")
            continue
        record = table["functions"].get(target)
        if record is None or record["file"] != source:
            missing.append(f"{target}: absent from {source}")
            continue
        start, end = record["span"]
        raw = layers[source]
        if not 0 <= start < end <= len(raw) or sha(raw[start:end]) != record["sha256"]:
            missing.append(f"{target}: source span differs")
    if missing:
        raise ValueError("Lower native slots failed SDK staging:\n" + "\n".join(missing))
