# -*- coding: utf-8 -*-
"""开发工具：用示例数据启动 KeyLens 主窗口（不装键盘钩子），1.8 秒后截图并退出。

用途：验证界面改动效果，不必先跑正式程序、也不会记录真实按键。
截图保存到 ``tools/_snap.png``；示例数据库在 ``tools/_snap.db``，删除后自动重建。

用法：
    python tools/_snap_debug.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main() -> int:
    from keylens import preview
    from keylens import report as report_module
    from keylens.core.tracker import Config, Tracker
    from keylens.ui.app import KeyLensApp

    here = os.path.dirname(os.path.abspath(__file__))
    config = Config()
    config.data_dir = here

    # 示例数据库：只有键位与次数，没有任何真实输入内容（建完即交给界面读）
    _ = preview.build_sample_database(os.path.join(here, "_snap.db"), total=2000)

    # Tracker 只初始化状态，不 start()：不安装键盘钩子、不写真实按键
    record = Tracker(config)

    # KeyLensApp 内部自建 root 窗口，这里**不要**再 tk.Tk()，
    # 否则会多出一个 200x200 的空白 "tk" 窗口
    gui = KeyLensApp(record, config, report_module=report_module, show_overlay=False)
    root = gui.root
    out = os.path.join(here, "_snap.png")

    def _do_snap() -> None:
        try:
            from PIL import ImageGrab

            root.update_idletasks()
            ImageGrab.grab().save(out)
            print("saved:", out)
        except Exception as exc:  # 调试工具：失败也只打印，不中断
            print("snap failed:", exc)
        finally:
            # 先取消所有待执行的定时回调，否则退出时会抛
            # "invalid command name ..._tick" 的噪音（无害但难看）
            try:
                timers: list = root.tk.call("after", "info")  # pyright: ignore[reportUnknownMemberType]
                for timer in timers:  # pyright: ignore[reportUnknownVariableType]
                    root.after_cancel(timer)  # pyright: ignore[reportAny]
            except Exception:
                pass
            try:
                gui.on_quit()
            except Exception:
                root.destroy()

    def _snap() -> None:
        # 置顶再截，避免被 IDE 等窗口挡住
        _ = root.attributes("-topmost", True)  # pyright: ignore[reportUnknownMemberType]
        _ = root.lift()  # pyright: ignore[reportUnknownMemberType]
        root.focus_force()
        root.update()
        _ = root.after(600, _do_snap)

    _ = root.after(1800, _snap)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
