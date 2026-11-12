import unreal
import random

#  日志
def log(message: str) -> None:
    unreal.log(message)


def log_warning(message: str) -> None:
    unreal.log_warning(message)


def log_error(message: str) -> None:
    unreal.log_error(message)


def log_info(message: str) -> None:
    if hasattr(unreal, "log_info"):
        unreal.log_info(message)
    else:
        unreal.log(message)


#  引擎信息 & 路径
def get_engine_version() -> str:
    return unreal.SystemLibrary.get_engine_version()


def get_project_dir() -> str:
    return unreal.Paths.project_dir()


def get_project_content_dir() -> str:
    return unreal.Paths.project_content_dir()


def get_project_saved_dir() -> str:
    return unreal.Paths.project_saved_dir()


def convert_relative_path_to_full(path: str) -> str:
    return unreal.Paths.convert_relative_path_to_full(path)


def collect_garbage() -> None:
    unreal.SystemLibrary.collect_garbage()


#  对话框
def show_message_ok(title: str, message: str) -> None:
    unreal.EditorDialog.show_message(
        title=title,
        message=message,
        message_type=unreal.AppMsgType.OK,
    )


def show_message_yes_no(title: str, message: str) -> bool:
    """返回 True 表示用户点了 YES。"""
    result = unreal.EditorDialog.show_message(
        title=title,
        message=message,
        message_type=unreal.AppMsgType.YES_NO,
    )
    return result == unreal.AppReturnType.YES


def get_editor_window_handle():
    if hasattr(unreal, "EditorLib") and hasattr(unreal.EditorLib, "get_editor_window_handle"):
        return unreal.EditorLib.get_editor_window_handle()
    return None


def try_desktop_platform_save_dialog(window_handle, initial_dir: str) -> str:
    if not hasattr(unreal, "DesktopPlatform"):
        raise RuntimeError("DesktopPlatform 不可用")
    try:
        result = unreal.DesktopPlatform.save_file_dialog(
            parent_window_handle=window_handle if window_handle else 0,
            dialog_title="Animcraft Export",
            default_path=initial_dir,
            default_file="Default",
            file_types="All Files (*.*)|*.*",
        )
        if result and len(result) > 0:
            return result if isinstance(result, str) else result[0]
        return ""
    except Exception as e:
        log_warning(f"[Animcraft] DesktopPlatform.save_file_dialog 失败: {e}")
        return ""


def try_editor_dialog_library_save_dialog(initial_dir: str) -> str:
    if not hasattr(unreal, "EditorDialogLibrary"):
        raise RuntimeError("EditorDialogLibrary 不可用")
    try:
        result = unreal.EditorDialogLibrary.save_file_dialog(
            title="Animcraft Export",
            default_path=initial_dir,
            default_file="Default",
            file_types="*.*",
        )
        return result if result else ""
    except Exception as e:
        log_warning(f"[Animcraft] EditorDialogLibrary 失败: {e}")
        return ""

#  资产注册表 & 依赖
def get_asset_registry():
    return unreal.AssetRegistryHelpers.get_asset_registry()


def make_dependency_options(
    include_hard: bool = True,
    include_soft: bool = True,
    include_searchable: bool = False,
    include_soft_management: bool = False,
    include_hard_management: bool = False,
) -> object:
    opts = unreal.AssetRegistryDependencyOptions()
    opts.include_hard_package_references = include_hard
    opts.include_soft_package_references = include_soft
    opts.include_searchable_names = include_searchable
    if hasattr(opts, "include_soft_management_references"):
        opts.include_soft_management_references = include_soft_management
    if hasattr(opts, "include_hard_management_references"):
        opts.include_hard_management_references = include_hard_management
    return opts


def make_name(text: str):
    return unreal.Name(text)


def get_assets_by_package_name(asset_registry, package_name: str) -> list:
    return asset_registry.get_assets_by_package_name(unreal.Name(package_name))


