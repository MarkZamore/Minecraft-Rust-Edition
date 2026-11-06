//! Validate Horizon's generated v2 data archive against the loose-file receipt.
use crate::{ensure_unlinked, hash_reader};
use anyhow::{Result, ensure};
use sha2::Sha256;
use std::{
    collections::BTreeMap,
    fs::{self, File},
    io::{BufReader, Read},
    path::Path,
};

const FILE_NAME: &str = "horizon-data.bin";
const HEADER_SIZE: usize = 40;
const ENTRY_SIZE: usize = 24;

fn temporary(name: &str) -> bool {
    name.strip_prefix("horizon-data.bin.")
        .and_then(|tail| tail.strip_suffix(".tmp"))
        .is_some_and(|pid| !pid.is_empty() && pid.bytes().all(|digit| digit.is_ascii_digit()))
}

pub(super) fn generated(name: &str) -> bool {
    name == FILE_NAME || temporary(name)
}

pub(super) fn reconcile(root: &Path, expected: &BTreeMap<String, String>) -> Result<()> {
    for entry in fs::read_dir(root)? {
        let entry = entry?;
        if entry.file_name().to_str().is_some_and(temporary) && entry.file_type()?.is_file() {
            fs::remove_file(entry.path())?;
        }
    }
    let archive = root.join(FILE_NAME);
    if ensure_unlinked(&archive)?.is_some()
        && let Err(error) = validate(root, &archive, expected)
    {
        eprintln!("Rebuilding only the generated Horizon data archive: {error}");
        fs::remove_file(&archive)?;
    }
    Ok(())
}

fn archived(name: &str) -> bool {
    !name.ends_with(".ogg")
        && ["data/", "assets/", "worldgen/structure/"]
            .iter()
            .any(|prefix| name.starts_with(prefix))
}

fn validate(root: &Path, archive: &Path, expected: &BTreeMap<String, String>) -> Result<()> {
    let members: Vec<_> = expected.iter().filter(|(name, _)| archived(name)).collect();
    let canonical = fs::canonicalize(root)?;
    let path = canonical.as_os_str().as_encoded_bytes();
    let source = fs::read(root.join("horizon-resources.json"))?;
    let names: Vec<_> = members.iter().flat_map(|(name, _)| name.bytes()).collect();
    let file = File::open(archive)?;
    let size = file.metadata()?.len();
    let mut reader = BufReader::with_capacity(128 * 1024, file);
    let mut header = [0; HEADER_SIZE];
    reader.read_exact(&mut header)?;
    ensure!(
        &header[..8] == b"HZDATA\0\0" && little(&header[8..12]) == 2,
        "Unsupported Horizon data archive"
    );
    ensure!(
        little(&header[12..16]) == members.len() as u64
            && little(&header[16..24]) == path.len() as u64
            && little(&header[24..32]) == source.len() as u64
            && little(&header[32..40]) == names.len() as u64,
        "Horizon data archive index differs from preparation"
    );
    verify_padded(&mut reader, path)?;
    verify_padded(&mut reader, &source)?;
    let mut index = vec![0; ENTRY_SIZE * members.len()];
    reader.read_exact(&mut index)?;
    verify_padded(&mut reader, &names)?;
    let mut data_offset = (HEADER_SIZE
        + path.len().next_multiple_of(8)
        + source.len().next_multiple_of(8)
        + index.len()
        + names.len().next_multiple_of(8)) as u64;
    let mut name_offset = 0;
    let mut buffer = [0; 65536];
    for ((name, expected_hash), entry) in members.into_iter().zip(index.chunks_exact(ENTRY_SIZE)) {
        let length = little(&entry[16..24]);
        ensure!(
            little(&entry[..4]) == name_offset
                && little(&entry[4..8]) == name.len() as u64
                && little(&entry[8..16]) == data_offset
                && data_offset
                    .checked_add(length)
                    .is_some_and(|end| end <= size),
            "Invalid Horizon data archive entry: {name}"
        );
        let mut bytes = (&mut reader).take(length);
        ensure!(
            hash_reader::<Sha256>(&mut bytes, &mut buffer)? == *expected_hash && bytes.limit() == 0,
            "Horizon data archive content differs: {name}"
        );
        name_offset += name.len() as u64;
        data_offset += length;
    }
    ensure!(
        data_offset == size,
        "Unexpected trailing Horizon data archive bytes"
    );
    Ok(())
}

fn little(bytes: &[u8]) -> u64 {
    let mut value = [0; 8];
    value[..bytes.len()].copy_from_slice(bytes);
    u64::from_le_bytes(value)
}

fn verify_padded(reader: &mut impl Read, expected: &[u8]) -> Result<()> {
    let mut field = vec![0; expected.len().next_multiple_of(8)];
    reader.read_exact(&mut field)?;
    ensure!(
        field[..expected.len()] == *expected,
        "Horizon data archive metadata differs"
    );
    Ok(())
}
