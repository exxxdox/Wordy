fn main() {
    // 嵌入 exe 文件图标：没有这一步，资源管理器只显示默认图标。
    println!("cargo:rerun-if-changed=wordy.rc");
    println!("cargo:rerun-if-changed=rust/assets/wordy.ico");
    let _ = embed_resource::compile("wordy.rc", embed_resource::NONE);
}
