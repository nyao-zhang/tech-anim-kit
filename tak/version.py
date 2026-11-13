# -*- coding: utf-8 -*-
"""单一版本事实源。

``__version__`` 是全仓库唯一版本出口：README、安装器标记块、Web ``/api/health``
都从这里取，任何地方都不要再硬编码版本号（``tests/test_release_meta.py`` 会拦）。
"""
__version__ = "0.5.0"
SCHEMA_VERSION = "1.0.0"
