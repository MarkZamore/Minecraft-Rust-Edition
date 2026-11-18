//! Launcher-owned files are read off the render thread. Invalid snapshots never
//! replace the last valid cards, and heartbeat freshness comes only from a
//! successfully presented native UI frame.
use super::{protocol, raster};
use std::{
    fs::{self, OpenOptions},
    io::{Read, Write},
    path::{Path, PathBuf},
    sync::{
        Arc, Mutex, OnceLock,
        atomic::{AtomicU32, AtomicU64, Ordering},
    },
    thread,
    time::{Duration, SystemTime, UNIX_EPOCH},
};

pub(super) struct Prepared {
    pub revision: u64,
    pub snapshot: protocol::Snapshot,
    pub cards: Vec<raster::CardBitmap>,
}

#[derive(Default)]
struct Store {
    prepared: Option<Arc<Prepared>>,
    width: u32,
    revision: u64,
    input_error: Option<String>,
    raster_error: Option<String>,
}

impl Store {
    fn availability_error(&self) -> Option<String> {
        self.raster_error.clone().or_else(|| {
            // A torn update is not a failed renderer: keep the last complete
            // snapshot until its original timestamps expire naturally.
            self.prepared.is_none().then(|| {
                self.input_error
                    .clone()
                    .unwrap_or_else(|| "Waiting for a complete notice snapshot".into())
            })
        })
    }
}

#[derive(Clone, Default)]
struct Presented {
    timestamp_ms: u64,
    rendered_count: usize,
    packaged_fonts: bool,
    error: Option<String>,
}

pub(super) struct Service {
    directory: PathBuf,
    store: Mutex<Store>,
    presented: Mutex<Presented>,
    viewport_width: AtomicU32,
    temporary_id: AtomicU64,
}

impl Service {
    fn new(directory: PathBuf) -> Self {
        Self {
            directory,
            store: Mutex::new(Store::default()),
            presented: Mutex::new(Presented::default()),
            viewport_width: AtomicU32::new(0),
            temporary_id: AtomicU64::new(0),
        }
    }

    pub(super) fn prepared(&self, physical_width: u32) -> Option<Arc<Prepared>> {
        self.viewport_width.store(physical_width, Ordering::Release);
        self.store
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .prepared
            .clone()
    }

    pub(super) fn report_presented(&self, rendered_count: usize, packaged_fonts: bool) {
        let mut frame = self.presented.lock().unwrap_or_else(|e| e.into_inner());
        frame.timestamp_ms = unix_ms();
        frame.rendered_count = rendered_count.min(3);
        frame.packaged_fonts = packaged_fonts;
        frame.error = None;
    }

    pub(super) fn report_error(&self, error: &str) {
        // Do not renew freshness on an attempted or failed render.
        self.presented
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .error = Some(error.chars().take(320).collect());
    }

    fn accept_documents(
        &self,
        style: &[u8],
        notices: &[u8],
        width: u32,
        now: u64,
        cached_fonts: &mut Option<raster::PackagedFonts>,
    ) {
        let snapshot = match protocol::parse_snapshot(style, notices, now) {
            Ok(snapshot) => snapshot,
            Err(error) => {
                self.store
                    .lock()
                    .unwrap_or_else(|e| e.into_inner())
                    .input_error = Some(error);
                return;
            }
        };
        {
            let mut store = self.store.lock().unwrap_or_else(|e| e.into_inner());
            store.input_error = None;
            if store.width == width
                && store
                    .prepared
                    .as_ref()
                    .is_some_and(|old| old.snapshot == snapshot)
            {
                store.raster_error = None;
                return;
            }
        }
        // GDI/font work and allocations must never hold the lock read by UI frames.
        let rasterized = (|| {
            let fonts = if snapshot.style.packaged_fonts {
                if cached_fonts.is_none() {
                    *cached_fonts = Some(read_packaged_fonts(&self.directory)?);
                }
                cached_fonts.as_ref()
            } else {
                None
            };
            raster::rasterize(&snapshot, width, fonts)
        })();
        let mut store = self.store.lock().unwrap_or_else(|e| e.into_inner());
        match rasterized {
            Ok(cards) => {
                store.revision = store.revision.wrapping_add(1);
                store.prepared = Some(Arc::new(Prepared {
                    revision: store.revision,
                    snapshot,
                    cards,
                }));
                store.width = width;
                store.raster_error = None;
            }
            Err(error) => store.raster_error = Some(error),
        }
    }