def get_asset_by_object_path(asset_registry, asset_path: str):
    """从注册表按对象路径获取 AssetData，无效则返回 None。"""
    asset_data = asset_registry.get_asset_by_object_path(asset_path)
    if asset_data and asset_data.is_valid():
        return asset_data
    return None


def get_asset_class_from_data(asset_data) -> str:
    """从 AssetData 获取类名，兼容 UE 5.0 ~ 5.6。"""
    try:
        return str(asset_data.asset_class_path.asset_name)
    except Exception:
        return str(asset_data.asset_class)


def get_asset_name_from_data(asset_data) -> str:
    return str(asset_data.asset_name)


def get_package_name_from_data(asset_data) -> str:
    return str(asset_data.package_name)


def get_package_path_from_data(asset_data) -> str:
    return str(asset_data.package_path)


def get_asset_object_from_data(asset_data):
    """asset_data.get_asset() — 加载并返回资产对象。"""
    return asset_data.get_asset()


def get_tag_value(asset_data, tag_name: str):
    """从 AssetData 读取标签值，返回去空格字符串或 None。"""
    try:
        value = asset_data.get_tag_value(tag_name)
        if value is None:
            return None
        value = str(value).strip()
        return value if value != "" else None
    except Exception:
        return None


def get_dependencies(asset_registry, package_name, dep_options) -> list:
    if isinstance(package_name, str):
        package_name = unreal.Name(package_name)
    return asset_registry.get_dependencies(package_name, dep_options)


def get_referencers(asset_registry, package_name, dep_options) -> list:
    if isinstance(package_name, str):
        package_name = unreal.Name(package_name)
    return asset_registry.get_referencers(package_name, dep_options)


def make_ar_filter(package_paths: list = None, recursive: bool = True,
                   class_names: list = None):
    kwargs = {"recursive_paths": recursive}
    if package_paths:
        kwargs["package_paths"] = package_paths
    if class_names:
        kwargs["class_names"] = class_names
    return unreal.ARFilter(**kwargs)


def get_assets(asset_registry, ar_filter) -> list:
    return asset_registry.get_assets(ar_filter)


def scan_paths_synchronous(asset_registry, paths: list, force_rescan: bool = True) -> None:
    asset_registry.scan_paths_synchronous(paths, force_rescan=force_rescan)


#  编辑器资产操作（EditorUtilityLibrary）
def get_selected_assets() -> list:
    return unreal.EditorUtilityLibrary.get_selected_assets()


def get_selected_asset_data() -> list:
    return unreal.EditorUtilityLibrary.get_selected_asset_data()


#  从【已加载的资产对象】获取信息
#  （参数是 asset_obj，即 load_asset() 返回的对象）
def get_asset_name(asset_obj) -> str:
    """获取资产的显示名称。"""
    return asset_obj.get_name()


def get_asset_class_name(asset_obj) -> str:
    """获取资产对象的类名（如 'StaticMesh'）。
    ★ 注意：参数是已加载的 asset_obj，不是 AssetData。
      如果你拿到的是 AssetData，请用 get_asset_class_from_data()。
    """
    return asset_obj.get_class().get_name()


# 兼容别名 — 部分模块可能用了更长的名字
get_asset_class_name_from_obj = get_asset_class_name


def get_asset_package_name(asset_obj) -> str:
    """获取资产的包路径名（取 '.' 前的部分）。"""
    return asset_obj.get_path_name().split(".")[0]


def get_asset_path_name(asset_obj) -> str:
    """完整路径名，包含 '.'。"""
    return asset_obj.get_path_name()


def get_asset_package_object_name(asset_obj) -> str:
    """通过 get_package().get_name() 获取包名。"""
    try:
        return str(asset_obj.get_package().get_name())
    except Exception:
        return asset_obj.get_path_name().split(".")[0]


