# -*- coding: utf-8 -*-
"""
محلل نطاق دوال Composable وتحديد الاستدعاءات والحدود.
Composable scope extractor, enclosing function/class resolver, and invocation counter.
"""

import re
from pathlib import Path
from typing import Optional, List, Tuple

try:
    from common.file_ops import is_ignored_path
except ImportError:
    try:
        from server.common.file_ops import is_ignored_path
    except ImportError:
        from ...common.file_ops import is_ignored_path


def _find_enclosing_function_or_class(content: str, line_number: int) -> str:
    """Finds enclosing @Composable function, standard function, or class around line_number."""
    if not content or line_number <= 0:
        return "Element"
    lines = content.splitlines()
    target_idx = min(len(lines) - 1, max(0, line_number - 1))

    # 1. Search backwards for @Composable fun or standard fun
    for idx in range(target_idx, -1, -1):
        line = lines[idx]
        m_fun = re.search(r'\b(?:(?:private|public|internal)\s+)*fun\s+([A-Za-z0-9_]+)\s*\(', line)
        if m_fun:
            name = m_fun.group(1)
            is_comp = False
            for prev_idx in range(idx, max(-1, idx - 4), -1):
                if '@Composable' in lines[prev_idx]:
                    is_comp = True
                    break
            if is_comp or name[0].isupper():
                return name

    # 2. Search backwards for class or object
    for idx in range(target_idx, -1, -1):
        line = lines[idx]
        m_cls = re.search(r'\b(?:class|object)\s+([A-Za-z0-9_]+)', line)
        if m_cls:
            return m_cls.group(1)

    return "Element"


def _extract_composable_scope(
    content: str,
    composable_name: str,
    project_root: Optional[Path] = None,
    current_file: str = "",
) -> Tuple[str, int, int, str]:
    """
    Extract full Composable function body.
    Returns (scope_text, start_line, end_line, found_file).
    """
    lines = content.splitlines()
    pattern = rf'\bfun\s+{re.escape(composable_name)}\s*\('
    
    start_idx = -1
    target_lines = lines
    found_file = current_file

    for i, line in enumerate(lines):
        if re.search(pattern, line):
            start_idx = i
            break
            
    # إذا لم توجد الدالة في الملف الحالي، ابحث عنها في بقية ملفات المشروع
    if start_idx < 0 and project_root:
        try:
            from core.project_scanner import ProjectScanner
            scanner = ProjectScanner(project_root)
            kt_finder = getattr(scanner, "find_kt_files", None) or getattr(scanner, "_find_kt_files")
            for kt_file in kt_finder():
                if kt_file.name == Path(current_file).name or "build" in str(kt_file).lower() or ".composebridge" in str(kt_file).lower():
                    continue
                try:
                    txt = kt_file.read_text(encoding="utf-8")
                    if composable_name in txt and f"fun {composable_name}" in txt:
                        flines = txt.splitlines()
                        for i, line in enumerate(flines):
                            if re.search(pattern, line):
                                start_idx = i
                                target_lines = flines
                                found_file = str(kt_file.relative_to(project_root)).replace("\\", "/")
                                break
                        if start_idx >= 0:
                            break
                except Exception:
                    continue
        except Exception:
            pass

    if start_idx < 0:
        return "", 0, 0, ""
    
    # ابحث عن نهاية الدالة (accolades balancing)
    depth = 0
    end_idx = start_idx
    found_open = False
    
    for i in range(start_idx, len(target_lines)):
        line = target_lines[i]
        for ch in line:
            if ch == '{':
                depth += 1
                found_open = True
            elif ch == '}':
                depth -= 1
        
        if found_open and depth == 0:
            end_idx = i
            break
    
    scope_text = "\n".join(target_lines[start_idx:end_idx + 1])
    return scope_text, start_idx + 1, end_idx + 1, found_file


def _count_function_calls(
    project_root: Path,
    composable_name: str,
    kt_cache: Optional[List[Tuple[str, str]]] = None,
) -> int:
    """احسب عدد مرات استدعاء الدالة في المشروع."""
    count = 0
    pattern = rf'\b{re.escape(composable_name)}\s*\('
    
    if kt_cache is None:
        try:
            from common.project_cache import PROJECT_CACHE
            kt_cache = PROJECT_CACHE.get_kt_cache(project_root)
        except Exception:
            kt_cache = None

    if kt_cache is not None:
        for rel_path, content in kt_cache:
            if any(x in rel_path.lower() for x in ["build", "backup", ".bak", ".composebridge"]):
                continue
            try:
                content_no_def = re.sub(
                    rf'\bfun\s+{re.escape(composable_name)}\s*\(',
                    f'_DEF_{composable_name}_',
                    content,
                )
                count += len(re.findall(pattern, content_no_def))
            except Exception:
                continue
        return count

    for kt_file in project_root.rglob("*.kt"):
        if is_ignored_path(kt_file, project_root):
            continue
        try:
            content = kt_file.read_text(encoding="utf-8")
            content_no_def = re.sub(
                rf'\bfun\s+{re.escape(composable_name)}\s*\(',
                f'_DEF_{composable_name}_',
                content,
            )
            count += len(re.findall(pattern, content_no_def))
        except Exception:
            continue
    
    return count

