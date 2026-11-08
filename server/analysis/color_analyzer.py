# -*- coding: utf-8 -*-
"""
وحدة تحليل الألوان وتطبيع القيم اللفظية (Color Analysis & Literal Normalization Core).
Handles Compose color literals, Hex values, AppColors imports, and color locations discovery.
Re-exports operational functions (global_color_edit, split_token_for_element) from color_operations.
"""

import os
import re
import time
from pathlib import Path
from typing import Optional, List, Dict, Tuple, Set, Any

from common.file_ops import is_ignored_path, _rank
from common.color_constants import NAMED_COLORS_HEX, NAMED_COLOR_VARIANTS

try:
    from core.project_root import get_project_root
    PROJECT_ROOT = get_project_root()
except Exception:
    PROJECT_ROOT = Path(os.environ.get("COMPOSEBRIDGE_ROOT", "."))

from .ast_scope_resolver import (
    _is_safe_compose_file,
    _is_safe_line,
    _find_all_occurrences,
    _update_existing_token,
    _find_token_by_value,
    _find_file,
    _detect_property,
    _validate_modified_file,
)

try:
    from common.file_ops import _atomic_write_text, _find_registry_file
except ImportError:
    try:
        from server.common.file_ops import _atomic_write_text, _find_registry_file
    except ImportError:
        from ..common.file_ops import _atomic_write_text, _find_registry_file


def _normalize_color_literals(val: str, token_name: str = "") -> List[str]:
    """
    Generate all possible code representations of a color (hex or named).
    Returns list of exact search patterns ordered by specificity (longest first).
    """
    if not val:
        return []
    val_clean = val.strip().replace('"', '').replace("'", "")
    named_candidate = None
    if val_clean.startswith("Color."):
        named_candidate = val_clean.split(".")[1]
    elif val_clean in NAMED_COLORS_HEX:
        named_candidate = val_clean

    if named_candidate and named_candidate in NAMED_COLORS_HEX:
        hex_val = NAMED_COLORS_HEX[named_candidate].lstrip("#")
    elif val_clean.startswith("Color(0x") or val_clean.startswith("Color(0X"):
        m = re.search(r'0x([0-9A-Fa-f]+)', val_clean, re.IGNORECASE)
        hex_val = m.group(1) if m else val_clean.lstrip("#")
    elif val_clean.startswith("0x") or val_clean.startswith("0X"):
        hex_val = val_clean[2:]
    else:
        hex_val = val_clean.lstrip("#")

    hex_clean = hex_val.upper()
    if len(hex_clean) == 6:
        hex_argb = f"FF{hex_clean}"
    elif len(hex_clean) == 8:
        hex_argb = hex_clean
        hex_clean = hex_clean[2:]
    else:
        hex_argb = hex_clean

    variants = []
    if hex_argb:
        variants.append(f"Color(0x{hex_argb})")
        variants.append(f"Color(0x{hex_argb.lower()})")
        variants.append(f"Color(0xFF{hex_clean.lower()})")
    if hex_clean:
        variants.append(f"Color(0x{hex_clean})")
        variants.append(f"Color(0x{hex_clean.lower()})")
    if hex_argb in NAMED_COLOR_VARIANTS:
        variants.append(NAMED_COLOR_VARIANTS[hex_argb])
    if val_clean.startswith("Color."):
        variants.append(val_clean)
    if token_name:
        variants.append(f"AppColors.{token_name}")
    if re.match(r'^[A-Za-z]\w*$', val_clean) and not val_clean.startswith("0x") and not val_clean.startswith("Color"):
        variants.append(f"AppColors.{val_clean}")

    seen = set()
    unique_variants = []
    for v in sorted(variants, key=len, reverse=True):
        if v and v not in seen:
            seen.add(v)
            unique_variants.append(v)
    return unique_variants


def _clean_hex(val: str) -> str:
    """Converts 0xFF818CF8 or #818CF8 to clean #RRGGBB."""
    val = val.strip().replace('"', '').replace("'", "")
    if val.startswith("0x") or val.startswith("0X"):
        h = val[2:]
        if len(h) == 8:
            return f"#{h[2:].upper()}"
        elif len(h) == 6:
            return f"#{h.upper()}"
        return f"#{h.upper()}"
    if val.startswith("#"):
        return val.upper()
    return f"#{val.upper()}"