def get_asset_class_object(asset_obj):
    """返回资产的 class 对象。"""
    return asset_obj.get_class()


def get_class_path_name(cls_obj) -> str:
    return cls_obj.get_path_name()

#  类型判断
def is_instance_of_blueprint(asset_obj) -> bool:
    return isinstance(asset_obj, unreal.Blueprint)


def is_instance_of_texture(asset_obj) -> bool:
    return isinstance(asset_obj, unreal.Texture)


def is_instance_of_static_mesh(asset_obj) -> bool:
    return isinstance(asset_obj, unreal.StaticMesh)


def is_instance_of_skeletal_mesh(asset_obj) -> bool:
    return isinstance(asset_obj, unreal.SkeletalMesh)

#  通用属性读写
def get_editor_property(obj, prop_name: str):
    return obj.get_editor_property(prop_name)


def has_editor_property(obj, prop_name: str) -> bool:
    try:
        obj.get_editor_property(prop_name)
        return True
    except Exception:
        return False


def is_unreal_object(val) -> bool:
    return isinstance(val, unreal.Object)


def is_unreal_array(val) -> bool:
    return isinstance(val, (list, tuple, unreal.Array))


#  编辑器资产操作（EditorAssetLibrary）
def does_asset_exist(path: str) -> bool:
    return unreal.EditorAssetLibrary.does_asset_exist(path)


def load_asset(path: str):
    return unreal.EditorAssetLibrary.load_asset(path)


def save_asset(path: str, only_if_dirty: bool = False) -> None:
    unreal.EditorAssetLibrary.save_asset(path, only_if_is_dirty=only_if_dirty)


def rename_asset(source: str, destination: str) -> bool:
    return unreal.EditorAssetLibrary.rename_asset(source, destination)


def delete_asset(path: str) -> bool:
    return unreal.EditorAssetLibrary.delete_asset(path)


def list_assets(directory: str, recursive: bool = True, include_folder: bool = False) -> list:
    return unreal.EditorAssetLibrary.list_assets(
        directory, recursive=recursive, include_folder=include_folder
    ) or []


def sync_browser_to_objects(paths: list) -> None:
    unreal.EditorAssetLibrary.sync_browser_to_objects(paths)


def get_path_name_for_loaded_asset(asset) -> str:
    return unreal.EditorAssetLibrary.get_path_name_for_loaded_asset(asset)


def load_blueprint_class(path: str):
    return unreal.EditorAssetLibrary.load_blueprint_class(path)

#  AssetTools
def get_asset_tools():
    return unreal.AssetToolsHelpers.get_asset_tools()


def create_unique_asset_name(asset_tools, base_path: str, suffix: str) -> tuple:
    return asset_tools.create_unique_asset_name(base_path, suffix)


def rename_assets_batch(asset_tools, rename_data_list: list) -> None:
    asset_tools.rename_assets(rename_data_list)


def make_asset_rename_data(asset, new_package_path: str, new_name: str):
    rd = unreal.AssetRenameData()
    rd.asset = asset
    rd.new_package_path = new_package_path
    rd.new_name = new_name
    return rd


#  Content Browser 文件夹路径
def get_selected_folder_paths_ue54() -> list:
    """UE 5.4+ get_selected_folder_paths"""
    lib = unreal.EditorUtilityLibrary
    if hasattr(lib, "get_selected_folder_paths"):
        try:
            return lib.get_selected_folder_paths() or []
        except Exception:
            pass
    return []


def get_selected_path_view_folder_paths() -> list:
    """UE 5.1 ~ 5.3"""
    lib = unreal.EditorUtilityLibrary
    if hasattr(lib, "get_selected_path_view_folder_paths"):
        try:
            return lib.get_selected_path_view_folder_paths() or []
        except Exception:
            pass
    return []


