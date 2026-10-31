//! Own a service and, on Windows, every process it starts (including frozen
//! Python bootloader children). The job handle is never inherited, so Windows
//! also tears down the service tree if the desktop app crashes.

use std::io;
use std::process::{Child, Command, ExitStatus};

pub struct OwnedChild {
    child: Child,
    #[cfg(windows)]
    job: std::os::windows::io::OwnedHandle,
}

impl OwnedChild {
    pub fn spawn(command: &mut Command) -> io::Result<Self> {
        #[cfg(windows)]
        {
            use std::os::windows::{io::AsRawHandle, process::CommandExt};
            use windows_sys::Win32::System::Threading::{CREATE_NO_WINDOW, CREATE_SUSPENDED};
            // Assignment must precede execution: a one-file Python executable
            // can otherwise create its worker before it belongs to our job.
            command.creation_flags(CREATE_NO_WINDOW | CREATE_SUSPENDED);
            let job = windows::new_job()?;
            let mut child = command.spawn()?;
            if let Err(error) = windows::assign_and_resume(job.as_raw_handle(), &child) {
                let _ = child.kill();
                let _ = child.wait();
                return Err(error);
            }
            Ok(Self { child, job })
        }
        #[cfg(not(windows))]
        {
            command.spawn().map(|child| Self { child })
        }
    }

    pub fn try_wait(&mut self) -> io::Result<Option<ExitStatus>> {
        self.child.try_wait()
    }

    pub fn wait(&mut self) -> io::Result<ExitStatus> {
        self.child.wait()
    }
}

impl Drop for OwnedChild {
    fn drop(&mut self) {
        #[cfg(windows)]
        {
            use std::os::windows::io::AsRawHandle;
            // Terminate the job even when its original process already exited.
            // Closing the handle below supplies the crash-cleanup guarantee.
            unsafe {
                windows_sys::Win32::System::JobObjects::TerminateJobObject(
                    self.job.as_raw_handle(),
                    1,
                );
            }
        }
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

#[cfg(windows)]
mod windows {
    use super::*;
    use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle, RawHandle};
    use windows_sys::Win32::{
        Foundation::INVALID_HANDLE_VALUE,
        System::{
            Diagnostics::ToolHelp::{
                CreateToolhelp32Snapshot, Thread32First, Thread32Next, TH32CS_SNAPTHREAD,
                THREADENTRY32,
            },
            JobObjects::{
                AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
                SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
                JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
            },
            Threading::{OpenThread, ResumeThread, THREAD_SUSPEND_RESUME},
        },
    };

    pub(super) fn new_job() -> io::Result<OwnedHandle> {
        // Null security attributes make this unnamed handle non-inheritable.
        let raw = unsafe { CreateJobObjectW(std::ptr::null(), std::ptr::null()) };
        if raw.is_null() {
            return Err(io::Error::last_os_error());
        }
        let job = unsafe { OwnedHandle::from_raw_handle(raw) };
        let mut limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        if unsafe {
            SetInformationJobObject(
                job.as_raw_handle(),
                JobObjectExtendedLimitInformation,
                &limits as *const _ as *const _,
                std::mem::size_of_val(&limits) as u32,
            )
        } == 0
        {
            return Err(io::Error::last_os_error());
        }
        Ok(job)
    }

    pub(super) fn assign_and_resume(job: RawHandle, child: &Child) -> io::Result<()> {
        if unsafe { AssignProcessToJobObject(job, child.as_raw_handle()) } == 0 {
            return Err(io::Error::last_os_error());
        }
        // std::process::Child does not expose the primary thread handle. A
        // suspended new process has not run user code or created worker threads.
        let raw = unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0) };
        if raw == INVALID_HANDLE_VALUE {
            return Err(io::Error::last_os_error());
        }
        let snapshot = unsafe { OwnedHandle::from_raw_handle(raw) };
        let mut entry = THREADENTRY32 {
            dwSize: std::mem::size_of::<THREADENTRY32>() as u32,
            ..Default::default()
        };
        let mut found = unsafe { Thread32First(snapshot.as_raw_handle(), &mut entry) };
        while found != 0 {
            if entry.th32OwnerProcessID == child.id() {
                let raw = unsafe { OpenThread(THREAD_SUSPEND_RESUME, 0, entry.th32ThreadID) };
                if raw.is_null() {
                    return Err(io::Error::last_os_error());
                }
                let thread = unsafe { OwnedHandle::from_raw_handle(raw) };
                if unsafe { ResumeThread(thread.as_raw_handle()) } == u32::MAX {
                    return Err(io::Error::last_os_error());
                }
                return Ok(());
            }
            found = unsafe { Thread32Next(snapshot.as_raw_handle(), &mut entry) };
        }
        Err(io::Error::new(
            io::ErrorKind::NotFound,
            "service primary thread was not found",
        ))
    }
}

#[cfg(all(test, windows))]
mod tests {
    use super::*;
    use crate::process::background_command;
    use std::fs::{self, OpenOptions};
    use std::os::windows::{
        fs::OpenOptionsExt,
        io::{AsRawHandle, FromRawHandle, OwnedHandle},
    };
    use std::path::{Path, PathBuf};
    use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
    use windows_sys::Win32::System::Threading::{
        OpenProcess, WaitForSingleObject, PROCESS_SYNCHRONIZE,
    };

