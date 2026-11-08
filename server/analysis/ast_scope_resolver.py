# -*- coding: utf-8 -*-
"""
واجهة محلل نطاق AST وتحليل مدى العناصر (AST Scope Resolver Facade).
AST scope resolver and element analyzer core facade.
Provides Composable scope extraction, usage counting, token discovery,
and element range inspection.
"""

import os
import re
import time
from pathlib import Path
from typing import Optional, List, Dict, Tuple, Set, Any

try:
    from core.project_root import get_project_root
    PROJECT_ROOT = get_project_root()
except Exception:
    PROJECT_ROOT = Path(os.environ.get("COMPOSEBRIDGE_ROOT", "."))

try:
    from common.file_ops import is_ignored_path, _atomic_write_text, _find_registry_file
except ImportError:
    try:
        from server.common.file_ops import is_ignored_path, _atomic_write_text, _find_registry_file
    except ImportError:
        from ..common.file_ops import is_ignored_path, _atomic_write_text, _find_registry_file

try:
    from common.color_constants import NAMED_COLORS_HEX, NAMED_COLOR_VARIANTS
except ImportError:
    try:
        from ..common.color_constants import NAMED_COLORS_HEX, NAMED_COLOR_VARIANTS
    except ImportError:
        from server.common.color_constants import NAMED_COLORS_HEX, NAMED_COLOR_VARIANTS

from common.kotlin_utils import (
    _clean_duplicate_imports,
    _validate_modified_file,
)

# استيراد وإعادة تصدير المحللات المعيارية (Re-export modular scope analyzers)
from .scope_analyzers import (
    _extract_colors_in_scope,
    _is_theme_dependent,
    _find_token_by_value,
    _update_existing_token,
    _find_all_occurrences,
    get_token_value_map,
    get_global_usage_counts,
    _extract_dimensions_in_scope,
    _extract_texts_in_scope,
    _find_matching_parenthesis,
    _find_enclosing_function_or_class,
    _extract_composable_scope,
    _count_function_calls,
)

RISKY_FILE_PATTERNS = [
    "ViewModel", "Repository", "DataSource", "Dao", "Database", 
    "Model", "Worker", "Service", "Entity", "Dto", "Test", "Preview"
]


def _detect_property(line: str) -> str:
    """Detects Compose color property name from code line."""
    prop_patterns = [
        (r'\bc1\s*[=:]', "c1"),
        (r'\bc2\s*[=:]', "c2"),
        (r'\bcontainerColor\s*[=:]', "containerColor"),
        (r'\bcontentColor\s*[=:]', "contentColor"),
        (r'\bdisabledContainerColor\s*[=:]', "disabledContainerColor"),
        (r'\bdisabledContentColor\s*[=:]', "disabledContentColor"),
        (r'\.background\s*\(|\bbackground\s*[=:]', "background"),
        (r'\.tint\s*\(|\btint\s*[=:]|\biconColor\s*[=:]', "tint"),
        (r'BorderStroke\s*\(|\bborder\s*[=:]|\.border\s*\(', "border"),
        (r'\btextColor\s*[=:]', "textColor"),
        (r'\.color\s*\(|\bcolor\s*[=:]', "color"),
    ]
    for pattern, name in prop_patterns:
        if re.search(pattern, line):
            return name
    return "color"





def _is_safe_compose_file(file_rel_path: str, content: str) -> Tuple[bool, str]:
    """Check if the file is safe to replace Compose color tokens."""
    fname = Path(file_rel_path).name

    for p in RISKY_FILE_PATTERNS:
        if p in fname:
            return False, f"Risky pattern in filename ({p})"

    is_compose = (
        "androidx.compose" in content
        and ("@Composable" in content or "import androidx.compose.ui.graphics.Color" in content or "androidx.compose.ui" in content)
    )
    if not is_compose:
        return False, "Not a Compose UI file"

    return True, "Safe"


def _is_safe_line(line: str) -> bool:
    """Check if a line is safe for replacement."""
    stripped = line.strip()
    if stripped.startswith("//") or stripped.startswith("*") or stripped.startswith("/*"):
        return False
    if "@ColorInt" in line or "toArgb()" in line or "android.graphics.Color" in line:
        return False
    return True


