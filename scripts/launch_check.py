#!/usr/bin/env python3
"""日本語サイトの公開前チェック。

使い方:
  python3 launch_check.py <ビルド済みディレクトリ>
  python3 launch_check.py https://example.com/ https://example.com/about/
  python3 launch_check.py --urls urls.txt
終了コード: 0 = 通過 / 1 = FAIL あり（--strict なら WARN でも）/ 2 = 指定の誤り
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from launchcheck.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
