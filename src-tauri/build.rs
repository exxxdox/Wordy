use std::{env, fs, path::PathBuf};

fn main() {
    // 精简 FFmpeg 随 exe 分发：构建时复制到 OUT_DIR，源码再用 include_bytes! 嵌入。
    // 缺少二进制时写入空占位，构建仍可通过，运行时视为「未打包」。
    let manifest = PathBuf::from(env::var("CARGO_MANIFEST_DIR").unwrap());
    let bundled = manifest.join("assets").join("ffmpeg.exe");
    println!("cargo:rerun-if-changed={}", bundled.display());
    let target = PathBuf::from(env::var("OUT_DIR").unwrap()).join("ffmpeg.exe");
    if bundled.is_file() {
        fs::copy(&bundled, &target).expect("无法复制 assets/ffmpeg.exe");
    } else {
        fs::write(&target, []).expect("无法写入 FFmpeg 占位文件");
    }
    tauri_build::build();
}