def _find_file(source_file: str, root: Path = PROJECT_ROOT) -> Optional[Path]:
    """Finds file in project root using ProjectCache."""
    full_path = root / source_file
    if full_path.exists():
        return full_path

    try:
        from common.project_cache import PROJECT_CACHE
        cached = PROJECT_CACHE.find_file(source_file, root)
        if cached and cached.exists():
            return cached
    except Exception:
        pass

    filename = Path(source_file).name
    candidates = [
        c for c in root.rglob(filename)
        if not is_ignored_path(c, root)
    ]
    return candidates[0] if candidates else None


def _extract_utf16_range(content: str, offset: int, length: int) -> str:
    """Extracts range using UTF-16 offset."""
    try:
        encoded = content.encode('utf-16-le')
        start_byte = offset * 2
        end_byte = (offset + length) * 2
        if end_byte > len(encoded):
            end_byte = len(encoded)
        sub = encoded[start_byte:end_byte]
        return sub.decode('utf-16-le', errors='ignore')
    except Exception as e:
        print(f"[source_analyzer] UTF-16 extract failed: {e}")
        return ""


def _line_context_with_start(content: str, line_number: int, before: int = 3, after: int = 20) -> Tuple[str, int]:
    """Returns lines around line_number along with 1-based start line, isolating enclosing Composable call if possible."""
    lines = content.splitlines()
    idx = line_number - 1
    if idx < 0 or idx >= len(lines):
        return "", 1

    # Check if line_number is a comment or blank, look ahead a few lines for the Composable call
    target_idx = idx
    check_indices = [target_idx]
    if lines[idx].strip().startswith("//") or not lines[idx].strip():
        for fwd in range(1, 4):
            if idx + fwd < len(lines):
                check_indices.append(idx + fwd)

    call_start = None
    for cand in check_indices:
        l = lines[cand]
        if re.search(r'^\s*[a-zA-Z0-9_]+\s*=', l):
            continue
        if re.search(r'\b[A-Z]\w*\s*\(', l):
            call_start = cand
            break

    # If not found looking ahead/at target, check backwards up to 15 lines, ensuring call doesn't close before target_idx
    if call_start is None:
        for b in range(1, min(15, idx + 1)):
            cand = idx - b
            l = lines[cand]
            if re.search(r'^\s*[a-zA-Z0-9_]+\s*=', l):
                continue
            if re.search(r'\b[A-Z]\w*\s*\(', l):
                test_depth = 0
                test_open = False
                closed_before_idx = False
                for cur_idx in range(cand, min(cand + 50, len(lines))):
                    l = lines[cur_idx]
                    test_depth += l.count('(') - l.count(')')
                    if '(' in l:
                        test_open = True
                    if test_open and test_depth <= 0:
                        if cur_idx < target_idx:
                            closed_before_idx = True
                        break
                if not closed_before_idx:
                    call_start = cand
                    break

    if call_start is None:
        call_start = idx

    depth = 0
    collected = []
    found_open = False
    for l in lines[call_start:]:
        collected.append(l)
        depth += l.count('(') - l.count(')')
        if '(' in l:
            found_open = True
        if found_open and depth <= 0:
            break

    if collected and found_open and len(collected) <= 50:
        return "\n".join(collected), call_start + 1

    start = max(0, idx - before)
    end = min(len(lines), idx + after)
    return "\n".join(lines[start:end]), start + 1


def _empty_result(error: str = "") -> dict:
    return {
        "composable_name": "Element",
        "colors": [],
        "tokens_used": [],
        "literals": [],
        "dimensions": [],
        "dimension_details": [],
        "bridge_spacing_tokens": [],
        "font_sizes": [],
        "font_weights": [],
        "properties": [],
        "composables": [],
        "scope_size": 0,
        "error": error,
    }