def _find_color_locations(
    project_root: Path,
    hex_value: str,
    token_name: str = None,
    kt_cache: Optional[List[Tuple[str, str]]] = None,
) -> List[Dict]:
    """
    Find all locations where this color appears in the project.
    Returns: [{"file": "...", "line": N, "property": "..."}]
    """
    try:
        locations = []
        variants = _normalize_color_literals(hex_value, token_name or "")
        
        if kt_cache is None:
            try:
                from common.project_cache import PROJECT_CACHE
                kt_cache = PROJECT_CACHE.get_kt_cache(project_root)
            except Exception:
                kt_cache = None

        if kt_cache is not None:
            for rel_path, file_content in kt_cache:
                if "backup" in rel_path.lower() or "build" in rel_path.lower():
                    continue
                for i, line in enumerate(file_content.splitlines(), 1):
                    if line.strip().startswith("//"):
                        continue
                    for variant in variants:
                        if variant in line:
                            property_match = re.search(
                                r'(\w+)\s*[=:]\s*' + re.escape(variant),
                                line,
                            )
                            prop = property_match.group(1) if property_match else _detect_property(line)
                            locations.append({
                                "file": rel_path,
                                "line": i,
                                "property": prop,
                            })
                            break
            return locations

        from core.project_scanner import ProjectScanner
        scanner = ProjectScanner(project_root)
        for kt_file in scanner.find_kt_files():
            if "backup" in str(kt_file).lower() or "build" in str(kt_file).lower():
                continue
            try:
                f_content = kt_file.read_text(encoding="utf-8")
                for i, line in enumerate(f_content.splitlines(), 1):
                    if line.strip().startswith("//"):
                        continue
                    for variant in variants:
                        if variant in line:
                            property_match = re.search(
                                r'(\w+)\s*[=:]\s*' + re.escape(variant),
                                line,
                            )
                            prop = property_match.group(1) if property_match else _detect_property(line)
                            locations.append({
                                "file": str(kt_file.relative_to(project_root)),
                                "line": i,
                                "property": prop,
                            })
                            break
            except Exception:
                continue
        
        return locations
    except Exception:
        return []


def _hex_to_color_expr(hex_val: str) -> str:
    """Convert '#10B981' or 'Color(...)' to 'Color(0xFF...)'."""
    if not hex_val:
        return "Color.Transparent"
    if hex_val.startswith("Color("):
        return hex_val
    if hex_val.startswith("Color."):
        return hex_val
    
    hex_clean = hex_val.lstrip("#").upper()
    if hex_clean.startswith("0X"):
        hex_clean = hex_clean[2:]
    if len(hex_clean) == 6:
        hex_clean = "FF" + hex_clean
    elif len(hex_clean) == 3:
        hex_clean = "FF" + "".join(c * 2 for c in hex_clean)
    elif len(hex_clean) != 8:
        return "Color.Transparent"
    return f"Color(0x{hex_clean})"


def _is_valid_kotlin_identifier(name: str) -> bool:
    if not name:
        return False
    if not name[0].isalpha() and name[0] != '_':
        return False
    return all(c.isalnum() or c == '_' for c in name)


def _detect_app_colors_pkg(file_path: Path) -> str:
    """Detect package of AppColors by searching for AppColors.kt or inspecting build.gradle.kts."""
    try:
        cur = file_path.parent
        project_root = None
        for _ in range(8):
            if any((cur / f).exists() for f in ["settings.gradle.kts", "settings.gradle", ".git"]):
                project_root = cur
                break
            if cur.parent == cur:
                break
            cur = cur.parent

        if project_root:
            for candidate in project_root.rglob("AppColors.kt"):
                if candidate.is_file() and not is_ignored_path(candidate, project_root):
                    c = candidate.read_text(encoding="utf-8", errors="ignore")
                    m = re.search(r'^\s*package\s+([\w.]+)', c, re.MULTILINE)
                    if m:
                        return m.group(1)

            for candidate in project_root.rglob("build.gradle.kts"):
                if candidate.is_file() and not is_ignored_path(candidate, project_root):
                    c = candidate.read_text(encoding="utf-8", errors="ignore")
                    m = re.search(r'namespace\s*=\s*["\']([\w.]+)["\']', c)
                    if m:
                        return f"{m.group(1)}.ui.theme"
    except Exception:
        pass
    return ""


