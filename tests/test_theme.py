# -*- coding: utf-8 -*-
"""设计 token 单一事实源：Qt 与 Web 输出必须同源、可复现。"""
from tak.ui import load_tokens
from tak.ui.generate_theme import build, main


def test_both_themes_present():
    tokens = load_tokens()
    assert set(tokens["themes"]) == {"dark", "light"}
    assert tokens["radius"]["lg"] > tokens["radius"]["sm"]


def test_generated_files_contain_token_values():
    generated = build(load_tokens())
    dark = load_tokens()["themes"]["dark"]
    light = load_tokens()["themes"]["light"]
    assert dark["bg"] in generated["theme.qss"]
    assert light["bg"] in generated["theme_light.qss"]
    css = generated["theme.css"]
    assert dark["accent"] in css
    assert light["accent"] in css
    assert '[data-theme="light"]' in css
    assert "--tak-radius-lg" in css
    assert build(load_tokens()) == generated


def test_check_mode_passes_after_generation():
    assert main(["--check"]) == 0