def _resolve_element_file_and_scope(
    source_file: str,
    line_number: int,
    utf16_offset: int,
    utf16_length: int,
    root: Path,
    tree: Optional[list],
) -> Tuple[Optional[Path], str, int, str, int, Optional[str], Optional[dict]]:
    """
    تحديد الملف الحقيقي ونطاق الكود (Scope) للعنصر المستهدف.
    Resolves the actual target source file, its content, and the precise textual scope.
    """
    actual_source_file = source_file
    actual_line_number = line_number
    if tree and isinstance(tree, list):
        for node in tree:
            if isinstance(node, dict):
                sf = node.get("sourceFile")
                if sf and sf != source_file and not sf.endswith(source_file):
                    sf_path = _find_file(sf, root)
                    if sf_path and sf_path.exists():
                        actual_source_file = sf
                        actual_line_number = node.get("line", line_number)
                        break

    full_path = _find_file(actual_source_file, root)
    if full_path is None:
        full_path = _find_file(source_file, root)
        actual_source_file = source_file

    if full_path is None:
        return None, actual_source_file, actual_line_number, "", 1, None, _empty_result(error=f"File not found: {source_file}")

    try:
        content = full_path.read_text(encoding="utf-8")
    except Exception as e:
        return full_path, actual_source_file, actual_line_number, "", 1, None, _empty_result(error=f"Read failed: {e}")

    start_line_in_file = 1
    if utf16_offset > 0 and utf16_length > 0 and actual_source_file == source_file:
        element_code = _extract_utf16_range(content, utf16_offset, utf16_length)
        if element_code:
            search_scope = element_code
            prefix = content.encode('utf-16-le')[:utf16_offset * 2].decode('utf-16-le', errors='ignore')
            start_line_in_file = prefix.count('\n') + 1
        else:
            search_scope, start_line_in_file = _line_context_with_start(content, actual_line_number)
    else:
        search_scope, start_line_in_file = _line_context_with_start(content, actual_line_number)

    if not search_scope:
        return full_path, actual_source_file, actual_line_number, content, start_line_in_file, "", _empty_result(error="Empty scope")

    return full_path, actual_source_file, actual_line_number, content, start_line_in_file, search_scope, None


def analyze_element_range(
    source_file: str,
    line_number: int,
    utf16_offset: int = 0,
    utf16_length: int = 0,
    project_root: Optional[Path] = None,
    tree: Optional[list] = None,
    runtime_text_hint: Optional[str] = None,
    parent_line: Optional[int] = None,
) -> dict:
    """
    Analyzes literal source code range of an element.
    Returns dimensions, tokens, literals, and structured Focus Mode color objects.
    Decomposed into specialized sub-extractors for clarity and maintainability.
    """
    from .text_ref_analyzer import _detect_composable_name
    root = project_root or PROJECT_ROOT

    # 1. تحديد الملف الحقيقي والنطاق النصي (File & Scope Resolution)
    full_path, actual_source_file, actual_line_number, content, start_line_in_file, search_scope, error_res = _resolve_element_file_and_scope(
        source_file, line_number, utf16_offset, utf16_length, root, tree
    )
    if error_res is not None:
        return error_res

    # تجهيز الكاش المشترك
    try:
        from common.project_cache import PROJECT_CACHE
        kt_cache = PROJECT_CACHE.get_kt_cache(root)
    except Exception:
        kt_cache = None

    # 2. استخراج الألوان والتوكنز (Colors & Tokens Extraction)
    tokens, literals, structured_colors = _extract_colors_in_scope(
        search_scope, start_line_in_file, root, kt_cache
    )

    # 3. تحديد اسم الـ Composable المحيط
    composable_name = _detect_composable_name(
        search_scope,
        fallback_line_content=content.splitlines()[line_number - 1] if 0 < line_number <= len(content.splitlines()) else "",
        full_content=content,
        line_number=actual_line_number
    )
    if composable_name in ["Element", "Text", "BasicText", "Column", "Row", "Box", "Spacer"] and content:
        enclosing_name = _find_enclosing_function_or_class(content, actual_line_number)
        if enclosing_name != "Element":
            composable_name = enclosing_name

    # 4. استخراج الأبعاد (Dimensions Extraction)
    filtered_dims, dim_scope_text, dim_scope_start_line, effective_file, bridge_tokens, sps, weights, properties = _extract_dimensions_in_scope(
        content, search_scope, start_line_in_file, actual_source_file, actual_line_number, composable_name, root, kt_cache
    )

    # 5. استخراج النصوص ومراجع الموارد (Texts Extraction)
    structured_texts = _extract_texts_in_scope(
        search_scope, start_line_in_file, dim_scope_text, dim_scope_start_line,
        actual_source_file, effective_file, content, root, parent_line, runtime_text_hint
    )

    return {
        "composable_name": composable_name,
        "texts": structured_texts,
        "source_file": effective_file,
        "colors": structured_colors,
        "tokens_used": [f"AppColors.{t}" for t in tokens],
        "literals": literals,
        "dimensions": [d["value"] for d in filtered_dims],
        "dimension_details": filtered_dims,
        "bridge_spacing_tokens": bridge_tokens,
        "font_sizes": list(set(sps))[:10],
        "font_weights": weights,
        "properties": properties,
        "composables": [composable_name],
        "scope_size": len(search_scope),
        "used_exact_range": utf16_offset > 0 and utf16_length > 0,
    }
