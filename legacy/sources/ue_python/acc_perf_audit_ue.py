import json
from pathlib import Path

import unreal
import acc_perf_audit_core as core


def _log(msg: str):
    unreal.log(str(msg))


def _log_warning(msg: str):
    unreal.log_warning(str(msg))


def _saved_dir() -> str:
    return unreal.Paths.project_saved_dir()


def _project_name() -> str:
    project_file = str(unreal.Paths.get_project_file_path())
    name = Path(project_file).stem
    return name if name else "UnknownProject"


def _get_editor_subsystem():
    try:
        return unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
    except Exception:
        return unreal.UnrealEditorSubsystem()


def _get_world():
    """
    PIE 时优先拿 Game World。
    没开 PIE 时才回退到 Editor World。
    """
    editor_subsystem = _get_editor_subsystem()

    try:
        world = editor_subsystem.get_game_world()
        if world:
            return world
    except Exception:
        pass

    try:
        pie_worlds = unreal.EditorLevelLibrary.get_pie_worlds(False)
        if pie_worlds and len(pie_worlds) > 0:
            return pie_worlds[0]
    except Exception:
        pass

    try:
        world = editor_subsystem.get_editor_world()
        if world:
            return world
    except Exception:
        pass

    raise RuntimeError("没拿到有效 World。请先打开关卡；若要测采集，建议进入 PIE 后再执行。")


def _execute_console_command(command: str):
    world = _get_world()
    unreal.SystemLibrary.execute_console_command(world, command)


def open_saved_profiling_folder():
    folder = Path(_saved_dir()) / "Profiling"
    folder.mkdir(parents=True, exist_ok=True)
    unreal.Paths.launch_file_explorer(str(folder))
    return str(folder)


def start_csv_capture():
    _execute_console_command("csvprofile start")
    _log("已执行: csvprofile start")


def stop_csv_capture():
    _execute_console_command("csvprofile stop")
    _log("已执行: csvprofile stop")


def capture_csv_frames(frames: int = 600):
    frames = max(1, int(frames))
    _execute_console_command(f"csvprofile frames={frames}")
    _log(f"已执行: csvprofile frames={frames}")


def find_latest_csv():
    return core.find_latest_csv(_saved_dir())


def get_latest_csv_path() -> str:
    return core.get_latest_csv_path(_saved_dir())


def generate_json_report():
    report_path = core.generate_json_report(
        saved_dir=_saved_dir(),
        project_name=_project_name(),
    )
    if not report_path:
        _log_warning("没找到 CSV 文件，先执行采集。")
        return None

    _log(f"报告已生成: {report_path}")
    return report_path


def get_latest_ui_summary():
    summary = core.get_latest_ui_summary(saved_dir=_saved_dir())
    if not summary:
        _log_warning("没找到 CSV 文件。")
        return None
    return summary


def get_latest_ui_summary_dict():
    summary = core.get_latest_ui_summary_dict(saved_dir=_saved_dir())
    if not summary:
        _log_warning("没找到 CSV 文件。")
        return None
    return summary


def print_latest_summary():
    summary = get_latest_ui_summary_dict()
    if not summary:
        _log_warning("没有可用 summary。")
        return None

    txt = json.dumps(summary, ensure_ascii=False, indent=2)
    _log(txt)
    return summary


def quick_test(frames: int = 300):
    capture_csv_frames(frames)
    _log("已开始固定帧数采集。等采集结束后，再执行 generate_json_report()。")


# ── 延迟生成报告 ──────────────────────────────

_pending_report_handle = None
_pending_report_wait_frames = 0
_pending_report_max_wait = 0


def _on_tick(delta_time: float):
    """每帧回调：等待 CSV 文件释放后再生成报告"""
    global _pending_report_handle, _pending_report_wait_frames, _pending_report_max_wait

    _pending_report_wait_frames += 1

    # 至少等 30 帧（约 0.5~1 秒），让引擎完成文件写入和关闭
    if _pending_report_wait_frames < 30:
        return

    csv_path = find_latest_csv()
    if csv_path:
        try:
            # 尝试打开文件，测试是否已释放
            with open(csv_path, "r", encoding="utf-8") as f:
                f.readline()

            # 文件可读 → 取消 Tick，生成报告
            _unregister_tick()
            _log("CSV 文件已释放，开始生成报告...")
            report_path = generate_json_report()
            if report_path:
                print_latest_summary()
            return

        except PermissionError:
            pass  # 文件仍被锁定，继续等待

    # 超过最大等待帧数则放弃
    if _pending_report_wait_frames >= _pending_report_max_wait:
        _unregister_tick()
        _log_warning(f"等待 {_pending_report_max_wait} 帧后 CSV 仍被锁定，请稍后手动执行 generate_json_report()")


def _unregister_tick():
    global _pending_report_handle
    if _pending_report_handle is not None:
        unreal.unregister_slate_post_tick_callback(_pending_report_handle)
        _pending_report_handle = None


def stop_and_report(max_wait_frames: int = 300):
    """
    一键停止采集并生成报告。
    先发送 csvprofile stop，然后注册 Tick 回调，
    等文件释放后自动生成报告。
    """
    global _pending_report_handle, _pending_report_wait_frames, _pending_report_max_wait

    # 先清理之前可能残留的回调
    _unregister_tick()

    # 1. 停止 CSV 采集
    stop_csv_capture()

    # 2. 注册每帧回调，等待文件释放后再读取
    _pending_report_wait_frames = 0
    _pending_report_max_wait = max_wait_frames
    _pending_report_handle = unreal.register_slate_post_tick_callback(_on_tick)
    _log(f"已停止采集，等待 CSV 文件释放（最多 {max_wait_frames} 帧）...")