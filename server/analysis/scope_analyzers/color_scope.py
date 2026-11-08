# -*- coding: utf-8 -*-
"""
وحدة تحليل الألوان والتوكنز في نطاق كود الـ Composable (Color Scope Analyzer).
Extracts, maps, and analyzes colors and tokens within element AST scope.
"""

import os
import re
import logging
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Set, Any

from common.file_ops import is_ignored_path


def _is_theme_dependent(
    color_name: str,
    value: str,
    is_token: bool,
    usage_global: int = 1,
) -> bool:
    """
    تحقق هل هذا اللون يعتمد على الثيم (Dark/Light) أو هو لون ثابت.
    """
    if not is_token:
        return False
    name_lower = color_name.lower()
    theme_keywords = ["primary", "secondary", "background", "surface", "error", "onprimary", "onbackground", "onsurface"]
    if any(k in name_lower for k in theme_keywords):
        return True
    return False


def _find_token_by_value(project_root: Path, hex_argb: str) -> Optional[str]:
    """Find a token with this color value in DesignSystem.kt. Returns token name ONLY if exactly 1 match, else None."""
    from core.token_migrator import TokenMigrator
    migrator = TokenMigrator(project_root)
    ds = migrator._find_design_system()
    if not ds or not ds.exists():
        return None
    
    content = ds.read_text(encoding="utf-8")
    hex_clean = hex_argb.lstrip("#").upper()
    if hex_clean.startswith("0X"):
        hex_clean = hex_clean[2:]
    if len(hex_clean) == 6:
        target_argb = "FF" + hex_clean
    else:
        target_argb = hex_clean

    matches = []
    for match in re.finditer(
        r'val\s+(\w+):\s*Color[\s\S]*?getColor\(\s*"(\w+)"\s*,\s*Color\(0x([0-9A-Fa-f]+)\)',
        content,
    ):
        token_val = match.group(3).upper()
        if len(token_val) == 6:
            token_val = "FF" + token_val
        if token_val == target_argb:
            matches.append(match.group(1))
    
    if len(matches) != 1:
        return None
    
    return matches[0]


