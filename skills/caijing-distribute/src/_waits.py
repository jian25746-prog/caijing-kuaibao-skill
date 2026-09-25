#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""页面操作之间的固定等待与普通输入，给后台页面留出加载/渲染时间。"""
import time


def hpause(a=0.6, b=1.6):
    time.sleep((a + b) / 2)


def htype(page, text):
    page.keyboard.type(text, delay=10)