def get_current_content_browser_path() -> str:
    """UE 5.0 兜底"""
    lib = unreal.EditorUtilityLibrary
    if hasattr(lib, "get_current_content_browser_path"):
        try:
            result = lib.get_current_content_browser_path()
            return str(result) if result else ""
        except Exception:
            pass
    return ""

#  静态网格体辅助
def get_static_mesh_num_verts(mesh, lod_index: int = 0) -> int:
    return unreal.EditorStaticMeshLibrary.get_number_verts(mesh, lod_index)


def get_static_mesh_num_triangles_via_lib(mesh, lod_index: int = 0):
    """通过 EditorStaticMeshLibrary 获取三角面数。"""
    try:
        return int(unreal.EditorStaticMeshLibrary.get_number_triangles(mesh, lod_index))
    except Exception:
        return None


def get_mesh_num_triangles(mesh, lod_index: int = 0):
    """通过 mesh.get_num_triangles() 获取三角面数。"""
    try:
        value = mesh.get_num_triangles(lod_index)
        return int(value) if value is not None else None
    except Exception:
        return None


def get_mesh_num_lods(mesh):
    """尝试多种方式获取 LOD 数量。"""
    for fn_name in ("get_num_lods", "get_lod_num"):
        try:
            fn = getattr(mesh, fn_name)
            value = fn()
            if value is not None:
                return int(value)
        except Exception:
            pass
    return None


def get_mesh_lod_info_count(mesh) -> int:
    """通过 lod_info 属性获取 LOD 数量。"""
    try:
        lod_info = mesh.get_editor_property("lod_info")
        return len(lod_info)
    except Exception:
        return 1


def get_mesh_materials_count(mesh) -> int:
    """通过 materials 属性获取材质槽数量。"""
    try:
        return len(mesh.get_editor_property("materials"))
    except Exception:
        return 0


#  骨骼网格体辅助
def get_skeletal_mesh_bone_count(mesh):
    """尝试多种方式获取骨骼数。"""
    try:
        ref_skel = mesh.get_ref_skeleton()
        for fn_name in ("get_num", "get_raw_bone_num"):
            try:
                fn = getattr(ref_skel, fn_name)
                value = fn()
                if value is not None:
                    return int(value)
            except Exception:
                pass
    except Exception:
        pass
    return None


def regenerate_skeletal_mesh_lod(mesh, new_lod_count: int = 3) -> bool:
    try:
        return mesh.regenerate_lod(new_lod_count=new_lod_count)
    except Exception:
        return False


#  StaticMesh 碰撞辅助
def get_static_mesh_collision_trace_flag(mesh) -> str:
    """获取 StaticMesh 的碰撞追踪标志字符串，失败返回 None。"""
    try:
        body_setup = mesh.get_editor_property("body_setup")
        if not body_setup:
            return None
        flag = body_setup.get_editor_property("collision_trace_flag")
        return str(flag) if flag is not None else None
    except Exception:
        return None


#  StaticMesh LOD 生成（子系统方式）
def get_static_mesh_editor_subsystem():
    return unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)


def static_mesh_set_lods(subsystem, mesh, options) -> None:
    subsystem.set_lods(mesh, options)


def make_mesh_reduction_options(auto_compute: bool = True):
    opts = unreal.EditorScriptingMeshReductionOptions()
    opts.auto_compute_lod_screen_size = auto_compute
    return opts


def make_mesh_reduction_settings(percent_triangles: float):
    s = unreal.EditorScriptingMeshReductionSettings()
    s.percent_triangles = percent_triangles
    return s


def set_reduction_settings_on_options(options, settings_list: list) -> None:
    options.reduction_settings = settings_list


#  贴图辅助
def get_texture_size(texture) -> tuple:
    """返回 (size_x, size_y)。"""
    return texture.blueprint_get_size_x(), texture.blueprint_get_size_y()


#  Actor 子系统 & 场景操作
def get_editor_actor_subsystem():
    return unreal.get_editor_subsystem(unreal.EditorActorSubsystem)