def _update_existing_token(
    token_name: str,
    new_value: str,
    project_root: Path,
) -> Dict:
    """
    Update an existing token's default value in DesignSystem.kt.
    No source code changes needed — only the default value in DesignSystem.kt changes.
    """
    from ..color_analyzer import _hex_to_color_expr
    from core.token_migrator import TokenMigrator
    migrator = TokenMigrator(project_root)
    ds = migrator._find_design_system()
    
    if not ds or not ds.exists():
        return {"success": False, "error": "DesignSystem.kt not found"}
    
    content = ds.read_text(encoding="utf-8")
    new_color_expr = _hex_to_color_expr(new_value)
    
    clean_name = token_name.split(".")[-1]

    pattern_dynamic = rf'((?:BridgeThemeRegistry\.)?getColor\(\s*"{re.escape(clean_name)}"\s*,\s*)(?:Color\([^)]*\)|Color\.\w+|0x[0-9A-Fa-f]+|[^)]+?)\)'
    pattern_standard = rf'(\bval\s+{re.escape(clean_name)}\s*(?::\s*Color)?\s*=\s*)(?:Color\([^)]*\)|Color\.\w+|0x[0-9A-Fa-f]+)'

    new_content, count = re.subn(pattern_dynamic, rf'\g<1>{new_color_expr})', content, count=1)
    if count == 0:
        wrap_replacement = rf'val {clean_name}: Color get() = BridgeThemeRegistry.getColor("{clean_name}", {new_color_expr})'
        new_content, count = re.subn(pattern_standard, wrap_replacement, content, count=1)
        if count > 0:
            if "import com.composebridge.agent.BridgeThemeRegistry" not in new_content:
                pkg_match = re.search(r'^(package\s+[^\n]+)', new_content, re.MULTILINE)
                if pkg_match:
                    pkg_line = pkg_match.group(1)
                    new_content = new_content.replace(
                        pkg_line,
                        f"{pkg_line}\n\nimport com.composebridge.agent.BridgeThemeRegistry",
                        1,
                    )
    
    if count == 0:
        return {
            "success": False,
            "error": f"Could not update token '{token_name}' — pattern not found in DesignSystem.kt",
        }
    
    target_line_no = 0
    old_line = ""
    new_line = ""
    for idx, line in enumerate(content.splitlines(), 1):
        if (f'"{clean_name}"' in line and 'getColor' in line) or re.search(rf'\bval\s+{re.escape(clean_name)}\b', line):
            old_line = line.strip()
            target_line_no = idx
            break
    for line in new_content.splitlines():
        if (f'"{clean_name}"' in line and 'getColor' in line) or re.search(rf'\bval\s+{re.escape(clean_name)}\b', line):
            new_line = line.strip()
            break
    
    try:
        from core.ast_finder import safe_create_backup
        safe_create_backup(ds, project_root=project_root)
    except Exception as be:
        print(f"[WARN] Failed to create backup for {ds.name}: {be}")

    ds.write_text(new_content, encoding="utf-8")
    
    try:
        from core.pattern_journal import PatternJournal
        journal = PatternJournal(project_root)
        file_rel = str(ds.relative_to(project_root)).replace("\\", "/")
        journal.record_change(
            file=file_rel,
            line=target_line_no,
            old_line=old_line or "",
            new_line=new_line or f"{token_name} -> {new_color_expr}",
            pattern_id=f"update_token_{token_name}",
        )
    except Exception:
        pass

    try:
        from network.ws_server import trigger_broadcast_refresh
        trigger_broadcast_refresh()
    except Exception:
        pass
    
    return {
        "success": True,
        "token_name": token_name,
        "new_value": new_value.lstrip("#"),
        "value": new_value,
        "value_expr": new_color_expr,
        "updated": True,
        "message": f"تم تحديث قيمة {token_name} إلى {new_value}",
    }


def _find_all_occurrences(project_root: Path, literal: str, token_name: str = "") -> List[Dict]:
    """ابحث عن كل occurrences الآمنة للـ literal في المشروع."""
    from ..color_analyzer import _normalize_color_literals
    from core.project_scanner import ProjectScanner
    scanner = ProjectScanner(project_root)
    occurrences = []

    clean_variations = _normalize_color_literals(literal, token_name)
    if not clean_variations:
        clean_variations = [literal]

    for kt_file in scanner.find_kt_files():
        if kt_file.name in ("DesignSystem.kt", "AppColors.kt", "Theme.kt"):
            continue
        rel_file = str(kt_file.relative_to(project_root))
        try:
            content = kt_file.read_text(encoding="utf-8")
            from ..ast_scope_resolver import _is_safe_compose_file, _is_safe_line, _detect_property
            is_safe, reason = _is_safe_compose_file(rel_file, content)
            if not is_safe:
                continue

            for i, line in enumerate(content.splitlines(), 1):
                if not _is_safe_line(line):
                    continue
                for v in clean_variations:
                    if v and v in line:
                        occurrences.append({
                            "file": rel_file,
                            "line": i,
                            "property": _detect_property(line),
                            "target_match": v,
                        })
                        break
        except Exception as e:
            logging.debug(f"Error scanning {kt_file}: {e}")

    return occurrences


