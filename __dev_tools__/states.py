import re
import math
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

class C:
    DCYN = '\033[36m'
    CYN = '\033[96m'
    WHT = '\033[97m'
    YEL = '\033[93m'
    GRN = '\033[92m'
    GRY = '\033[90m'
    RST = '\033[0m'

W = 68
hr_line = "─" * W
hr2_line = "═" * W

def Section(title):
    print("")
    print(f"{C.DCYN}{hr2_line}{C.RST}")
    print(f" {C.CYN}{title}{C.RST}")
    print(f"{C.DCYN}{hr2_line}{C.RST}")

def Row(label, value, color=C.WHT):
    print(f"{color} {label:<28} {value}{C.RST}")

def Warn(msg): print(f"{C.YEL}  ⚠  {msg}{C.RST}")
def Good(msg): print(f"{C.GRN}  ✓  {msg}{C.RST}")
def Info(msg): print(f"{C.GRY}  ·  {msg}{C.RST}")

rx_comment = re.compile(r'^\s*#')
rx_docstr = re.compile(r'^\s*(\"\"\"|\'\'\')')
rx_class = re.compile(r'^\s*class\s+')
rx_func = re.compile(r'^\s*def\s+')
rx_import = re.compile(r'^\s*(import\s+|from\s+.+\s+import)')
rx_todo = re.compile(r'(TODO|FIXME|HACK|XXX|NOTE)')
rx_typehint = re.compile(r'(->\s*\w+|:\s*(str|int|bool|float|list|dict|tuple|set|Optional|Union|Any|List|Dict))')

file_stats = []
total_lines = 0
empty_lines = 0
comment_lines = 0
total_size = 0

now = datetime.now()

