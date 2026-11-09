//! Instance identity follows the profile, without putting a path in IPC names.
use sha2::{Digest, Sha256};
use std::io;
use std::path::Path;

pub fn identifier(
    base: &str,
    profile: &Path,
    default_profile: Option<&Path>,
) -> io::Result<String> {
    let canonical = profile.canonicalize()?;
    let normalized = |path: &Path| {
        let value = path.to_string_lossy().into_owned();
        if cfg!(windows) {
            value.to_lowercase()
        } else {
            value
        }
    };
    let profile_name = normalized(&canonical);
    if default_profile
        .and_then(|path| path.canonicalize().ok())
        .map(|path| normalized(&path) == profile_name)
        .unwrap_or(false)
    {
        return Ok(base.to_owned());
    }
    let digest = Sha256::digest(profile_name.as_bytes());
    // 128 hash bits keep collisions negligible while leaving enough room for
    // the macOS plugin's /tmp/<identifier>_si.sock path (under 100 bytes).
    let suffix: String = digest[..16]
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect();
    Ok(format!("{base}.profile{suffix}"))
}

/// Existing installations retain their original identifier and browser storage.
/// Only explicit alternate data directories need a separate instance namespace.
pub fn configured_identifier(base: &str) -> Result<String, crate::sidecar::SidecarError> {
    if std::env::var_os("LOCALMIND_DATA_DIR").is_none_or(|value| value.is_empty()) {
        return Ok(base.to_owned());
    }
    let profile = crate::sidecar::data_dir()?;
    let default_profile = dirs::data_dir().map(|path| path.join("LocalMind"));
    identifier(base, &profile, default_profile.as_deref())
        .map_err(crate::sidecar::SidecarError::DataDirectoryIo)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn equivalent_profile_paths_use_one_namespace_and_other_profiles_do_not() {
        let root =
            std::env::temp_dir().join(format!("localmind-instance-{}", rand::random::<u64>()));
        let first = root.join("first");
        let second = root.join("second");
        std::fs::create_dir_all(first.join("child")).unwrap();
        std::fs::create_dir_all(&second).unwrap();
        let base = "com.localmind.desktop";
        assert_eq!(identifier(base, &first, Some(&first)).unwrap(), base);
        let isolated = identifier(base, &second, Some(&first)).unwrap();
        assert_ne!(isolated, base);
        assert!(format!("/tmp/{isolated}_si.sock").len() < 100);
        assert!(!isolated.contains(root.to_str().unwrap()));
        assert_eq!(
            identifier(base, &first.join("child").join(".."), None).unwrap(),
            identifier(base, &first, None).unwrap()
        );
        assert_ne!(identifier(base, &first, None).unwrap(), isolated);
        std::fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn missing_profile_cannot_silently_join_another_instance() {
        let missing =
            std::env::temp_dir().join(format!("localmind-missing-{}", rand::random::<u64>()));
        assert!(identifier("com.localmind.desktop", &missing, None).is_err());
    }
}