    struct FixtureDir(PathBuf);
    impl FixtureDir {
        fn new() -> Self {
            let nonce = SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos();
            let dir = std::env::temp_dir()
                .join(format!("localmind-process-{}-{nonce}", std::process::id()));
            fs::create_dir(&dir).unwrap();
            Self(dir)
        }
    }
    impl Drop for FixtureDir {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    fn fixture(dir: &Path, role: &str) -> Command {
        let mut command = background_command(std::env::current_exe().unwrap());
        command
            .args([
                "--exact",
                "process::owned::tests::tree_fixture",
                "--ignored",
                "--nocapture",
            ])
            .env("LOCALMIND_PROCESS_TEST_DIR", dir)
            .env("LOCALMIND_PROCESS_TEST_ROLE", role)
            .stdout(std::process::Stdio::null());
        command
    }

    fn wait_for_file(path: &Path) -> String {
        let deadline = Instant::now() + Duration::from_secs(8);
        loop {
            if let Ok(contents) = fs::read_to_string(path) {
                if !contents.is_empty() {
                    return contents;
                }
            }
            assert!(
                Instant::now() < deadline,
                "fixture did not become ready: {path:?}"
            );
            std::thread::sleep(Duration::from_millis(20));
        }
    }

    fn leaf_handle(dir: &Path) -> OwnedHandle {
        let pid: u32 = wait_for_file(&dir.join("leaf-ready")).parse().unwrap();
        let raw = unsafe { OpenProcess(PROCESS_SYNCHRONIZE, 0, pid) };
        assert!(
            !raw.is_null(),
            "could not open fixture process: {}",
            io::Error::last_os_error()
        );
        unsafe { OwnedHandle::from_raw_handle(raw) }
    }

    fn assert_leaf_stopped(handle: &OwnedHandle, dir: &Path) {
        assert_eq!(
            unsafe { WaitForSingleObject(handle.as_raw_handle(), 5_000) },
            0,
            "owned descendant survived shutdown"
        );
        // This is the upgrade failure: the descendant holds an exclusive file
        // lock even after the original bootloader/desktop has gone away.
        let deadline = Instant::now() + Duration::from_secs(2);
        loop {
            if OpenOptions::new()
                .write(true)
                .share_mode(0)
                .open(dir.join("locked-file"))
                .is_ok()
            {
                break;
            }
            assert!(
                Instant::now() < deadline,
                "descendant file lock survived termination"
            );
            std::thread::sleep(Duration::from_millis(20));
        }
    }

    #[test]
    #[ignore = "fixture launched by ownership tests"]
    #[allow(clippy::zombie_processes)] // Deliberately reproduce an exited bootloader with a live worker.
    fn tree_fixture() {
        #[link(name = "kernel32")]
        extern "system" {
            fn GetConsoleWindow() -> *mut std::ffi::c_void;
        }
        assert!(
            unsafe { GetConsoleWindow() }.is_null(),
            "owned service created a console window"
        );
        let dir = PathBuf::from(std::env::var_os("LOCALMIND_PROCESS_TEST_DIR").unwrap());
        let role = std::env::var("LOCALMIND_PROCESS_TEST_ROLE").unwrap();
        if role == "leaf" {
            let _lock = OpenOptions::new()
                .write(true)
                .create_new(true)
                .share_mode(0)
                .open(dir.join("locked-file"))
                .unwrap();
            fs::write(dir.join("leaf-ready"), std::process::id().to_string()).unwrap();
            std::thread::sleep(Duration::from_secs(30));
        } else if role == "owner" {
            let _child = OwnedChild::spawn(&mut fixture(&dir, "root")).unwrap();
            wait_for_file(&dir.join("leaf-ready"));
            fs::write(dir.join("owner-ready"), "ready").unwrap();
            std::thread::sleep(Duration::from_secs(30));
        } else {
            // Intentionally unmanaged: it must inherit the root's OS job.
            let mut child = fixture(&dir, "leaf").spawn().unwrap();
            wait_for_file(&dir.join("leaf-ready"));
            if role == "root-exits" {
                return;
            }
            let _ = child.wait();
        }
    }

    #[test]
    fn dropping_service_releases_descendant_file_lock() {
        let dir = FixtureDir::new();
        let child = OwnedChild::spawn(&mut fixture(&dir.0, "root")).unwrap();
        let leaf = leaf_handle(&dir.0);
        drop(child);
        assert_leaf_stopped(&leaf, &dir.0);
    }

    #[test]
    fn exited_bootloader_does_not_leave_worker_behind() {
        let dir = FixtureDir::new();
        let mut child = OwnedChild::spawn(&mut fixture(&dir.0, "root-exits")).unwrap();
        let leaf = leaf_handle(&dir.0);
        assert!(child.wait().unwrap().success());
        drop(child);
        assert_leaf_stopped(&leaf, &dir.0);
    }

    #[test]
    fn crashed_owner_releases_service_tree_without_rust_drop() {
        let dir = FixtureDir::new();
        let mut owner = fixture(&dir.0, "owner").spawn().unwrap();
        let leaf = leaf_handle(&dir.0);
        wait_for_file(&dir.0.join("owner-ready"));
        owner.kill().unwrap();
        owner.wait().unwrap();
        assert_leaf_stopped(&leaf, &dir.0);
    }

    #[test]
    fn shutdown_leaves_an_independent_service_running() {
        let first = FixtureDir::new();
        let second = FixtureDir::new();
        let owned = OwnedChild::spawn(&mut fixture(&first.0, "root")).unwrap();
        let mut unrelated = OwnedChild::spawn(&mut fixture(&second.0, "leaf")).unwrap();
        let leaf = leaf_handle(&first.0);
        let other = leaf_handle(&second.0);
        drop(owned);
        assert_leaf_stopped(&leaf, &first.0);
        assert!(unrelated.try_wait().unwrap().is_none());
        assert_eq!(
            unsafe { WaitForSingleObject(other.as_raw_handle(), 0) },
            258
        );
        drop(unrelated);
        assert_leaf_stopped(&other, &second.0);
    }
}