for path in Path(".").rglob("*.py"):
    if not path.is_file(): continue
    
    try:
        with open(path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except Exception:
        continue

    line_count = len(lines)
    empty_count = 0
    comment_count = 0
    docstr_count = 0
    class_count = 0
    func_count = 0
    import_count = 0
    todo_count = 0
    typehints = 0

    for line in lines:
        s_line = line.strip()
        if not s_line:
            empty_count += 1
            continue
            
        if rx_comment.search(line): comment_count += 1
        elif rx_docstr.search(line): docstr_count += 1
        
        if rx_class.search(line): class_count += 1
        if rx_func.search(line): func_count += 1
        if rx_import.search(line): import_count += 1
        if rx_todo.search(line): todo_count += 1
        if rx_typehint.search(line): typehints += 1

    code_count = line_count - empty_count - comment_count
    
    total_lines += line_count
    empty_lines += empty_count
    comment_lines += comment_count
    file_length = path.stat().st_size
    total_size += file_length

    density = round((code_count / line_count * 100), 1) if line_count > 0 else 0
    comment_ratio = round((comment_count / code_count * 100), 1) if code_count > 0 else 0

    est_vol = code_count * 5.0
    est_complexity = func_count * 2 + class_count * 3 + 1
    mi_raw = 171 - 5.2 * math.log(max(est_vol, 1)) - 0.23 * est_complexity - 16.2 * math.log(max(code_count, 1))
    maintainability_index = max(0.0, min(100.0, mi_raw * 100 / 171))

    file_stats.append({
        "Name": path.name,
        "Path": str(path),
        "Directory": path.parent.name if path.parent.name else ".",
        "Lines": line_count,
        "CodeLines": code_count,
        "EmptyLines": empty_count,
        "CommentLines": comment_count,
        "DocstrLines": docstr_count,
        "Classes": class_count,
        "Functions": func_count,
        "Imports": import_count,
        "TODOs": todo_count,
        "TypeHints": typehints,
        "CodeDensity": density,
        "CommentRatio": comment_ratio,
        "SizeKB": round(file_length / 1024, 2),
        "LastModified": datetime.fromtimestamp(path.stat().st_mtime),
        "Maintainability": round(maintainability_index, 1)
    })

total_files = len(file_stats)
code_lines = total_lines - empty_lines - comment_lines
total_classes = sum(f["Classes"] for f in file_stats)
total_funcs = sum(f["Functions"] for f in file_stats)
total_imports = sum(f["Imports"] for f in file_stats)
total_todos = sum(f["TODOs"] for f in file_stats)
total_hints = sum(f["TypeHints"] for f in file_stats)

complex_files = [f for f in file_stats if f["CodeLines"] > 300]
simple_files = [f for f in file_stats if f["CodeLines"] < 30]
dense_files = [f for f in file_stats if f["CodeDensity"] > 85]
sparse_files = [f for f in file_stats if f["CodeDensity"] < 40]
god_files = [f for f in file_stats if f["Classes"] >= 3 and f["Functions"] >= 10]
no_comment_files = [f for f in file_stats if f["CommentLines"] == 0 and f["CodeLines"] > 50]
stale_files = [f for f in file_stats if f["LastModified"] < now - timedelta(days=90)]
hot_files = [f for f in file_stats if f["LastModified"] > now - timedelta(days=7)]

size_groups = {"Tiny  (<50)": [], "Small (50-150)": [], "Med   (150-400)": [], "Large (400+)": []}
for f in file_stats:
    if f["Lines"] < 50: size_groups["Tiny  (<50)"].append(f["Name"])
    elif f["Lines"] < 150: size_groups["Small (50-150)"].append(f["Name"])
    elif f["Lines"] < 400: size_groups["Med   (150-400)"].append(f["Name"])
    else: size_groups["Large (400+)"].append(f["Name"])

dir_groups = defaultdict(list)
for f in file_stats: dir_groups[f["Directory"]].append(f)
dir_groups_sorted = sorted(dir_groups.items(), key=lambda x: len(x[1]), reverse=True)

largest_by_lines = sorted(file_stats, key=lambda x: x["Lines"], reverse=True)[:5]
most_funcs = sorted(file_stats, key=lambda x: x["Functions"], reverse=True)[:5]
most_classes = sorted(file_stats, key=lambda x: x["Classes"], reverse=True)[:5]
most_todos = sorted([f for f in file_stats if f["TODOs"] > 0], key=lambda x: x["TODOs"], reverse=True)[:5]
recently_changed = sorted(file_stats, key=lambda x: x["LastModified"], reverse=True)[:5]
worst_maintainability = sorted([f for f in file_stats if f["CodeLines"] > 20], key=lambda x: x["Maintainability"])[:5]

Section("OVERVIEW")
Row("Python files", total_files)
Row("Total lines", total_lines)
Row("Code lines", code_lines)
Row("Comment lines", f"{comment_lines}  ({round(comment_lines/max(total_lines,1)*100,1)}%)")
Row("Empty lines", f"{empty_lines}  ({round(empty_lines/max(total_lines,1)*100,1)}%)")
Row("Total size", f"{round(total_size/1024,1)} KB  /  {round(total_size/1048576,2)} MB")
Row("Avg lines / file", round(total_lines / max(total_files,1), 1))
Row("Avg code / file", round(code_lines / max(total_files,1), 1))

Section("STRUCTURE")
Row("Classes (total)", total_classes)
Row("Functions (total)", total_funcs)
Row("Imports (total)", total_imports)
Row("Type hints (total)", total_hints)
Row("TODOs / FIXMEs", total_todos)
Row("Avg func / file", round(total_funcs / max(total_files,1), 1))
Row("Avg class / file", round(total_classes / max(total_files,1), 1))

Section("SIZE DISTRIBUTION")
for group_name, files_list in size_groups.items():
    if files_list:
        Row(f"{group_name}  [{len(files_list)} files]", "  ".join(files_list))

Section("BY DIRECTORY")
for d_name, d_files in dir_groups_sorted:
    d_lines = sum(f["Lines"] for f in d_files)
    Row(f"{d_name}  [{len(d_files)} files]", f"{d_lines} lines total")

Section("TOP 5 — LARGEST FILES")
for f in largest_by_lines:
    Row(f["Name"], f"{f['Lines']} lines  ·  {f['CodeLines']} code  ·  {f['SizeKB']} KB")

Section("TOP 5 — MOST FUNCTIONS")
for f in most_funcs:
    Row(f["Name"], f"{f['Functions']} funcs  ·  {f['Classes']} classes")

Section("TOP 5 — MOST CLASSES")
for f in most_classes:
    Row(f["Name"], f"{f['Classes']} classes  ·  {f['Functions']} funcs")

Section("RECENTLY CHANGED  (last 7 days)")
if not hot_files:
    Info("No files changed in the last 7 days.")
else:
    for f in recently_changed:
        if f["LastModified"] > now - timedelta(days=7):
            Row(f["Name"], f["LastModified"].strftime("%Y-%m-%d %H:%M"), C.GRN)

Section("HEALTH ANALYSIS")

if complex_files:
    Warn("Large files (>300 code lines) — consider splitting:")
    for f in sorted(complex_files, key=lambda x: x["CodeLines"], reverse=True):
        Info(f"{f['Name']}  ({f['CodeLines']} code lines)")

if god_files:
    Warn("God files (3+ classes AND 10+ functions):")
    for f in god_files:
        Info(f"{f['Name']}  ({f['Classes']} classes, {f['Functions']} funcs)")

if no_comment_files:
    Warn("No comments but >50 code lines:")
    for f in sorted(no_comment_files, key=lambda x: x["CodeLines"], reverse=True)[:5]:
        Info(f"{f['Name']}  ({f['CodeLines']} code lines)")

if total_todos > 0:
    Warn(f"TODO/FIXME markers ({total_todos} total):")
    for f in most_todos:
        Info(f"{f['Name']}  ({f['TODOs']} markers)")

if sparse_files:
    Warn("Very sparse files (<40% code density):")
    for f in sorted(sparse_files, key=lambda x: x["CodeDensity"])[:3]:
        Info(f"{f['Name']}  ({f['CodeDensity']}% density)")

if stale_files:
    Warn(f"Stale files (not touched in 90+ days): {len(stale_files)} files")

if simple_files:
    Good(f"Tiny files (<30 code lines) that might be merged: {len(simple_files)} files")
    for f in sorted(simple_files, key=lambda x: x["CodeLines"])[:5]:
        Info(f"{f['Name']}  ({f['CodeLines']} code lines)")

if worst_maintainability:
    Warn("Low Maintainability Index:")
    for f in worst_maintainability:
        Info(f"{f['Name']}  (Score: {f['Maintainability']}/100)")

type_hint_ratio = round(total_hints / total_funcs * 100, 1) if total_funcs > 0 else 0
if type_hint_ratio < 30:
    Warn(f"Low type hint coverage: {type_hint_ratio}% of functions")
else:
    Good(f"Type hint coverage: {type_hint_ratio}%")

comment_coverage = round(comment_lines / max(code_lines,1) * 100, 1)
if comment_coverage < 10:
    Warn(f"Low comment coverage: {comment_coverage}%")
else:
    Good(f"Comment coverage: {comment_coverage}%")

print("")
print(f"{C.DCYN}{hr2_line}{C.RST}")
print("")