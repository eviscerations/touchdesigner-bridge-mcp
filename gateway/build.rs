// Embed the Windows .exe icon (the bridge logo) as a resource so Explorer / the taskbar show it.
// Build-only; a no-op on non-Windows. Fail-soft: if no resource compiler is found, warn and continue
// (the runtime egui window icon is set separately in gui.rs and does not depend on this).
fn main() {
    #[cfg(windows)]
    {
        println!("cargo:rerun-if-changed=assets/app.ico");
        let mut res = winresource::WindowsResource::new();
        res.set_icon("assets/app.ico");
        if let Err(e) = res.compile() {
            println!("cargo:warning=could not embed exe icon: {e}");
        }
    }
}
