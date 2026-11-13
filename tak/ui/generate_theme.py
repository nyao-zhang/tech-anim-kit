# -*- coding: utf-8 -*-
"""由 tokens.json 生成 Qt QSS 与 Web CSS，保证四个 DCC 视觉同源。

唯一事实源是 tokens.json：改颜色 / 间距后重跑本脚本即可，
Qt 端与 Web 端同步更新，不需要分别手改样式表。

用法：
    python tak/ui/generate_theme.py            # 生成
    python tak/ui/generate_theme.py --check    # CI：校验已生成文件是否过期
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_TOKENS = os.path.join(_HERE, "tokens.json")

_QSS_TEMPLATE = """/* TAK {theme} theme - generated from tokens.json v{version}. DO NOT EDIT. */
QWidget, QMainWindow {{
    background-color: {bg};
    color: {text};
    font-family: {family};
    font-size: {body}px;
}}
QWidget[takCard="true"], QGroupBox, QFrame[takCard="true"] {{
    background-color: {surface};
    border: 1px solid {border};
    border-radius: {r_lg}px;
    padding: {s_md}px;
}}
QLabel {{ color: {text}; background: transparent; }}
QLabel[takMuted="true"] {{ color: {text_muted}; }}
QLabel[takHint="true"] {{ color: {text_hint}; font-size: {caption}px; }}
QPushButton {{
    background-color: {elevated};
    color: {text};
    border: 1px solid {border_strong};
    border-radius: {r_md}px;
    padding: {s_sm}px {s_lg}px;
}}
QPushButton:hover {{ border-color: {accent}; }}
QPushButton:pressed {{ background-color: {accent_soft}; }}
QPushButton[takPrimary="true"] {{
    background-color: {accent};
    color: {accent_text};
    border: 1px solid {accent};
}}
QLineEdit, QSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
    background-color: {elevated};
    color: {text};
    border: 1px solid {border};
    border-radius: {r_sm}px;
    padding: {s_xs}px {s_sm}px;
    selection-background-color: {accent_soft};
}}
QPlainTextEdit {{ font-family: {mono}; }}
QTableWidget, QTreeWidget, QListView, QListWidget {{
    background-color: {surface};
    alternate-background-color: {table_alt};
    color: {text};
    border: 1px solid {border};
    border-radius: {r_md}px;
    gridline-color: {border};
}}
QListWidget {{ padding: {s_xs}px; outline: none; }}
QListWidget::item {{
    padding: {s_sm}px {s_md}px;
    border-radius: {r_sm}px;
    color: {text_muted};
    border: 1px solid transparent;
}}
QListWidget::item:hover {{ background-color: {elevated}; color: {text}; }}
QListWidget::item:selected {{
    background-color: {accent_soft};
    color: {text};
    border: 1px solid {accent};
}}
QListWidget#takCategoryList::item {{ padding: {s_xs}px {s_md}px; }}
QHeaderView::section {{
    background-color: {elevated};
    color: {text_muted};
    border: none;
    border-bottom: 1px solid {border};
    padding: {s_sm}px;
}}
QLabel[takSeverity="error"], QLabel[takSeverity="blocker"] {{ color: {error}; }}
QLabel[takSeverity="warning"] {{ color: {warning}; }}
QLabel[takSeverity="info"] {{ color: {info}; }}
QLabel[takSeverity="success"] {{ color: {success}; }}
QLabel[takDisplay="true"] {{ font-size: {size_display}px; font-weight: 600; }}
QLabel[takSection="true"] {{
    color: {text_muted};
    font-size: {caption}px;
    font-weight: 600;
    padding-top: {s_sm}px;
}}
QLabel[takBadge="true"] {{
    background-color: {severity_soft};
    color: {text_muted};
    border: 1px solid {border};
    border-radius: {r_sm}px;
    padding: 2px {s_sm}px;
    font-size: {caption}px;
}}
QPushButton[takGhost="true"] {{
    background-color: transparent;
    border: 1px solid {border};
    color: {text_muted};
}}
QPushButton[takGhost="true"]:hover {{ color: {text}; border-color: {accent}; }}
QPushButton:disabled {{ color: {text_hint}; border-color: {border}; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QSplitter::handle {{ background: {border}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QTabWidget::pane {{ border: none; }}
QToolTip {{
    background-color: {elevated};
    color: {text};
    border: 1px solid {border_strong};
    padding: {s_xs}px;
}}
QCheckBox, QRadioButton {{ color: {text}; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 14px; height: 14px; }}
QProgressBar {{
    background-color: {elevated};
    border: 1px solid {border};
    border-radius: {r_sm}px;
    text-align: center;
}}
QProgressBar::chunk {{ background-color: {accent}; border-radius: {r_sm}px; }}
QScrollBar:vertical {{ background: {bg}; width: 10px; }}
QScrollBar::handle:vertical {{ background: {border_strong}; border-radius: {r_sm}px; }}
"""

_CSS_TEMPLATE = """/* TAK Web theme - generated from tokens.json v%(version)s. DO NOT EDIT. */
%(dark_vars)s
%(light_vars)s
:root {
  --tak-radius-sm: %(r_sm)dpx; --tak-radius-md: %(r_md)dpx; --tak-radius-lg: %(r_lg)dpx;
  --tak-space-xs: %(s_xs)dpx; --tak-space-sm: %(s_sm)dpx; --tak-space-md: %(s_md)dpx;
  --tak-space-lg: %(s_lg)dpx; --tak-space-xl: %(s_xl)dpx;
  --tak-font: %(family)s; --tak-mono: %(mono)s;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--tak-bg); color: var(--tak-text);
       font-family: var(--tak-font); font-size: %(body)dpx; line-height: 1.6; }
.tak-panel { background: var(--tak-surface); border: 1px solid var(--tak-border);
             border-radius: var(--tak-radius-lg); padding: var(--tak-space-lg);
             max-width: 1080px; margin: var(--tak-space-xl) auto; }
.tak-header { display: flex; align-items: center; justify-content: space-between; }
.tak-header h1 { font-size: %(title)dpx; font-weight: 500; margin: 0; }
.tak-meta { color: var(--tak-text-muted); font-size: %(caption)dpx;
            margin: var(--tak-space-sm) 0 var(--tak-space-lg); }
.tak-state { padding: 2px 10px; border-radius: var(--tak-radius-sm);
             font-size: %(caption)dpx; }
.tak-state-pass { background: var(--tak-success); color: var(--tak-accent-text); }
.tak-state-fail { background: var(--tak-error); color: var(--tak-accent-text); }
.tak-table { width: 100%%; border-collapse: collapse; }
.tak-table th { text-align: left; color: var(--tak-text-muted);
                border-bottom: 1px solid var(--tak-border);
                padding: var(--tak-space-sm); font-weight: 500; }
.tak-table td { padding: var(--tak-space-sm); border-bottom: 1px solid var(--tak-border); }
.tak-row:nth-child(even) { background: var(--tak-table-alt); }
.tak-badge { background: var(--tak-severity-soft); border-radius: var(--tak-radius-sm);
             padding: 2px 8px; font-size: %(caption)dpx; }
.tak-sev-error .tak-badge, .tak-sev-blocker .tak-badge { color: var(--tak-error); }
.tak-sev-warning .tak-badge { color: var(--tak-warning); }
.tak-sev-info .tak-badge { color: var(--tak-info); }
.tak-hint { display: block; color: var(--tak-text-hint); font-size: %(caption)dpx; }
code { font-family: var(--tak-mono); color: var(--tak-accent); }
"""


def _load() -> dict:
    with open(_TOKENS, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _qss_values(tokens: dict, theme_name: str) -> dict:
    c = tokens["themes"][theme_name]
    r, s, f = tokens["radius"], tokens["spacing"], tokens["font"]
    values = dict(c)
    values.update(
        theme=theme_name, version=tokens["version"],
        family=f["family"], mono=f["mono"],
        body=f["size_body"], caption=f["size_caption"],
        size_display=f.get("size_display", f["size_title"] + 6),
        r_sm=r["sm"], r_md=r["md"], r_lg=r["lg"],
        s_xs=s["xs"], s_sm=s["sm"], s_md=s["md"], s_lg=s["lg"], s_xl=s["xl"],
    )
    return values


def _css_vars(colors: dict, selector: str) -> str:
    """把主题里的每个键都暴露为 --tak-* CSS 变量，token 增加时自动覆盖。"""
    lines = ["%s {" % selector]
    for key, value in colors.items():
        lines.append("  --tak-%s: %s;" % (key.replace("_", "-"), value))
    lines.append("}")
    return "\n".join(lines)


def _qss(tokens: dict, theme_name: str) -> str:
    return _QSS_TEMPLATE.format(**_qss_values(tokens, theme_name))


def _css(tokens: dict) -> str:
    r, s, f = tokens["radius"], tokens["spacing"], tokens["font"]
    return _CSS_TEMPLATE % dict(
        version=tokens["version"],
        dark_vars=_css_vars(tokens["themes"]["dark"], ":root"),
        light_vars=_css_vars(tokens["themes"]["light"], '[data-theme="light"]'),
        r_sm=r["sm"], r_md=r["md"], r_lg=r["lg"],
        s_xs=s["xs"], s_sm=s["sm"], s_md=s["md"], s_lg=s["lg"], s_xl=s["xl"],
        family=f["family"], mono=f["mono"],
        body=f["size_body"], title=f["size_title"], caption=f["size_caption"],
    )


def build(tokens: dict) -> dict:
    return {"theme.qss": _qss(tokens, "dark"),
            "theme_light.qss": _qss(tokens, "light"),
            "theme.css": _css(tokens)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="只校验，不写文件")
    args = parser.parse_args(argv)
    generated = build(_load())
    if args.check:
        stale = []
        for name, text in generated.items():
            path = os.path.join(_HERE, name)
            if not os.path.isfile(path):
                stale.append(name)
                continue
            with open(path, "r", encoding="utf-8") as fh:
                if fh.read() != text:
                    stale.append(name)
        print("STALE: %s" % ", ".join(stale) if stale else "OK: 主题文件与 tokens.json 一致")
        return 1 if stale else 0
    for name, text in generated.items():
        with open(os.path.join(_HERE, name), "w", encoding="utf-8") as fh:
            fh.write(text)
        print("generated %s (%d bytes)" % (name, len(text)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