    fn poll(&self, cached_fonts: &mut Option<raster::PackagedFonts>) {
        let width = self.viewport_width.load(Ordering::Acquire);
        if width == 0 {
            return;
        }
        match read_documents(&self.directory) {
            Ok((style, notices)) => {
                self.accept_documents(&style, &notices, width, unix_ms(), cached_fonts)
            }
            Err(error) => {
                self.store
                    .lock()
                    .unwrap_or_else(|e| e.into_inner())
                    .input_error = Some(error)
            }
        }
    }

    fn heartbeat(&self) -> Result<(), String> {
        let frame = self
            .presented
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .clone();
        let error = frame.error.clone().or_else(|| {
            self.store
                .lock()
                .unwrap_or_else(|e| e.into_inner())
                .availability_error()
        });
        let bytes = heartbeat_bytes(&frame, error.as_deref());
        let id = self.temporary_id.fetch_add(1, Ordering::Relaxed);
        write_atomic(&self.directory, &bytes, id)
    }
}

pub(super) fn service() -> Option<Arc<Service>> {
    static SHARED: OnceLock<Option<Arc<Service>>> = OnceLock::new();
    SHARED
        .get_or_init(|| {
            let directory = PathBuf::from(std::env::var_os("LANMINECRAFT_NOTICE_DIRECTORY")?);
            if !directory.is_absolute() || !ordinary_directory(&directory) {
                return None;
            }
            let service = Arc::new(Service::new(directory));
            let worker = service.clone();
            thread::Builder::new()
                .name("Launcher notices".into())
                .spawn(move || {
                    let mut tick = false;
                    let mut cached_fonts = None;
                    loop {
                        worker.poll(&mut cached_fonts);
                        // Snapshot reads every 250 ms; heartbeat writes every 500 ms.
                        tick = !tick;
                        if tick {
                            // A transient sharing violation must not affect the game.
                            let _ = worker.heartbeat();
                        }
                        thread::sleep(Duration::from_millis(250));
                    }
                })
                .ok()?;
            Some(service)
        })
        .clone()
}

pub(super) fn unix_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .min(u64::MAX as u128) as u64
}

fn ordinary_directory(path: &Path) -> bool {
    fs::symlink_metadata(path).is_ok_and(|metadata| metadata.is_dir() && !reparse(&metadata))
}

fn reparse(metadata: &fs::Metadata) -> bool {
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        metadata.file_attributes() & 0x400 != 0
    }
    #[cfg(not(windows))]
    {
        metadata.file_type().is_symlink()
    }
}

fn read_document(path: &Path) -> Result<Vec<u8>, String> {
    read_bounded_file(path, protocol::MAX_BYTES)
}

fn read_bounded_file(path: &Path, max_bytes: usize) -> Result<Vec<u8>, String> {
    let metadata = fs::symlink_metadata(path).map_err(|_| "Notice document unavailable")?;
    if !metadata.is_file() || reparse(&metadata) || metadata.len() > max_bytes as u64 {
        return Err("Notice document is not a bounded ordinary file".into());
    }
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        options.custom_flags(0x0020_0000); // FILE_FLAG_OPEN_REPARSE_POINT
    }
    let file = options
        .open(path)
        .map_err(|_| "Notice document could not be opened")?;
    let opened = file
        .metadata()
        .map_err(|_| "Notice document metadata unavailable")?;
    if !opened.is_file() || reparse(&opened) || opened.len() > max_bytes as u64 {
        return Err("Notice document changed to a non-ordinary file".into());
    }
    let mut bytes = Vec::with_capacity(opened.len() as usize);
    file.take(max_bytes as u64 + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| "Notice document could not be read")?;
    if bytes.len() > max_bytes {
        return Err("Notice document exceeds its size limit".into());
    }
    Ok(bytes)
}