def get_token_value_map(project_root: Path) -> Dict[str, str]:
    """Extract token names and their HEX values from DesignSystem.kt or AppColors.kt."""
    from core.token_migrator import TokenMigrator
    migrator = TokenMigrator(project_root)
    ds = migrator._find_design_system()
    if not ds or not ds.exists():
        return {}

    content = ds.read_text(encoding="utf-8")
    mapping = {}

    for m in re.finditer(
        r'val\s+(\w+)\s*:\s*Color[\s\S]*?getColor\(\s*"(\w+)"\s*,\s*Color\(0x([0-9A-Fa-f]+)\)',
        content,
    ):
        t_name = m.group(1)
        raw_hex = m.group(3).upper()
        if len(raw_hex) == 8:
            clean = f"#{raw_hex[2:]}"
        elif len(raw_hex) == 6:
            clean = f"#{raw_hex}"
        else:
            clean = f"#{raw_hex}"
        mapping[t_name] = clean

    for m in re.finditer(
        r'val\s+(\w+)\s*:\s*Color\s*=\s*Color\(0x([0-9A-Fa-f]+)\)',
        content,
    ):
        t_name = m.group(1)
        raw_hex = m.group(2).upper()
        if len(raw_hex) == 8:
            clean = f"#{raw_hex[2:]}"
        elif len(raw_hex) == 6:
            clean = f"#{raw_hex}"
        else:
            clean = f"#{raw_hex}"
        if t_name not in mapping:
            mapping[t_name] = clean

    return mapping


def get_global_usage_counts(project_root: Path) -> Dict[str, int]:
    """Scan project files and count global occurrences for each AppColors token and Color literal."""
    try:
        from core.token_baseline import build_baseline
        baseline = build_baseline(project_root)
        counts = {}
        for token_name, data in baseline.items():
            counts[token_name] = len(data.get("locations", []))
            hex_val = data.get("value", "").upper()
            if hex_val:
                counts[hex_val] = counts.get(hex_val, 0) + len(data.get("locations", []))
        if counts:
            return counts
    except Exception:
        pass

    counts: Dict[str, int] = {}
    from core.project_scanner import ProjectScanner
    scanner = ProjectScanner(project_root)
    token_pattern = re.compile(r'\bAppColors\.(\w+)\b')
    literal_pattern = re.compile(r'Color\s*\(\s*(0x[0-9A-Fa-f]+)\s*\)')

    for kt_file in scanner.find_kt_files():
        if kt_file.name in ("DesignSystem.kt", "AppColors.kt", "Theme.kt"):
            continue
        try:
            content = kt_file.read_text(encoding="utf-8")
            for t in token_pattern.findall(content):
                counts[t] = counts.get(t, 0) + 1
            for lit in literal_pattern.findall(content):
                clean = lit.upper()
                counts[clean] = counts.get(clean, 0) + 1
        except Exception:
            pass

    return counts


