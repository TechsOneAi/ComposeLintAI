# -*- coding: utf-8 -*-
"""
وحدة تفريع التوكنات للعناصر المحددة (Token Split Operation).
Contains split_token_for_element for creating dedicated tokens for individual UI elements.
"""

import os
import re
from pathlib import Path
from typing import Optional, List, Dict, Any

try:
    from common.file_ops import is_ignored_path, _atomic_write_text, apply_safe_edit
except ImportError:
    try:
        from server.common.file_ops import is_ignored_path, _atomic_write_text, apply_safe_edit
    except ImportError:
        from ..common.file_ops import is_ignored_path, _atomic_write_text, apply_safe_edit

from .color_analyzer import (
    _normalize_color_literals,
    _hex_to_color_expr,
    _ensure_app_colors_import,
)


def split_token_for_element(
    original_token: str,
    original_value: str,
    new_token_name: str,
    new_value: str,
    element_file: str,
    element_line: int,
    occurrences: List[Dict],
    project_root: Path,
) -> Dict:
    """
    Split: create token ONLY for this element + replace occurrences in scope.
    Other occurrences in project: unchanged.
    """
    try:
        from core.token_migrator import TokenMigrator
        from core.pattern_journal import PatternJournal
        
        migrator = TokenMigrator(project_root)
        journal = PatternJournal(project_root)
        
        app_colors_pkg = ""
        ds_file = migrator._find_design_system()
        ds_content = ds_file.read_text(encoding="utf-8") if ds_file and ds_file.exists() else ""
        if ds_content:
            pkg_m = re.search(r'^\s*package\s+([\w.]+)', ds_content, re.MULTILINE)
            if pkg_m:
                app_colors_pkg = pkg_m.group(1)

        def _get_token_color_hex(tname: str) -> Optional[str]:
            m = re.search(
                rf'val\s+{re.escape(tname)}\b[^\n]*?getColor\(\s*"{re.escape(tname)}"\s*,\s*Color\(\s*0x([0-9A-Fa-f]+)\s*\)',
                ds_content,
            )
            if m:
                val = m.group(1).upper()
                return "FF" + val if len(val) == 6 else val
            m_stat = re.search(
                rf'\bval\s+{re.escape(tname)}\b[^\n=]*=\s*Color\(\s*0x([0-9A-Fa-f]+)\s*\)',
                ds_content,
            )
            if m_stat:
                val = m_stat.group(1).upper()
                return "FF" + val if len(val) == 6 else val
            return None

        new_hex_clean = (new_value or "").lstrip("#").upper()
        if new_hex_clean.startswith("0X"):
            new_hex_clean = new_hex_clean[2:]
        target_argb = ("FF" + new_hex_clean) if len(new_hex_clean) == 6 else new_hex_clean

        final_token_name = new_token_name
        token_already_exists = False

        if re.search(rf'\bval\s+{re.escape(final_token_name)}\b', ds_content):
            existing_val = _get_token_color_hex(final_token_name)
            if existing_val == target_argb:
                token_already_exists = True
            else:
                suffix = 2
                while True:
                    cand_name = f"{new_token_name}{suffix}"
                    if not re.search(rf'\bval\s+{re.escape(cand_name)}\b', ds_content):
                        final_token_name = cand_name
                        break
                    cand_val = _get_token_color_hex(cand_name)
                    if cand_val == target_argb:
                        final_token_name = cand_name
                        token_already_exists = True
                        break
                    suffix += 1

        new_expr = _hex_to_color_expr(new_value)
        orig_ds_content = ds_content
        token_created_in_ds = False

        def _rollback_ds():
            if token_created_in_ds and ds_file and ds_file.exists() and orig_ds_content:
                try:
                    _atomic_write_text(ds_file, orig_ds_content)
                except Exception:
                    try:
                        ds_file.write_text(orig_ds_content, encoding="utf-8")
                    except Exception:
                        pass

        if not token_already_exists:
            result = migrator.create_color_token(
                token_name=final_token_name,
                value_expr=new_expr,
                original_value=new_expr,
            )
            if not result.get("success"):
                return result
            token_created_in_ds = True
            print(f"[OK] [SPLIT] Token {final_token_name}: created")
        else:
            print(f"[OK] [SPLIT] Token {final_token_name}: already exists with matching value, reusing")

        file_path = project_root / element_file
        if not file_path.exists() or "backup" in str(file_path).lower():
            clean_matches = [
                m for m in project_root.rglob(Path(element_file).name)
                if "build" not in str(m)
                and ".composebridge" not in str(m)
                and "backup" not in str(m).lower()
            ]
            if not clean_matches:
                _rollback_ds()
                return {"success": False, "error": f"File not found: {element_file}"}
            src_matches = [m for m in clean_matches if "src" in str(m).replace("\\", "/").split("/")]
            file_path = src_matches[0] if src_matches else clean_matches[0]
        
        from core.ast_finder import safe_create_backup, validate_kotlin_syntax
        is_valid, err_syntax = validate_kotlin_syntax(file_path)
        if not is_valid:
            _rollback_ds()
            return {"success": False, "error": f"الملف الحالي يحتوي على أخطاء برمجية ({err_syntax})، يرجى إصلاحه أولاً."}

        ok_bak, err_bak, _ = safe_create_backup(file_path, project_root=project_root)
        if not ok_bak:
            _rollback_ds()
            return {"success": False, "error": f"Backup failed for {file_path.name}: {err_bak}"}

        content = file_path.read_text(encoding="utf-8")
        lines = content.splitlines()
        replaced = 0
        ste_changes = []
        
        if not occurrences and element_line > 0:
            occurrences = []
            cand_search = _normalize_color_literals(original_value or original_token)
            clean_token = (original_token or "").replace("AppColors.", "").strip()
            clean_hex = (original_value or "").lstrip("#").upper()
            if clean_token:
                cand_search.extend([f"AppColors.{clean_token}", clean_token])

            start_i = max(0, element_line - 25)
            end_i = min(len(lines), element_line + 30)
            local_matches = []
            for idx in range(start_i, end_i):
                l_txt = lines[idx]
                if (clean_token and f"AppColors.{clean_token}" in l_txt) or any(c in l_txt for c in cand_search) or (clean_hex and len(clean_hex) == 6 and clean_hex in l_txt):
                    local_matches.append(idx + 1)
            
            if len(local_matches) == 1:
                occurrences.append({"line": local_matches[0], "property": ""})
            elif len(local_matches) > 1:
                _rollback_ds()
                return {
                    "success": False,
                    "error": f"AMBIGUOUS_SPLIT_MATCH: وُجد أكثر من سطر ({local_matches}) يحتوي على اللون '{original_token or original_value}' في النطاق القريب. يرجى التحديد اليدوي.",
                }
            else:
                file_matches = []
                for idx, l_txt in enumerate(lines):
                    if (clean_token and f"AppColors.{clean_token}" in l_txt) or any(c in l_txt for c in cand_search) or (clean_hex and len(clean_hex) == 6 and clean_hex in l_txt):
                        file_matches.append(idx + 1)

                if len(file_matches) == 1:
                    occurrences.append({"line": file_matches[0], "property": ""})
                elif len(file_matches) > 1:
                    _rollback_ds()
                    return {
                        "success": False,
                        "error": f"AMBIGUOUS_SPLIT_MATCH: وُجد أكثر من سطر ({file_matches}) في {file_path.name} يحتوي على '{original_token or original_value}'. يرجى التحديد اليدوي لتجنب التعديل العشوائي.",
                    }
                else:
                    _rollback_ds()
                    return {
                        "success": False,
                        "error": f"لم يتم العثور على أي سطر يحتوي على اللون '{original_token or original_value}' في كامل ملف {file_path.name}.",
                    }

        sorted_occs = sorted(occurrences, key=lambda x: x.get("line", 0), reverse=True)
        
        for occ in sorted_occs:
            occ_line = occ.get("line", 0)
            if occ_line < 1 or occ_line > len(lines):
                continue
            
            target_idx = occ_line - 1
            cand_patterns = _normalize_color_literals(original_token or original_value)
            if original_token:
                cand_patterns.extend([original_token, f"AppColors.{original_token}"])
            if original_value:
                cand_patterns.extend(_normalize_color_literals(original_value))
            seen_c = set()
            clean_cands = []
            for c in sorted(cand_patterns, key=len, reverse=True):
                if c and c not in seen_c:
                    seen_c.add(c)
                    clean_cands.append(c)

            matched_cand = None
            for c in clean_cands:
                if c in lines[target_idx]:
                    matched_cand = c
                    break

            if not matched_cand:
                for off in [1, -1, 2, -2, 3, -3, 4, -4, 5, -5]:
                    c_idx = target_idx + off
                    if 0 <= c_idx < len(lines):
                        for c in clean_cands:
                            if c in lines[c_idx]:
                                matched_cand = c
                                target_idx = c_idx
                                occ_line = c_idx + 1
                                break
                    if matched_cand:
                        break

            old_line = lines[target_idx]
            new_line = old_line
            if matched_cand:
                new_line = old_line.replace(matched_cand, f"AppColors.{final_token_name}")
            
            if new_line != old_line:
                lines[target_idx] = new_line
                ste_changes.append((occ_line, old_line, new_line))
                replaced += 1
        
        if replaced == 0:
            _rollback_ds()
            return {"success": False, "error": f"لم يتم العثور على أي سطر مطابق لاستبداله في {file_path.name}."}

        updated_content = "\n".join(lines)
        updated_content, was_added, import_line, target_import = _ensure_app_colors_import(file_path, updated_content, app_colors_pkg)
        if was_added:
            journal.record_change(
                file=str(file_path.relative_to(project_root)),
                line=import_line,
                old_line="",
                new_line=target_import,
                pattern_id=f"split_import_{final_token_name}",
            )
        ok_edit, msg_edit, _ = apply_safe_edit(
            file_path=file_path,
            new_content=updated_content + "\n",
            original_content=content,
            pattern_id=f"split_token_{final_token_name}",
            project_root=project_root,
        )
        if not ok_edit:
            _rollback_ds()
            return {"success": False, "error": msg_edit}
        for occ_l, old_l, new_l in ste_changes:
            journal.record_change(
                file=str(file_path.relative_to(project_root)),
                line=occ_l,
                old_line=old_l,
                new_line=new_l,
                pattern_id=f"split_token_{final_token_name}",
            )
        
        modified = journal.list_modified_files()

        return {
            "success": True,
            "new_token": final_token_name,
            "new_token_name": final_token_name,
            "token_name": final_token_name,
            "new_value": new_value.lstrip("#"),
            "value": new_value,
            "value_expr": new_expr,
            "replaced_count": replaced,
            "file": str(file_path.relative_to(project_root)),
            "element_file": element_file,
            "already_exists": token_already_exists,
            "journal_files": [f["file"] for f in modified],
        }
    
    except Exception as e:
        _rollback_ds()
        return {"success": False, "error": str(e)}