def get_selected_level_actors(actor_subsystem) -> list:
    return actor_subsystem.get_selected_level_actors()


def get_all_level_actors(actor_subsystem) -> list:
    return actor_subsystem.get_all_level_actors()


def set_selected_level_actors(actor_subsystem, actors: list) -> None:
    actor_subsystem.set_selected_level_actors(actors)


def spawn_actor_from_object_subsystem(actor_subsystem, asset, location, rotation,
                                      transient: bool = False):
    return actor_subsystem.spawn_actor_from_object(
        asset, location, rotation, transient=transient
    )


def spawn_actor_from_object_level_lib(asset, location):
    return unreal.EditorLevelLibrary.spawn_actor_from_object(asset, location)


def destroy_actor(actor_subsystem, actor) -> None:
    actor_subsystem.destroy_actor(actor)


def get_actor_location(actor):
    return actor.get_actor_location()


def get_actor_rotation(actor):
    return actor.get_actor_rotation()


def get_actor_relative_scale3d(actor):
    return actor.get_actor_relative_scale3d()


def set_actor_relative_scale3d(actor, scale) -> None:
    actor.set_actor_relative_scale3d(scale)


def set_actor_scale3d(actor, scale) -> None:
    actor.set_actor_scale3d(scale)


def get_actor_folder_path(actor):
    return actor.get_folder_path()


def set_actor_folder_path(actor, folder) -> None:
    actor.set_folder_path(folder)


def get_actor_label(actor) -> str:
    return actor.get_actor_label()


def set_actor_label(actor, label: str) -> None:
    actor.set_actor_label(label)


def get_components_by_class(actor, cls_name: str) -> list:
    """根据类名字符串获取组件列表。"""
    cls_map = {
        "ActorComponent": unreal.ActorComponent,
        "StaticMeshComponent": unreal.StaticMeshComponent,
        "SkeletalMeshComponent": unreal.SkeletalMeshComponent,
        "HierarchicalInstancedStaticMeshComponent": unreal.HierarchicalInstancedStaticMeshComponent,
    }
    cls = cls_map.get(cls_name)
    if cls is None:
        try:
            cls = getattr(unreal, cls_name)
        except AttributeError:
            return []
    return actor.get_components_by_class(cls)


def is_instanced_foliage_actor(actor) -> bool:
    return isinstance(actor, unreal.InstancedFoliageActor)


def get_instance_count(component) -> int:
    return component.get_instance_count()


def get_instance_transform(component, index: int, world_space: bool = True):
    return component.get_instance_transform(index, world_space=world_space)


#  数学 / 向量
def make_vector(x: float, y: float, z: float):
    return unreal.Vector(x, y, z)


def get_rotator_from_transform(transform):
    return transform.rotation.rotator()

#  事务 & 进度
class ScopedEditorTransaction:
    """包装 unreal.ScopedEditorTransaction 的上下文管理器。"""

    def __init__(self, description: str):
        self._desc = description
        self._txn = None

    def __enter__(self):
        self._txn = unreal.ScopedEditorTransaction(self._desc)
        return self._txn.__enter__()

    def __exit__(self, *args):
        return self._txn.__exit__(*args)


class ScopedSlowTask:
    """包装 unreal.ScopedSlowTask 的上下文管理器。"""

    def __init__(self, total: int, message: str):
        self._task = unreal.ScopedSlowTask(total, message)

    def __enter__(self):
        self._task.__enter__()
        return self

    def __exit__(self, *args):
        return self._task.__exit__(*args)

    def make_dialog(self, can_cancel: bool = True) -> None:
        self._task.make_dialog(can_cancel)

    def should_cancel(self) -> bool:
        return self._task.should_cancel()

    def enter_progress_frame(self, work: int, message: str = "") -> None:
        self._task.enter_progress_frame(work, message)


