# -*- coding: utf-8 -*-
import unreal
import shutil
import json
import os

def original_export_assets(dir_path):
    base_name = os.path.splitext(os.path.basename(dir_path))[0]
    ac_content_path = os.path.join(dir_path, base_name + "_ACCSF")
    ac_json_path = os.path.join(dir_path, base_name + "_Ver.json")
    content_dir = os.path.abspath(unreal.Paths.project_content_dir())
    copy_arrs = copy_file(ac_content_path, content_dir)
    # try:
    #     shutil.copytree(ac_content_path, content_dir, dirs_exist_ok=True)
    #     print(f"成功复制: {ac_content_path} -> {content_dir}")
    # except Exception as e:
    #     print(f"复制失败: {e}")
    if copy_arrs:
        for copy_arr in copy_arrs:
            uasset = copy_arr.replace(content_dir, "/Game").replace(".uasset", "").replace("\\", "/")
            print("uasset:" + uasset)
            unreal.AssetRegistryHelpers.get_asset_registry().scan_paths_synchronous([uasset])
            unreal.EditorLoadingAndSavingUtils.reload_packages([unreal.load_package(uasset)])
    with open(ac_json_path, "r", encoding="utf-16") as f:
        data = json.load(f)
        print("json_asset:" + data["Path"])
        unreal.EditorLoadingAndSavingUtils.reload_packages([unreal.load_package(data["Path"])])
        unreal.AssetRegistryHelpers.get_asset_registry().scan_paths_synchronous([data["Path"]])
        unreal.EditorAssetLibrary.sync_browser_to_objects([data["Path"]])
    # unreal.EditorAssetLibrary.save_directory("/Game", only_if_is_dirty=False, recursive=True)

def copy_file(src, dst):
    base, name = os.path.split(dst)
    names = []
    if not name:
        dst = base
    for home, dirs, files in os.walk(src):
        dst_path = home.replace(src, dst)
        for dir_name in dirs:
            dst_dir = os.path.join(dst_path, dir_name)
            if not os.path.exists(dst_dir):
                os.makedirs(dst_dir)
        for filename in files:
            dst_file = os.path.join(dst_path, filename)
            if not os.path.exists(dst_file):
                shutil.copy(os.path.join(home, filename), dst_file)
                # print(os.path.join(home, filename), dst_file)
                names.append(dst_file)
    return names

def acc_get_game_name():
    return "Original:" + unreal.SystemLibrary.get_game_name()

def acc_get_project_path():
    return unreal.SystemLibrary.get_project_directory()

def acc_get_default_object():
    return unreal.GlobalEditorUtilityBase.get_default_object()

def acc_get_selected_assets():
    utility_base = unreal.GlobalEditorUtilityBase.get_default_object()
    return utility_base.get_selected_assets()

def ac_ue4_export_fbx_to_temp_folder(export_fbx_path, u_obj):
    export_task = unreal.AssetExportTask()
    export_task.filename = export_fbx_path
    export_task.object = u_obj
    export_task.automated = True
    export_task.replace_identical = True
    export_task.prompt = False
    export_task.options = unreal.FbxExportOption()
    #export_task.options.ascii = True
    export_task.options.fbx_export_compatibility = unreal.FbxExportCompatibility.FBX_2018
    unreal.Exporter.run_asset_export_task(export_task)
    unreal.log("Successfully export skeletal mesh as FBX.")


def ac_ue4_send_fbx_anim_to_editor(acc_anim_path, acc_anim_name):
    sel_objs = unreal.GlobalEditorUtilityBase.get_default_object().get_selected_assets()
    if sel_objs is None or sel_objs == []:
        unreal.EditorDialog.show_message('Animcraft',"Please first select the skeletal mesh you need to import animation",unreal.AppMsgType.OK)
        return
    u_obj = sel_objs[0]
    u_obj_name = u_obj.get_full_name()
    u_asset_type = u_obj_name.split(" ")[0]
    if u_asset_type != "SkeletalMesh":
        unreal.EditorDialog.show_message('Animcraft',"Please first select the skeletal mesh you need to import animation",unreal.AppMsgType.OK)
        return
    obj_name = u_obj_name.split("SkeletalMesh ")[-1]
    last_slash = obj_name.rfind("/")
    obj_folder = obj_name[0:last_slash + 1]

    # 获取版本
    version_str = unreal.SystemLibrary.get_engine_version()
    parts = version_str.split(".")
    if int(parts[0]) > 5 or (int(parts[0]) == 5 and int(parts[1]) >= 4):
        unreal.log("UE version is >= 5.4")
        asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
        files_to_import = [acc_anim_path]
        # 构建任务列表
        tasks = []
        for file in files_to_import:
            task = unreal.AssetImportTask()
            task.filename = file
            task.destination_path = obj_folder
            task.automated = False  # 自动导入，无需弹窗
            task.save = True
            tasks.append(task)

        asset_tools.import_asset_tasks(tasks)
    else:
        unreal.log("UE version is < 5.4")
        task = unreal.AssetImportTask()
        task.filename = acc_anim_path
        task.destination_path = obj_folder
        task.destination_name = acc_anim_name
        task.replace_existing = False
        task.automated = True
        task.options = unreal.FbxImportUI()
        task.options.import_animations = True
        task.options.import_mesh = False
        task.options.skeleton = u_obj.skeleton
        task.options.import_materials = False
        task.options.import_textures = False
        task.options.import_as_skeletal = False
        tasks = [task]
        unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks(tasks)
        unreal.log("Successfully import anim as FBX.")



