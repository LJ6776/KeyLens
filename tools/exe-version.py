# Windows 版本信息资源：让系统与杀毒软件能识别这个 exe 的名称、用途与厂商。
# 注意：本文件会被 PyInstaller 直接 eval()，必须保持为单个表达式（不能 import/赋值）。
# 结构遵循新版 PyInstaller：VSVersionInfo(ffi=FixedFileInfo(...), kids=[...])
#
# 本文件已从 keylens\exe-version.py 迁移到 tools\，内容未改动。
VSVersionInfo(
    ffi=FixedFileInfo(
        filevers=(1, 0, 0, 0),
        prodvers=(1, 0, 0, 0),
        mask=0x3F,
        flags=0x0,
        OS=0x40004,
        fileType=0x1,
        subtype=0x0,
        date=(0, 0),
    ),
    kids=[
        StringFileInfo(
            [
                StringTable(
                    "080404b0",
                    [
                        StringStruct("CompanyName", "KeyLens 本机自用"),
                        StringStruct(
                            "FileDescription", "KeyLens 本机按键统计（可见窗口，数据只存本地）"
                        ),
                        StringStruct("FileVersion", "1.0.0.0"),
                        StringStruct("InternalName", "KeyLens"),
                        StringStruct("LegalCopyright", "本机自用，无网络传输"),
                        StringStruct("OriginalFilename", "KeyLens.exe"),
                        StringStruct("ProductName", "KeyLens"),
                        StringStruct("ProductVersion", "1.0.0.0"),
                    ],
                )
            ]
        ),
        VarFileInfo([VarStruct("Translation", [2052, 1200])]),
    ],
)
