//! Keep the launcher and all of its descendants owned by the desktop process.
#[cfg(windows)]
mod platform {
    use std::mem::{size_of, zeroed};
    use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle};
    use std::process::Child;
    use windows_sys::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
        SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };

    pub struct Job(OwnedHandle);

    impl Job {
        pub fn attach(child: &Child) -> Result<Self, String> {
            unsafe {
                let raw = CreateJobObjectW(std::ptr::null(), std::ptr::null());
                if raw.is_null() { return Err(std::io::Error::last_os_error().to_string()); }
                let handle = OwnedHandle::from_raw_handle(raw);
                let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = zeroed();
                limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
                if SetInformationJobObject(
                    handle.as_raw_handle(), JobObjectExtendedLimitInformation,
                    &limits as *const _ as *const _, size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
                ) == 0 || AssignProcessToJobObject(handle.as_raw_handle(), child.as_raw_handle()) == 0 {
                    return Err(std::io::Error::last_os_error().to_string());
                }
                Ok(Self(handle))
            }
        }
    }

    impl Drop for Job {
        fn drop(&mut self) { let _ = self.0.as_raw_handle(); }
    }
}

#[cfg(not(windows))]
mod platform {
    pub struct Job;
    impl Job {
        pub fn attach(_: &std::process::Child) -> Result<Self, String> {
            Err("This desktop build currently supports Windows only.".into())
        }
    }
}

pub use platform::Job;
