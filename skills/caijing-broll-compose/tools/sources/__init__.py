# -*- coding: utf-8 -*-
"""下载源自动发现：本目录下每个不以下划线开头的 .py 文件里，所有设置了 name 的 Source 子类都会被加载。"""
import importlib
import os
import pkgutil

from .base import Candidate, Source

__all__ = ["Candidate", "Source", "discover"]


def discover():
    """返回 ({name: 实例}, {模块名: 加载错误})。单个下载源出错不影响其他源。"""
    found, errors = {}, {}
    for m in pkgutil.iter_modules([os.path.dirname(__file__)]):
        if m.name.startswith("_") or m.name == "base":
            continue
        try:
            mod = importlib.import_module(f"{__name__}.{m.name}")
        except Exception as e:
            errors[m.name] = f"{type(e).__name__}: {e}"
            continue
        for obj in vars(mod).values():
            if isinstance(obj, type) and issubclass(obj, Source) and obj is not Source and obj.name:
                found[obj.name] = obj()
    return found, errors
