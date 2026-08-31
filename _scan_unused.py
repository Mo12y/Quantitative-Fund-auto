# -*- coding: utf-8 -*-
"""扫描 cli 模块的未使用 import（分析用，不修改）"""
import ast
import glob

for path in glob.glob("src/cli/*.py"):
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src)

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for a in node.names:
                imported.add(a.asname or a.name)
        elif isinstance(node, ast.Import):
            for a in node.names:
                imported.add((a.asname or a.name).split(".")[0])

    used = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            used.add(node.value.id)

    unused = sorted(imported - used)
    print(f"{path}: unused={unused}")