fn read_packaged_fonts(directory: &Path) -> Result<raster::PackagedFonts, String> {
    if !ordinary_directory(directory) {
        return Err("Notice font session directory is unavailable".into());
    }
    let regular = read_bounded_file(
        &directory.join("Roboto-Regular.ttf"),
        raster::MAX_FONT_BYTES,
    )?;
    let semibold = read_bounded_file(
        &directory.join("Roboto-SemiBold.ttf"),
        raster::MAX_FONT_BYTES,
    )?;
    raster::PackagedFonts::load(regular, semibold)
}

fn read_documents(directory: &Path) -> Result<(Vec<u8>, Vec<u8>), String> {
    if !ordinary_directory(directory) {
        return Err("Notice session directory is unavailable".into());
    }
    let style = directory.join("style.properties");
    let notices = directory.join("notices.properties");
    let first = (read_document(&style)?, read_document(&notices)?);
    let second = (read_document(&style)?, read_document(&notices)?);
    if first != second {
        return Err("Notice snapshot changed during reading".into());
    }
    Ok(first)
}

fn escaped(value: &str) -> String {
    let mut result = String::new();
    for character in value.chars().take(320) {
        match character {
            '\\' => result.push_str("\\\\"),
            '\n' => result.push_str("\\n"),
            '\r' => result.push_str("\\r"),
            '\t' => result.push_str("\\t"),
            '=' | ':' | '#' | '!' => {
                result.push('\\');
                result.push(character);
            }
            c if c.is_control() => result.push(' '),
            c => result.push(c),
        }
    }
    result
}

fn heartbeat_bytes(frame: &Presented, error: Option<&str>) -> Vec<u8> {
    let mut data = format!(
        "backend=native-ui-v1\npid={}\ntimestampUnixMs={}\nrenderedCount={}\ntransformations=0\nerror={}\n",
        std::process::id(),
        frame.timestamp_ms,
        frame.rendered_count.min(3),
        escaped(error.unwrap_or("")),
    );
    if frame.packaged_fonts && error.is_none() {
        data.push_str("fontMode=");
        data.push_str(protocol::ROBOTO_FONT_MODE);
        data.push('\n');
    }
    data.into_bytes()
}