#  平台工具
def open_directory_in_explorer(path: str) -> None:
    import os as _os
    import sys as _sys
    if _sys.platform == "win32":
        _os.startfile(path)
    else:
        import subprocess
        subprocess.Popen(["open" if _sys.platform == "darwin" else "xdg-open", path])



def GetReferencedLevelObjects():
    # 获取选中资产
    selected_assets = unreal.EditorUtilityLibrary.get_selected_assets()

    if selected_assets:
        print(f"查找使用资产: {[asset.get_name() for asset in selected_assets]}")

        # 获取所有Actor
        world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
        all_actors = unreal.GameplayStatics.get_all_actors_of_class(world, unreal.Actor)

        matching_actors = []

        # 检查每个Actor
        for actor in all_actors:
            if not actor:
                continue

            # 获取Actor和所有组件
            components = actor.get_components_by_class(unreal.ActorComponent)
            all_objects = [actor] + list(components)

            # 检查每个对象的属性
            for obj in all_objects:
                # 使用get_editor_property检查常见属性
                props_to_check = ['static_mesh', 'skeletal_mesh', 'material', 'niagara_system',
                                  'system', 'template', 'asset', 'sound', 'texture', 'particle_system']

                for prop in props_to_check:
                    try:
                        value = obj.get_editor_property(prop)
                        if value and value in selected_assets:
                            matching_actors.append(actor)
                            print(f"找到: {actor.get_name()}")
                            break
                    except:
                        pass
                if actor in matching_actors:
                    break

        # 使用新API选中Actor（5.0-5.6兼容）
        if matching_actors:
            try:
                # 尝试使用新的API (5.1+)
                editor_actor_subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
                editor_actor_subsystem.set_selected_level_actors(matching_actors)
            except:
                # 回退到旧API (5.0)
                unreal.EditorLevelLibrary.set_selected_level_actors(matching_actors)

            print(f"选中了 {len(matching_actors)} 个Actor")
        else:
            print("未找到使用该资产的Actor")
    else:
        print("请先选中资产")


def foliage_replace():
    """
    替换InstancedFoliageActor为选中的资产
    """

    # 获取选中对象
    actor_subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    selected_actors = actor_subsystem.get_selected_level_actors()
    selected_assets = unreal.EditorUtilityLibrary.get_selected_assets()

    # 筛选FoliageActor
    foliage_actors = [a for a in selected_actors if 'foliage' in a.get_class().get_name().lower()]

    if not foliage_actors or not selected_assets:
        print("请选中FoliageActor和资产")
        return 0

    # 收集所有实例Transform
    transforms = []
    for fa in foliage_actors:
        components = [c for c in fa.get_components_by_class(unreal.ActorComponent)
                      if 'instance' in c.get_class().get_name().lower()]

        for comp in components:
            for i in range(comp.get_instance_count()):
                try:
                    # 尝试获取Transform
                    result = comp.get_instance_transform(i, True)
                    transform = result[1] if isinstance(result, tuple) else result
                    if transform and hasattr(transform, 'translation'):
                        transforms.append(transform)
                except:
                    pass

    if not transforms:
        print("未找到可用实例")
        return 0

    print(f"找到 {len(transforms)} 个实例点位")

    # 生成Actor
    success = 0
    for transform in transforms:
        try:
            asset = random.choice(selected_assets)
            location = transform.translation
            rotation = transform.rotation.rotator()

            actor = actor_subsystem.spawn_actor_from_object(asset, location, rotation)
            if actor:
                actor.set_actor_scale3d(transform.scale3d)

                # Niagara特殊处理
                if 'niagara' in asset.get_class().get_name().lower():
                    for comp in actor.get_components_by_class(unreal.NiagaraComponent):
                        comp.set_relative_location(unreal.Vector(0, 0, 0))
                        comp.deactivate_immediate()
                        comp.activate(True)

                success += 1
        except:
            pass

    print(f"成功生成 {success} 个Actor")
    return success



