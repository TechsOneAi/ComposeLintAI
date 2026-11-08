# -*- coding: utf-8 -*-
"""
وحدة عمليات تعديل الألوان الشاملة (Global Color Edit Operation).
Contains global_color_edit and re-exports split_token_for_element.
"""

import os
import re
import time
from pathlib import Path
from typing import Optional, List, Dict, Tuple, Set, Any

try:
    from common.file_ops import is_ignored_path, _rank, _atomic_write_text
except ImportError:
    try:
        from server.common.file_ops import is_ignored_path, _rank, _atomic_write_text
    except ImportError:
        from ..common.file_ops import is_ignored_path, _rank, _atomic_write_text

from .ast_scope_resolver import (
    _is_safe_compose_file,
    _is_safe_line,
    _find_all_occurrences,
    _update_existing_token,
)
from .color_analyzer import (
    _normalize_color_literals,
    _hex_to_color_expr,
    _is_valid_kotlin_identifier,
    _ensure_app_colors_import,
)

from .token_split import split_token_for_element


def global_color_edit(
    file: str,
    line: int,
    old_value: str,
    new_value: str,
    token_name: str,
    project_root: Path,
    property_name: str = "all",
) -> Dict:
    """
    Global Color Edit:
    - If old_value is literal (#... or Color(...)): search literal occurrences only,
      replace literals with AppColors.<token>, and do NOT modify existing tokens.
    - If old_value is token (or matches token name): update only that single token in DesignSystem.kt.
    """
    try:
        from core.token_migrator import TokenMigrator
        from core.pattern_journal import PatternJournal
        
        migrator = TokenMigrator(project_root)
        journal = PatternJournal(project_root)
        
        ds_file = migrator._find_design_system()
        ds_content = ds_file.read_text(encoding="utf-8") if ds_file and ds_file.exists() else ""

        old_val_str = (old_value or "").strip()
        clean_token = (token_name or "").replace("AppColors.", "").strip()
        clean_old = old_val_str.replace("AppColors.", "").strip()

        is_token = False
        target_token_name = ""
        if clean_token and _is_valid_kotlin_identifier(clean_token) and re.search(rf'\bval\s+{re.escape(clean_token)}\b', ds_content):
            is_token = True
            target_token_name = clean_token
        elif clean_old and _is_valid_kotlin_identifier(clean_old) and re.search(rf'\bval\s+{re.escape(clean_old)}\b', ds_content):
            is_token = True
            target_token_name = clean_old

        if is_token:
            print(f"[OK] [UPDATE] Token {target_token_name}: {old_val_str} -> {new_value}")
            return _update_existing_token(
                token_name=target_token_name,
                new_value=new_value,
                project_root=project_root,
            )

        if clean_token and _is_valid_kotlin_identifier(clean_token):
            final_token_name = clean_token
        else:
            from core.name_suggester import NameSuggester
            final_token_name = NameSuggester().suggest_name(new_value, "color") or f"SuccessColor{int(time.time()) % 1000}"

        token_exists_in_ds = bool(re.search(rf'\bval\s+{re.escape(final_token_name)}\b', ds_content))

        if not token_exists_in_ds:
            new_expr = _hex_to_color_expr(new_value)
            create_result = migrator.create_color_token(
                token_name=final_token_name,
                value_expr=new_expr,
                original_value=new_expr,
            )
            if not create_result.get("success"):
                return create_result

        app_colors_pkg = ""
        if ds_file and ds_file.exists():
            pkg_m = re.search(r'^\s*package\s+([\w.]+)', ds_content, re.MULTILINE)
            if pkg_m:
                app_colors_pkg = pkg_m.group(1)

        if property_name and property_name != "all":
            file_path = project_root / file
            if not file_path.exists():
                matches = [m for m in project_root.rglob(Path(file).name) if "build" not in str(m) and ".composebridge" not in str(m)]
                if matches:
                    file_path = matches[0]
            
            matched_var = old_value
            if file_path.exists():
                f_lines = file_path.read_text(encoding="utf-8").splitlines()
                if 1 <= line <= len(f_lines):
                    target_l = f_lines[line - 1]
                    for v in [f"Color(0xFF{old_value.lstrip('#')})", f"Color(0xff{old_value.lstrip('#').lower()})", old_value]:
                        if v in target_l:
                            matched_var = v
                            break
                    if matched_var not in target_l:
                        lit_m = re.search(r'Color\s*\(\s*0x[0-9A-Fa-f]+\s*\)|Color\.\w+', target_l)
                        if lit_m:
                            matched_var = lit_m.group(0)

            all_occurrences = [{
                "file": str(file_path.relative_to(project_root)) if file_path.exists() else file,
                "line": line,
                "property": property_name,
                "matched_variant": matched_var,
            }]
        else:
            all_occurrences = _find_all_occurrences(project_root, old_value, final_token_name)

        replaced = 0
        errors = []
        skipped_files = []
        
        by_file: Dict[str, List[Dict]] = {}
        for occ in all_occurrences:
            f = occ["file"]
            if f not in by_file:
                by_file[f] = []
            by_file[f].append(occ)
        
        for f, occs in by_file.items():
            file_path = project_root / f
            if not file_path.exists():
                matches = [m for m in project_root.rglob(Path(f).name) if not is_ignored_path(m, project_root)]
                if not matches:
                    errors.append({"file": f, "error": "not found"})
                    continue
                matches.sort(key=lambda p: (_rank(p), len(str(p))))
                file_path = matches[0]
            
            try:
                content = file_path.read_text(encoding="utf-8")
                is_safe, reason = _is_safe_compose_file(f, content)
                if not is_safe:
                    skipped_files.append({"file": f, "reason": reason})
                    continue

                from core.ast_finder import safe_create_backup, validate_kotlin_syntax
                is_valid, err_syntax = validate_kotlin_syntax(file_path)
                if not is_valid:
                    skipped_files.append({"file": f, "reason": f"Syntax error: {err_syntax}"})
                    continue

                ok_bak, err_bak, _ = safe_create_backup(file_path, project_root=project_root)
                if not ok_bak:
                    errors.append({"file": f, "error": f"Backup failed: {err_bak}"})
                    continue

                lines = content.splitlines()
                file_replaced_in_this_file = False
                gce_changes = []
                
                sorted_occs = sorted(occs, key=lambda x: x["line"], reverse=True)
                for occ in sorted_occs:
                    occ_line = occ["line"]
                    target_match = occ.get("matched_variant") or occ.get("target_match", old_value)
                    if occ_line < 1 or occ_line > len(lines):
                        continue
                    
                    cur_line = lines[occ_line - 1]
                    if not _is_safe_line(cur_line):
                        continue

                    if f"AppColors.{final_token_name}" in cur_line:
                        continue
                    
                    if target_match in cur_line:
                        new_line = cur_line.replace(target_match, f"AppColors.{final_token_name}")
                    else:
                        matched_in_line = None
                        for v in _normalize_color_literals(old_value, final_token_name):
                            if v and v in cur_line:
                                matched_in_line = v
                                break
                        if matched_in_line:
                            new_line = cur_line.replace(matched_in_line, f"AppColors.{final_token_name}")
                        else:
                            continue
                    
                    if new_line != cur_line:
                        lines[occ_line - 1] = new_line
                        gce_changes.append((occ_line, cur_line, new_line))
                        replaced += 1
                        file_replaced_in_this_file = True
                
                if file_replaced_in_this_file:
                    updated_content = "\n".join(lines)
                    updated_content, was_added, import_line, target_import = _ensure_app_colors_import(file_path, updated_content, app_colors_pkg)
                    if was_added:
                        journal.record_change(
                            file=f,
                            line=import_line,
                            old_line="",
                            new_line=target_import,
                            pattern_id=f"global_color_{final_token_name}",
                        )
                    try:
                        _atomic_write_text(file_path, updated_content + "\n")
                    except Exception:
                        file_path.write_text(updated_content + "\n", encoding="utf-8")
                    for occ_l, old_l, new_l in gce_changes:
                        journal.record_change(
                            file=f,
                            line=occ_l,
                            old_line=old_l,
                            new_line=new_l,
                            pattern_id=f"global_color_{final_token_name}",
                        )
            
            except Exception as e:
                errors.append({"file": f, "error": str(e)})

        if replaced == 0 and not token_exists_in_ds:
            if ds_file and ds_file.exists() and ds_content:
                try:
                    _atomic_write_text(ds_file, ds_content)
                except Exception:
                    pass
            return {
                "success": False,
                "error": f"لا توجد occurrences لـ {old_value}. ربما تم استبدالها بالفعل بـ token آخر.",
                "token_name": final_token_name,
                "hint": "حاول Reject All ثم أعد المحاولة",
                "replaced_count": 0,
                "files_affected": 0,
                "skipped": skipped_files,
                "errors": errors,
            }

        return {
            "success": True,
            "token_name": final_token_name,
            "new_value": new_value.lstrip("#"),
            "value": new_value,
            "value_expr": _hex_to_color_expr(new_value),
            "replaced_count": replaced,
            "files_affected": len([f for f in by_file if f not in [s["file"] for s in skipped_files]]),
            "skipped": skipped_files,
            "errors": errors,
            "created": not token_exists_in_ds,
            "updated": False,
        }
    
    except Exception as e:
        if not token_exists_in_ds and ds_file and ds_file.exists() and ds_content:
            try:
                _atomic_write_text(ds_file, ds_content)
            except Exception:
                pass
        return {"success": False, "error": str(e)}


__all__ = [
    "global_color_edit",
    "split_token_for_element",
]
