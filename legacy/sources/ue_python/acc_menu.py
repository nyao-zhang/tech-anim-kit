import unreal
import os
import tkinter as tk
import acc_unreal_tools
import acc_menu_export
import importlib
import webbrowser
import json
import acc_perf_audit_scan
import acc_unreal_api as api
import acc_perf_audit_ue
# 热重载逻辑模块
importlib.reload(acc_unreal_tools)
importlib.reload(acc_perf_audit_scan)
importlib.reload(acc_perf_audit_ue)

# --- 语言状态管理 ---
class AnimcraftLang:
    IS_CN = False
    @classmethod
    def sync(cls):
        # 获取当前编辑器界面'zh-Hans' 或 'en'
        current_culture = unreal.InternationalizationLibrary.get_current_culture()
        cls.IS_CN = current_culture.startswith("zh")
        return cls.IS_CN

def make_cmd_action(command: str):
    @unreal.uclass()
    class _CmdAction(unreal.ToolMenuEntryScript):
        @unreal.ufunction(override=True)
        def execute(self, context):
            unreal.SystemLibrary.execute_console_command(None, command)
            unreal.log(f"[Animcraft] Executed: {command}")
    return _CmdAction()

@unreal.uclass()
class AnimcraftFlatImport(unreal.ToolMenuEntryScript):
    @unreal.ufunction(override=True)
    def execute(self, context):
        acc_unreal_tools.AnimcraftCore.flat_import()

@unreal.uclass()
class AnimcraftReferenceImport(unreal.ToolMenuEntryScript):
    @unreal.ufunction(override=True)
    def execute(self, context):
        acc_unreal_tools.AnimcraftCore.reference_import()

@unreal.uclass()
class AnimcraftExportAction(unreal.ToolMenuEntryScript):
    @unreal.ufunction(override=True)
    def execute(self, context):
        acc_menu_export.plugin_assets_button_clicked()

@unreal.uclass()
class AnimcraftBeginDetection(unreal.ToolMenuEntryScript):
    @unreal.ufunction(override=True)
    def execute(self, context):
        acc_perf_audit_ue.start_csv_capture()

@unreal.uclass()
class AnimcraftStopDetection(unreal.ToolMenuEntryScript):
    @unreal.ufunction(override=True)
    def execute(self, context):
        # unreal.EditorLevelLibrary.editor_end_play()
        # acc_perf_audit_ue.stop_csv_capture()
        # acc_perf_audit_ue.generate_json_report()
        # acc_perf_audit_ue.print_latest_summary()
        acc_perf_audit_ue.stop_and_report()

@unreal.uclass()
class AnimcraftCopyPathAction(unreal.ToolMenuEntryScript):
    @unreal.ufunction(override=True)
    def execute(self, context):
        selected_assets = unreal.EditorUtilityLibrary.get_selected_asset_data()

        if selected_assets:
            asset_data = selected_assets[0]
            asset_cls = asset_data.asset_class_path.asset_name
            ext = ".umap" if str(asset_cls) == "World" else ".uasset"
            disk_path = acc_unreal_tools.AnimcraftCore.ue_path_to_disk_path(str(asset_data.package_name)) + ext
            ue_dir = str(asset_data.package_path)
        else:
            lib = unreal.EditorUtilityLibrary
            folders = (getattr(lib, "get_selected_path_view_folder_paths", lambda: [])() or [
                lib.get_current_content_browser_path()])
            if not folders or not folders[0]:
                return
            folder_str = str(folders[0])
            disk_path = acc_unreal_tools.AnimcraftCore.ue_path_to_disk_path(folder_str)
            ue_dir = folder_str

        # 统一路径分隔符
        disk_path = disk_path.replace("\\", "/")

        # 构造 JSON
        result = json.dumps(
            {
                "path": disk_path,
                "dir": ue_dir,
                "source": "animcraft"
            },
            indent=4
        )

        # Tkinter 剪贴板操作
        r = tk.Tk()
        r.withdraw()
        r.clipboard_clear()
        r.clipboard_append(result)
        r.update()
        r.destroy()

        unreal.log(f"[Animcraft] Copied JSON:\n{result}")


@unreal.uclass()
class AnimcraftCheckRefAction(unreal.ToolMenuEntryScript):
    @unreal.ufunction(override=True)
    def execute(self, context):
        acc_unreal_tools.AnimcraftCore.check_references()


@unreal.uclass()
class AnimcraftDupAction(unreal.ToolMenuEntryScript):
    @unreal.ufunction(override=True)
    def execute(self, context):
        acc_unreal_tools.AnimcraftCore.find_duplicates()



