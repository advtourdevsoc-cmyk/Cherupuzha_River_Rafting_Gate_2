import ast
import glob
import jinja2
import os

print("--- Step 1: Parsing Python Files for Syntax Errors ---")
py_files = glob.glob("**/*.py", recursive=True)
for f in py_files:
    if "venv" in f or ".git" in f:
        continue
    try:
        with open(f, 'r', encoding='utf-8') as file:
            ast.parse(file.read(), filename=f)
    except Exception as e:
        print(f"[SYNTAX ERROR] {f}: {e}")

print("\n--- Step 2: Parsing Jinja2 Templates ---")
env = jinja2.Environment()
template_files = glob.glob("templates/**/*.html", recursive=True)
for t in template_files:
    try:
        with open(t, 'r', encoding='utf-8') as file:
            env.parse(file.read())
    except Exception as e:
        print(f"[TEMPLATE ERROR] {t}: {e}")

print("Audit step 1 & 2 complete.")