def _ensure_app_colors_import(file_path: Path, content: str, app_colors_pkg: str = "") -> Tuple[str, bool, int, str]:
    """
    Ensure the file has an AppColors import.
    Returns (new_content, was_added, line_num, target_import).
    """
    pkg_match = re.search(r'^\s*package\s+([\w.]+)', content, re.MULTILINE)
    file_pkg = pkg_match.group(1) if pkg_match else ""

    if not app_colors_pkg:
        app_colors_pkg = _detect_app_colors_pkg(file_path)

    if not app_colors_pkg and file_pkg:
        parts = file_pkg.split('.')
        base = ".".join(parts[:3]) if len(parts) >= 3 else file_pkg
        app_colors_pkg = f"{base}.ui.theme"

    if app_colors_pkg and file_pkg == app_colors_pkg:
        return content, False, 0, ""

    target_import = f"import {app_colors_pkg}.AppColors" if app_colors_pkg else ""
    wildcard_import = f"import {app_colors_pkg}.*" if app_colors_pkg else ""

    if not target_import:
        return content, False, 0, ""

    import_patterns = [
        target_import,
        wildcard_import,
    ]
    for pattern in import_patterns:
        if pattern and pattern in content:
            return content, False, 0, ""

    lines = content.splitlines()
    last_import_idx = -1
    for i, line in enumerate(lines):
        if line.strip().startswith("import "):
            last_import_idx = i

    if last_import_idx >= 0:
        lines.insert(last_import_idx + 1, target_import)
        return "\n".join(lines), True, last_import_idx + 2, target_import

    for i, line in enumerate(lines):
        if line.strip().startswith("package "):
            lines.insert(i + 1, "")
            lines.insert(i + 2, target_import)
            return "\n".join(lines), True, i + 3, target_import

    lines.insert(0, target_import)
    return "\n".join(lines), True, 1, target_import


def preview_global_color_edit(
    old_value: str,
    project_root: Path,
) -> Dict:
    """Preview what will change before committing."""
    from core.project_scanner import ProjectScanner
    scanner = ProjectScanner(project_root)

    safe_occs = _find_all_occurrences(project_root, old_value)
    preview = {
        "total": len(safe_occs),
        "safe": safe_occs,
        "risky": [],
        "skipped": [],
    }

    clean_variations = _normalize_color_literals(old_value)
    kt_finder = getattr(scanner, "find_kt_files", None) or getattr(scanner, "_find_kt_files")
    for kt_file in kt_finder():
        if kt_file.name in ("DesignSystem.kt", "AppColors.kt", "Theme.kt"):
            continue
        rel_file = str(kt_file.relative_to(project_root))
        try:
            content = kt_file.read_text(encoding="utf-8")
            is_safe, reason = _is_safe_compose_file(rel_file, content)
            if not is_safe:
                for i, line in enumerate(content.splitlines(), 1):
                    if any(v in line for v in clean_variations):
                        preview["skipped"].append({
                            "file": rel_file,
                            "line": i,
                            "reason": reason,
                        })
        except Exception as e:
            preview["risky"].append({
                "file": rel_file,
                "error": str(e),
            })

    return {"success": True, "preview": preview}


# استيراد وإعادة تصدير عمليات الألوان (Re-export operations)
from .color_operations import global_color_edit, split_token_for_element

__all__ = [
    "NAMED_COLORS_HEX",
    "NAMED_COLOR_VARIANTS",
    "_normalize_color_literals",
    "_clean_hex",
    "_find_color_locations",
    "_hex_to_color_expr",
    "_is_valid_kotlin_identifier",
    "_detect_app_colors_pkg",
    "_ensure_app_colors_import",
    "preview_global_color_edit",
    "global_color_edit",
    "split_token_for_element",
]