def _extract_colors_in_scope(
    search_scope: str,
    start_line_in_file: int,
    root: Path,
    kt_cache: Any,
) -> Tuple[List[str], List[str], List[Dict[str, Any]]]:
    """
    استخراج الألوان والتوكنز من نطاق الكود النصي.
    Extracts tokens, literals, and structured color objects from the textual scope.
    """
    from ..color_analyzer import _clean_hex, _find_color_locations, NAMED_COLORS_HEX
    from ..ast_scope_resolver import _detect_property

    token_pattern = re.compile(r'\bAppColors\.(\w+)\b')
    literal_pattern = re.compile(r'Color\s*\(\s*(0x[0-9A-Fa-f]+)\s*\)')
    named_pattern = re.compile(r'\bColor\.(White|Black|LightGray|Gray|DarkGray|Red|Green|Blue|Yellow|Cyan|Magenta|Transparent|Unspecified)\b')
    material_pattern = re.compile(r'\b(?:MaterialTheme\.)?colorScheme\.(\w+)\b')

    tokens = list(set(token_pattern.findall(search_scope)))
    literals = list(set(literal_pattern.findall(search_scope)))

    token_value_map = get_token_value_map(root)
    global_counts = get_global_usage_counts(root)

    element_color_occurrences: Dict[str, List[Dict]] = {}
    scope_lines = search_scope.splitlines()

    for idx, l_str in enumerate(scope_lines):
        line_no = start_line_in_file + idx
        prop = _detect_property(l_str)

        for m in token_pattern.finditer(l_str):
            t_name = m.group(1)
            t_hex = token_value_map.get(t_name, "#818CF8")
            occ_key = f"{t_name}::{prop}::{line_no}"
            if occ_key not in element_color_occurrences:
                element_color_occurrences[occ_key] = []
            element_color_occurrences[occ_key].append({
                "name": t_name,
                "value": t_hex,
                "is_token": True,
                "property": prop,
                "line": line_no,
            })

        for m in material_pattern.finditer(l_str):
            mat_name = m.group(0)
            rest_of_line = l_str[m.end():]
            copy_m = re.match(r'^\s*\.copy\s*\([^)]*\)', rest_of_line)
            if copy_m:
                mat_name = f"{mat_name}{copy_m.group(0)}"
            occ_key = f"{mat_name}::{prop}::{line_no}"
            if occ_key not in element_color_occurrences:
                element_color_occurrences[occ_key] = []
            element_color_occurrences[occ_key].append({
                "name": mat_name,
                "value": mat_name,
                "is_token": False,
                "property": prop,
                "line": line_no,
            })

        for m in named_pattern.finditer(l_str):
            col_name = m.group(0)
            simple_name = m.group(1)
            col_hex = NAMED_COLORS_HEX.get(simple_name, "#FFFFFF")
            rest_of_line = l_str[m.end():]
            copy_m = re.match(r'^\s*\.copy\s*\([^)]*\)', rest_of_line)
            if copy_m:
                col_name = f"{col_name}{copy_m.group(0)}"
                col_hex = col_name
            occ_key = f"{col_name}::{prop}::{line_no}"
            if occ_key not in element_color_occurrences:
                element_color_occurrences[occ_key] = []
            element_color_occurrences[occ_key].append({
                "name": col_name,
                "value": col_hex,
                "is_token": False,
                "property": prop,
                "line": line_no,
            })

        for m in literal_pattern.finditer(l_str):
            raw_hex = m.group(1)
            clean_val = _clean_hex(raw_hex)
            lit_name = f"Color({raw_hex})"
            rest_of_line = l_str[m.end():]
            copy_m = re.match(r'^\s*\.copy\s*\([^)]*\)', rest_of_line)
            if copy_m:
                lit_name = f"{lit_name}{copy_m.group(0)}"
                clean_val = lit_name
            occ_key = f"{lit_name}::{prop}::{line_no}"
            if occ_key not in element_color_occurrences:
                element_color_occurrences[occ_key] = []
            element_color_occurrences[occ_key].append({
                "name": lit_name,
                "value": clean_val,
                "is_token": False,
                "property": prop,
                "line": line_no,
            })

    structured_colors = []
    for c_name, occs in element_color_occurrences.items():
        primary_occ = occs[0]
        usage_in_element = len(occs)
        
        if primary_occ["is_token"]:
            usage_global = global_counts.get(c_name, usage_in_element)
        else:
            clean_val = primary_occ["value"].upper()
            usage_global = global_counts.get(c_name, global_counts.get(clean_val, usage_in_element))

        usage_global = max(usage_global, usage_in_element)
        is_shared = usage_global > usage_in_element

        locations = _find_color_locations(
            root,
            primary_occ["value"],
            primary_occ["name"] if primary_occ["is_token"] else None,
            kt_cache=kt_cache,
        )

        is_theme_dep = _is_theme_dependent(
            color_name=primary_occ["name"],
            value=primary_occ["value"],
            is_token=primary_occ["is_token"],
            usage_global=usage_global,
        )

        structured_colors.append({
            "name": primary_occ["name"],
            "value": primary_occ["value"],
            "usage_global": usage_global,
            "usage_in_element": usage_in_element,
            "is_shared": is_shared,
            "is_token": primary_occ["is_token"],
            "property": primary_occ["property"],
            "line_in_element": primary_occ["line"],
            "occurrences_in_element": [{"line": o["line"], "property": o["property"]} for o in occs],
            "locations": locations[:50],
            "is_theme_dependent": is_theme_dep,
        })

    return tokens, literals, structured_colors