@unreal.uclass()
class AnimcraftCheckAssets(unreal.ToolMenuEntryScript):
    @unreal.ufunction(override=True)
    def execute(self, context):
        selected_assets = unreal.EditorUtilityLibrary.get_selected_assets()
        if not selected_assets:
            unreal.EditorDialog.show_message(title="Animcraft", message="当前选中资产为空。", message_type=unreal.AppMsgType.OK)
            return False
        paths = [asset.get_path_name() for asset in selected_assets]
        for path in paths:
            acc_perf_audit_scan.scan_assets(path)

@unreal.uclass()
class AnimcraftAutoLod(unreal.ToolMenuEntryScript):
    @unreal.ufunction(override=True)
    def execute(self, context):
        selected_assets = unreal.EditorUtilityLibrary.get_selected_assets()
        if not selected_assets:
            unreal.EditorDialog.show_message(title="Animcraft", message="当前选中资产为空。", message_type=unreal.AppMsgType.OK)
            return False
        # for asset in selected_assets:
        #     if not (isinstance(asset, unreal.SkeletalMesh) or isinstance(asset, unreal.StaticMesh)):
        #         unreal.EditorDialog.show_message(title="Animcraft", message="选中资产不含骨骼网格体或静态网格体。", message_type=unreal.AppMsgType.OK)
        #         return False
        asset_paths = [asset.get_path_name() for asset in selected_assets]
        acc_perf_audit_scan.auto_generate_lods(asset_paths)


@unreal.uclass()
class AnimcraftUnusedAction(unreal.ToolMenuEntryScript):
    @unreal.ufunction(override=True)
    def execute(self, context):
        acc_unreal_tools.AnimcraftCore.find_unused()


@unreal.uclass()
class AnimcraftSortTexAction(unreal.ToolMenuEntryScript):
    @unreal.ufunction(override=True)
    def execute(self, context):
        acc_unreal_tools.AnimcraftCore.sort_textures()


@unreal.uclass()
class AnimcraftLevelRefAction(unreal.ToolMenuEntryScript):
    @unreal.ufunction(override=True)
    def execute(self, context):
        acc_unreal_tools.AnimcraftCore.log_level_deps()


@unreal.uclass()
class AnimcraftSwapAction(unreal.ToolMenuEntryScript):
    @unreal.ufunction(override=True)
    def execute(self, context):
        acc_unreal_tools.AnimcraftCore.swap_actors()


@unreal.uclass()
class AnimcraftGridAction(unreal.ToolMenuEntryScript):
    @unreal.ufunction(override=True)
    def execute(self, context):
        acc_unreal_tools.AnimcraftCore.grid_layout()

@unreal.uclass()
class AnimcraftInstancedReplace(unreal.ToolMenuEntryScript):
    @unreal.ufunction(override=True)
    def execute(self, context):
        api.foliage_replace()

@unreal.uclass()
class AnimcraftSelectAllMatchingActors(unreal.ToolMenuEntryScript):
    @unreal.ufunction(override=True)
    def execute(self, context):
        acc_unreal_tools.AnimcraftCore.select_actors_with_same_assets()

@unreal.uclass()
class AnimcraftSelectLevelActorsbyAsset(unreal.ToolMenuEntryScript):
    @unreal.ufunction(override=True)
    def execute(self, context):
        api.GetReferencedLevelObjects()

@unreal.uclass()
class AnimcraftLanguageToggleAction(unreal.ToolMenuEntryScript):
    @unreal.ufunction(override=True)
    def execute(self, context):
        # 1. 获取当前状态并反向切换
        is_currently_cn = AnimcraftLang.sync()
        new_is_cn = not is_currently_cn

        # 2. 执行引擎文化切换命令
        new_culture = "zh-Hans" if new_is_cn else "en"
        unreal.SystemLibrary.execute_console_command(None, f"culture={new_culture}")

        # 3. 强制刷新菜单注册
        unreal.log(f"[Animcraft] Switching UI Language to: {new_culture}")
        register_animcraft_tools()


