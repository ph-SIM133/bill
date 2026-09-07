"""外来服薬支援のバージョン情報。

リリース時は VERSION だけを更新する。画面表示、保存記録、
Windows exe のプロパティ、ビルド記録はすべてこの値を使う。
"""

from __future__ import annotations

APP_NAME = "外来服薬支援"
EXECUTABLE_NAME = "medi_sup"
VERSION = "0.15.30"


def numeric_version() -> tuple[int, int, int, int]:
    """Windows バージョンリソース用の4要素を返す。"""
    values = [int(part) for part in VERSION.split(".")]
    if len(values) > 4:
        raise ValueError("VERSION は4要素以内で指定してください。")
    return tuple((values + [0] * (4 - len(values)))[:4])