fn write_atomic(directory: &Path, bytes: &[u8], id: u64) -> Result<(), String> {
    if !ordinary_directory(directory) {
        return Err("Notice session directory is unavailable".into());
    }
    let target = directory.join("heartbeat.properties");
    if fs::symlink_metadata(&target).is_ok_and(|m| !m.is_file() || reparse(&m)) {
        return Err("Notice heartbeat target is not an ordinary file".into());
    }
    let temporary = directory.join(format!(".heartbeat.{}.{}.tmp", std::process::id(), id));
    let mut created = false;
    let result = (|| {
        let mut file = OpenOptions::new()
            .create_new(true)
            .write(true)
            .open(&temporary)
            .map_err(|_| "Notice heartbeat temporary file unavailable")?;
        created = true;
        file.write_all(bytes)
            .and_then(|_| file.flush())
            .map_err(|_| "Notice heartbeat write failed")?;
        drop(file);
        fs::rename(&temporary, &target).map_err(|_| "Notice heartbeat replace failed")?;
        Ok(())
    })();
    if result.is_err() && created {
        // Only this exact temporary name, never another session's files.
        let _ = fs::remove_file(&temporary);
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture() -> PathBuf {
        static NEXT: AtomicU64 = AtomicU64::new(0);
        let executable = std::env::current_exe().unwrap();
        let root = executable
            .ancestors()
            .find(|path| path.join("RUN.json").is_file())
            .expect("Compile tests inside a registered agent-runs task directory");
        let directory = root.join(format!(
            "notice-state-test-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&directory).unwrap();
        directory
    }

    #[test]
    fn heartbeat_records_last_presented_frame_even_with_no_notices() {
        let frame = Presented {
            timestamp_ms: 1234,
            rendered_count: 0,
            packaged_fonts: false,
            error: None,
        };
        let text = String::from_utf8(heartbeat_bytes(&frame, None)).unwrap();
        assert!(text.contains("backend=native-ui-v1\n"));
        assert!(text.contains(&format!("pid={}\n", std::process::id())));
        assert!(
            text.contains("timestampUnixMs=1234\nrenderedCount=0\ntransformations=0\nerror=\n")
        );
    }

    #[test]
    fn failed_render_does_not_renew_heartbeat_freshness() {
        let service = Service::new(PathBuf::new());
        service.presented.lock().unwrap().timestamp_ms = 1234;
        service.report_error("native UI unavailable");
        let frame = service.presented.lock().unwrap().clone();
        assert_eq!(frame.timestamp_ms, 1234);
        assert_eq!(frame.error.as_deref(), Some("native UI unavailable"));
    }

    #[test]
    fn heartbeat_atomically_replaces_only_its_own_file() {
        let directory = fixture();
        fs::write(directory.join("heartbeat.properties"), b"old").unwrap();
        let bytes = heartbeat_bytes(
            &Presented {
                timestamp_ms: 55,
                ..Default::default()
            },
            Some("broken\nreason=bad\\value"),
        );
        write_atomic(&directory, &bytes, 0).unwrap();
        assert_eq!(
            fs::read(directory.join("heartbeat.properties")).unwrap(),
            bytes
        );
        assert_eq!(fs::read_dir(&directory).unwrap().count(), 1);
        assert!(!directory.join("session.json").exists());
        fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn colliding_temporary_file_is_not_owned_and_must_not_be_removed() {
        let directory = fixture();
        let other = directory.join(format!(".heartbeat.{}.0.tmp", std::process::id()));
        fs::write(&other, b"not created by this write").unwrap();
        assert!(write_atomic(&directory, b"new heartbeat", 0).is_err());
        assert_eq!(fs::read(&other).unwrap(), b"not created by this write");
        assert!(!directory.join("heartbeat.properties").exists());
        fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn oversized_or_missing_input_is_rejected_without_unbounded_read() {
        let directory = fixture();
        let path = directory.join("notices.properties");
        assert!(read_document(&path).is_err());
        fs::write(&path, vec![b'x'; protocol::MAX_BYTES + 1]).unwrap();
        assert!(read_document(&path).is_err());
        fs::write(&path, b"count=0\ndpiScale=1\n").unwrap();
        assert_eq!(read_document(&path).unwrap(), b"count=0\ndpiScale=1\n");
        fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn invalid_update_keeps_original_notice_expiry() {
        let snapshot = protocol::Snapshot {
            style: protocol::Style {
                bg_argb: 0,
                border_argb: 0,
                text_argb: 0,
                muted_argb: 0,
                font_family: "Tahoma".into(),
                packaged_fonts: false,
                font_size: 14.0,
                width: 320.0,
                gap: 24.0,
                padding: 16.0,
                radius: 8.0,
                rise_ms: 200,
                fade_ms: 350,
                life_ms: 7000,
            },
            notices: vec![protocol::Notice {
                title: "Before".into(),
                body: "Complete".into(),
                created_unix_ms: 1000,
            }],
            dpi_scale: 1.0,
        };
        let original = Arc::new(Prepared {
            revision: 1,
            snapshot,
            cards: vec![],
        });
        let service = Service::new(PathBuf::new());
        service.store.lock().unwrap().prepared = Some(original.clone());
        service.accept_documents(b"broken=", b"count=1\n", 320, 2000, &mut None);
        let store = service.store.lock().unwrap();
        assert!(Arc::ptr_eq(store.prepared.as_ref().unwrap(), &original));
        assert!(original.snapshot.notices[0].is_alive(7999));
        assert!(!original.snapshot.notices[0].is_alive(8000));
        assert!(store.availability_error().is_none());
    }

    const ROBOTO_STYLE: &str = "bgArgb=FF112233\nborderArgb=FF334455\ntextArgb=FFFFFFFF\nmutedArgb=FFEEEEEE\nfontFamily=Roboto\nfontMode=roboto-v1\nfontSize=14\nwidth=320\ngap=24\npadding=16\nradius=8\nriseMs=200\nfadeMs=350\nlifeMs=7000\n";

    #[test]
    fn missing_fonts_cannot_prepare_or_advertise_roboto() {
        let service = Service::new(fixture());
        let mut fonts = None;
        service.accept_documents(
            ROBOTO_STYLE.as_bytes(),
            b"count=0\ndpiScale=1\n",
            640,
            2000,
            &mut fonts,
        );
        assert!(fonts.is_none());
        let store = service.store.lock().unwrap();
        assert!(store.prepared.is_none());
        assert!(store.availability_error().is_some());
        drop(store);
        service.report_presented(0, false);
        service.heartbeat().unwrap();
        let heartbeat = fs::read_to_string(service.directory.join("heartbeat.properties")).unwrap();
        assert!(!heartbeat.contains("fontMode="));
        fs::remove_dir_all(&service.directory).unwrap();
    }

    // Run this exact fixture with the launcher's pinned font directory.
    #[cfg(windows)]
    #[test]
    #[ignore = "requires LANMC_NOTICE_TEST_FONT_DIR containing the packaged launcher fonts"]
    fn shipped_roboto_pair_paints_and_heartbeat_proves_presented_mode() {
        let font_dir = PathBuf::from(std::env::var_os("LANMC_NOTICE_TEST_FONT_DIR").unwrap());
        let service = Service::new(fixture());
        for name in ["Roboto-Regular.ttf", "Roboto-SemiBold.ttf"] {
            fs::copy(font_dir.join(name), service.directory.join(name)).unwrap();
        }
        let mut fonts = None;
        service.accept_documents(
            ROBOTO_STYLE.as_bytes(),
            b"count=0\ndpiScale=1\n",
            640,
            2000,
            &mut fonts,
        );
        assert!(
            fonts.is_some(),
            "{:?}",
            service.store.lock().unwrap().availability_error()
        );
        assert!(service.prepared(640).unwrap().cards.is_empty());
        service.report_presented(0, true);
        service.heartbeat().unwrap();
        let path = service.directory.join("heartbeat.properties");
        assert!(
            fs::read_to_string(&path)
                .unwrap()
                .contains("fontMode=roboto-v1\n")
        );

        service.accept_documents(
            ROBOTO_STYLE.as_bytes(),
            "count=1\ndpiScale=2\n0.title=Игрок подключился\n0.body=Ёж и мир\n0.createdUnixMs=2000\n".as_bytes(),
            960,
            2000,
            &mut fonts,
        );
        let prepared = service.prepared(960).unwrap();
        assert_eq!(
            prepared.cards.len(),
            1,
            "{:?}",
            service.store.lock().unwrap().availability_error()
        );
        assert!(
            prepared.cards[0]
                .rgba
                .chunks_exact(4)
                .any(|rgba| rgba[0] > 100 && rgba[3] > 200)
        );
        service.report_error("not presented");
        service.heartbeat().unwrap();
        assert!(!fs::read_to_string(&path).unwrap().contains("fontMode="));
        drop(fonts);
        fs::remove_dir_all(&service.directory).unwrap();
    }
}