@unreal.uclass()
class AnimcraftAboutAction(unreal.ToolMenuEntryScript):
    @unreal.ufunction(override=True)
    def execute(self, context):

        title = "关于 Animcraft"
        msg = (
            "关于 Animcraft\n"
            "注意：无需加载 Animcraft 插件。\n\n"
            "版本：v1.0\n\n"
            "【是(Yes)】：访问教程\n"
            "【否(No)】：访问官网\n"
            "【取消】：关闭"
        )

        # 返回值：Yes(教程), No(官网), Cancel(退出)
        result = unreal.EditorDialog.show_message(
            title,
            msg,
            unreal.AppMsgType.YES_NO_CANCEL
        )

        # 3. 根据点击执行跳转
        if result == unreal.AppReturnType.YES:
            webbrowser.open("https://animcraft.com/")
            unreal.log("[Animcraft] 正在打开教程...")
        elif result == unreal.AppReturnType.NO:
            webbrowser.open("https://animcraft.com/")
            unreal.log("[Animcraft] 正在打开官网...")


# --- 菜单 ---
def register_animcraft_tools():
    menus = unreal.ToolMenus.get()
    owner_name = "AnimcraftOwner"

    # 每次清理旧菜单所有权，防止重复注册导致 UI 堆叠
    menus.unregister_owner_by_name(owner_name)

    # 同步当前语言状态
    is_cn = AnimcraftLang.sync()

    lbl_flat = "平铺导入" if is_cn else "Flat Import"
    lbl_copy = "复制磁盘路径" if is_cn else "Copy Disk Path"
    lbl_export = "导出资产" if is_cn else "Export Assets"
    lbl_Check = "检测选中资产" if is_cn else "Check Assets"
    lbl_Lod = "自动生成LOD" if is_cn else "Auto LOD"

    # --- 1. 资产右键 ---
    asset_menu = menus.find_menu("ContentBrowser.AssetContextMenu")
    if asset_menu:
        asset_menu.add_section("Animcraft", "Animcraft")
        entry_export = AnimcraftExportAction()
        entry_export.init_entry(owner_name, asset_menu.menu_name, "Animcraft", "AC_ExportAssets", lbl_export)
        asset_menu.add_menu_entry_object(entry_export)

        #
        entry_check = AnimcraftCheckAssets()
        entry_check.init_entry(owner_name, asset_menu.menu_name, "Animcraft", "AC_CheckAssets", lbl_Check)
        asset_menu.add_menu_entry_object(entry_check)

    # --- 2. 文件夹右键 ---
    folder_menu = menus.find_menu("ContentBrowser.FolderContextMenu")
    if folder_menu:
        folder_menu.add_section("Animcraft", "Animcraft")

        entry_copy_f = AnimcraftCopyPathAction()
        entry_copy_f.init_entry(owner_name, folder_menu.menu_name, "Animcraft", "AC_CopyDiskPath", lbl_copy)
        folder_menu.add_menu_entry_object(entry_copy_f)

        entry_flat_f = AnimcraftFlatImport()
        entry_flat_f.init_entry(owner_name, folder_menu.menu_name, "Animcraft", "AC_FlatImport", lbl_flat)
        folder_menu.add_menu_entry_object(entry_flat_f)

    # other
    target_types = ["StaticMesh", "SkeletalMesh"]
    for asset_type in target_types:
        menu_lod = menus.find_menu(f"ContentBrowser.AssetContextMenu.{asset_type}")
        if menu_lod:
            menu_lod.add_section("Animcraft", "Animcraft")
            entry_export_lod = AnimcraftAutoLod()
            entry_export_lod.init_entry(owner_name, menu_lod.menu_name, "Animcraft", "AC_Lod", lbl_Lod)
            menu_lod.add_menu_entry_object(entry_export_lod)

    # --- 3. 顶部主菜单 ---
    main_menu = menus.find_menu("LevelEditor.MainMenu")
    if main_menu:
        top_sub_name = "AnimcraftTopTools"
        # 根菜单名
        anim_top_sub = main_menu.add_sub_menu(owner_name, "Help", top_sub_name, "Animcraft")
        sub_menu_obj = menus.find_menu(anim_top_sub.menu_name)

        tools_map = [
            ("Audit", [
                (AnimcraftCheckRefAction(), "T_CheckReferenceList", "检查引用列表" if is_cn else "Check Reference List"),
                # (AnimcraftDupAction(), "T_ScanDuplicateAssets", "扫描重复资产" if is_cn else "Scan Duplicate Assets"),
                (AnimcraftUnusedAction(), "T_DetectUnusedAssets", "检测未被引用资产" if is_cn else "Detect Unused Assets"),
                (AnimcraftSortTexAction(), "T_ValidateTextureInfo", "验证贴图信息" if is_cn else "Validate Texture Info"),
                # (AnimcraftLevelRefAction(), "T_ExportLevelReference Table", "导出关卡引用表格" if is_cn else "Export Level Reference"),
            ]),
            ("Asset", [
                (AnimcraftCheckAssets(), "T_CheckAssets", lbl_Check),
                (AnimcraftAutoLod(), "T_AutoLod", lbl_Lod),
            ]),
            ("Parameters", [
                ("__submenu__", "T_Debug", "Common Parameters" if not is_cn else "常用参数", [
                    (make_cmd_action("stat fps"), "T_fps", "stat fps"),
                    (make_cmd_action("stat unit"), "T_unit", "stat unit"),
                    (make_cmd_action("stat unitgraph"), "T_unitgraph", "stat unitgraph"),
                    (make_cmd_action("stat hitches"), "T_hitches", "stat hitches"),
                    (make_cmd_action("stat memory"), "T_memory", "stat memory"),
                    (make_cmd_action("stat rhi"), "T_rhi", "stat rhi"),
                    (make_cmd_action("stat scenerendering"), "T_scenerendering", "stat scenerendering"),
                    (make_cmd_action("stat gpu"), "T_gpu", "stat gpu"),
                    (make_cmd_action("stat niagara"), "T_niagara", "stat niagara"),
                    (make_cmd_action("stat game"), "T_game", "stat game"),
                    (make_cmd_action("stat AI"), "T_AI", "stat AI"),
                    (make_cmd_action("showdebug abilitysystem"), "T_abilitysystem", "showdebug abilitysystem"),
                ]),
            ]),
            ("Scene", [
                (AnimcraftSwapAction(), "T_LevelContentSwitcher", "关卡与内容互换" if is_cn else "Level-Content Switcher"),
                (AnimcraftGridAction(), "T_ArrayLayout", "阵列排放" if is_cn else "Array Layout"),
                (AnimcraftInstancedReplace(), "T_InstancedActorReplace", "实例点位替换" if is_cn else "Instanced Actor Replace"),
                (AnimcraftSelectAllMatchingActors(), "T_SelectAllMatchingActors", "全选同类物体" if is_cn else "Select All Matching Actors"),
                (AnimcraftSelectLevelActorsbyAsset(), "T_SelectLevelActorsbyAsset", "按资产选中场景物体" if is_cn else "Select Level Actors by Asset"),
            ]),
            ("Lib", [
                (AnimcraftFlatImport(), "T_FlatImport", lbl_flat),
                (AnimcraftReferenceImport(), "T_ReferenceImport", "引用粘贴板导入" if is_cn else "Reference Import from Clipboard"),
                (AnimcraftCopyPathAction(), "T_CopyDiskPath", lbl_copy),
                (AnimcraftExportAction(), "T_ExportAssets", lbl_export),  # 修正了这里之前笔误写成 lbl_copy
            ]),
            ("Performance", [
                (AnimcraftBeginDetection(), "T_BeginDetection","检测开始" if is_cn else "Begin Detection"),
                (AnimcraftStopDetection(), "T_EndDetectio","检测停止" if is_cn else "End Detection"),
            ]),
            ("System", [
                (AnimcraftLanguageToggleAction(), "T_LanguageSwitch", "中英文切换" if is_cn else "Language Switch"),
            ]),
            ("About", [
                (AnimcraftAboutAction(), "T_About", "关于" if is_cn else "About"),
            ])
        ]

        for section_name, actions in tools_map:
            sub_menu_obj.add_section(section_name, section_name)
            for item in actions:
                # （4元组）
                if isinstance(item, tuple) and len(item) == 4 and item[0] == "__submenu__":
                    _, submenu_id, submenu_label, children = item
                    child_sub = sub_menu_obj.add_sub_menu(
                        owner_name, section_name, submenu_id, submenu_label
                    )
                    child_menu_obj = menus.find_menu(child_sub.menu_name)
                    if child_menu_obj:
                        for child_action, child_id, child_label in children:
                            child_action.init_entry(
                                owner_name, child_menu_obj.menu_name,
                                "", child_id, child_label
                            )
                            child_menu_obj.add_menu_entry_object(child_action)
                else:
                    # （3元组）
                    action_obj, name_id, label = item
                    action_obj.init_entry(
                        owner_name, sub_menu_obj.menu_name,
                        section_name, name_id, label
                    )
                    sub_menu_obj.add_menu_entry_object(action_obj)

    # 刷新界面 UI
    menus.refresh_all_widgets()


# if __name__ == "__main__":
#     register_animcraft_tools()