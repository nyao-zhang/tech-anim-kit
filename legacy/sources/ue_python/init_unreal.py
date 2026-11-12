# -*- coding: utf-8 -*-
import unreal
version_str = unreal.SystemLibrary.get_engine_version()
import acc_unreal_action
import importlib
importlib.reload(acc_unreal_action)
acc_communication_isok = acc_unreal_action.acc_get_animcraft_houpath_start_communication()
print("animcraft init python" if acc_communication_isok == 0 else "animcraft init error")
