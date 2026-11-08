from common.file_ops import assert_inside
# -*- coding: utf-8 -*-
"""
Dimension edit application handler.
Applies surgical regex/AST edits to dimension references in Kotlin sources.
"""

import re
from pathlib import Path
from typing import Optional, List, Dict, Tuple

from core.pattern_journal import PatternJournal
from core.project_root import get_project_root
from analysis.dimension_analyzer import _normalize_dim_value
from handlers.kotlin_validation import (
    _validate_kotlin_line, _clean_duplicate_modifier, _validate_modified_file
)
from network.ws_server import log_info, log_error

def _apply_dimension_edit(edit: dict, project_root: Path) -> dict:
    """Apply dimension edit at specific line with PatternJournal recording."""
    file_rel = edit.get("file")
    line = edit.get("line", 0)
    property_name = edit.get("property", "")
    old_value = str(edit.get("old_value", "")).replace(".dp", "").replace(".sp", "").strip()
    new_value = str(edit.get("new_value", "")).replace(".dp", "").replace(".sp", "").strip()
    
    # ⭐ Step 3: دعم التوكنات (Tokens) وتوجيهها تلقائياً إلى DesignSystem.kt
    token_candidate = None
    for cand in [edit.get("token_name"), edit.get("old_value"), edit.get("property"), edit.get("dimension_token")]:
        cand_str = str(cand or "").strip()
        if cand_str and any(p in cand_str for p in ("AppDesign.", "DesignSystem.", "AppDimens.", "Dimensions.")):
            token_candidate = cand_str
            break
        if cand_str and (cand_str.startswith("CardCorner") or cand_str.startswith("cardCorner")):
            token_candidate = cand_str
            break

    if token_candidate:
        from editor.dimension_editor import update_token_default
        success, msg, extra = update_token_default(token_candidate, new_value)
        clean_tok = token_candidate.split(".")[-1]
        if success:
            return {
                "success": True,
                "token_name": clean_tok,
                "registry_key": clean_tok,
                "file": "DesignSystem.kt",
                "message": msg,
            }
        else:
            return {"success": False, "error": msg or f"Failed to update dimension token {token_candidate}"}

    if not file_rel or line < 1:
        return {"success": False, "error": "Invalid file/line"}
    
    file_path = assert_inside(project_root, project_root / file_rel)
    if not file_path.exists():
        matches = [m for m in project_root.rglob(Path(file_rel).name) 
                   if "build" not in str(m) and ".composebridge" not in str(m) 
                   and "backup" not in str(m).lower()]
        if not matches:
            return {"success": False, "error": f"File not found: {file_rel}"}
        file_path = matches[0]
    
    try:
        content = file_path.read_text(encoding="utf-8")
        lines = content.splitlines()
        
        if line > len(lines):
            return {"success": False, "error": f"Line {line} out of range"}
        
        target_idx = line - 1
        
        try:
            num = float(new_value)
            new_val_str = str(int(num)) if num.is_integer() else str(num)
        except Exception:
            new_val_str = new_value

        from core.source_analyzer import _normalize_dim_value
        old_val_variants = _normalize_dim_value(old_value)
        is_ternary = edit.get("is_ternary", False)
        registry_key = str(edit.get("registry_key", "")).strip()
        kind = edit.get("dimension_kind", "dp")

        # 0. Check if target_idx itself is a match
        found_idx = -1
        exact_line = lines[target_idx]
        has_val = any(re.search(rf'\b{re.escape(v)}\.(?:dp|sp)\b', exact_line) for v in old_val_variants)
        has_prop = (property_name in exact_line) if property_name else False

        if has_val:
            found_idx = target_idx
        elif has_prop and ("BridgeDimensionRegistry" in exact_line or re.search(r'\d+(?:\.\d+)?\.(?:dp|sp)\b', exact_line)):
            found_idx = target_idx

        # ⭐ Step 2: توسيع نطاق البحث من ±5 أسطر إلى ±15 سطراً
        if found_idx < 0:
            search_range = range(
                max(0, target_idx - 15),
                min(len(lines), target_idx + 16),
            )
            # 1. First: line that matches property and old value
            for i in search_range:
                content_line = lines[i]
                prop_matches = False
                if property_name == "padding_horizontal" and "horizontal" in content_line:
                    prop_matches = True
                elif property_name == "padding_vertical" and "vertical" in content_line:
                    prop_matches = True
                elif property_name and property_name in content_line:
                    prop_matches = True
                elif property_name and ("corner" in property_name.lower() or "shape" in property_name.lower()) and ("shape" in content_line or "Corner" in content_line):
                    prop_matches = True
                
                if prop_matches:
                    for variant in old_val_variants:
                        if re.search(rf'\b{re.escape(variant)}\.(?:dp|sp)\b', content_line):
                            found_idx = i
                            break
                    if found_idx >= 0:
                        break

            # 2. Second: line that matches property and BridgeDimensionRegistry
            if found_idx < 0:
                for i in search_range:
                    content_line = lines[i]
                    prop_matches = (
                        (property_name and property_name in content_line)
                        or (property_name == "padding_horizontal" and "horizontal" in content_line)
                        or (property_name == "padding_vertical" and "vertical" in content_line)
                        or (property_name and ("corner" in property_name.lower() or "shape" in property_name.lower()) and ("shape" in content_line or "Corner" in content_line))
                    )
                    if prop_matches and "BridgeDimensionRegistry" in content_line:
                        found_idx = i
                        break

            # 3. Third: line that matches old value with dp/sp
            if found_idx < 0:
                for i in search_range:
                    content_line = lines[i]
                    for variant in old_val_variants:
                        if re.search(rf'\b{re.escape(variant)}\.(?:dp|sp)\b', content_line):
                            found_idx = i
                            break
                    if found_idx >= 0:
                        break

            # 4. Fourth: line that matches property name even if ternary if/else or token
            if found_idx < 0 and property_name:
                for i in search_range:
                    content_line = lines[i]
                    prop_matches = (
                        (property_name in content_line)
                        or (property_name == "padding_horizontal" and "horizontal" in content_line)
                        or (property_name == "padding_vertical" and "vertical" in content_line)
                        or (("corner" in property_name.lower() or "shape" in property_name.lower()) and ("shape" in content_line or "Corner" in content_line))
                    )
                    if prop_matches and (re.search(r'\d+(?:\.\d+)?\.(?:dp|sp)\b', content_line) or "BridgeDimensionRegistry" in content_line or "AppDesign." in content_line):
                        found_idx = i
                        break

        if found_idx < 0:
            return {
                "success": False,
                "error": f"Could not find line for {property_name}={old_value}",
                "search_range": f"{target_idx-15}..{target_idx+15}",
            }

        target_idx = found_idx
        actual_line = target_idx + 1
        old_line = lines[target_idx]
        new_line = old_line

        # التحقق مما إذا كان السطر يشير إلى توكن تصميم (مثل AppDesign.CardCornerLarge)
        token_line_m = re.search(r'\b(?:AppDesign|DesignSystem|AppDimens)\.([A-Za-z0-9_]+)\b', old_line)
        if token_line_m:
            ref_tok = token_line_m.group(1)
            from editor.dimension_editor import update_token_default
            tok_ok, tok_msg, _ = update_token_default(ref_tok, new_value)
            if tok_ok:
                return {
                    "success": True, "token_name": ref_tok, "registry_key": ref_tok,
                    "file": "DesignSystem.kt", "message": tok_msg,
                }

        # ⭐ مسار فصل الأبعاد (Dimension Split - For This Element): إنشاء توكن جديد في DesignSystem.kt
        is_split = (edit.get("edit_mode") == "split") or edit.get("is_split", False)
        if is_split:
            client_key = str(edit.get("registry_key") or edit.get("dimension_token") or "").strip()
            clean_p = (property_name or "dim").replace("padding_", "").replace(".", "_")
            token_base = re.sub(r'[^A-Za-z0-9_]', '_', client_key or f"{clean_p}_{actual_line}")
            if token_base and token_base[0].isdigit():
                token_base = "dim_" + token_base
            registry_key = token_base
            try:
                from core.token_migrator import TokenMigrator
                migrator = TokenMigrator(project_root)
                ds = migrator._find_design_system()
                if ds and ds.exists():
                    ds_content = ds.read_text(encoding="utf-8")
                    if f"val {token_base}" not in ds_content:
                        obj_m = re.search(r'object\s+(?:AppDesign|AppDimens|Dimensions|AppColors)\s*\{', ds_content)
                        if obj_m:
                            ins_pos = obj_m.end()
                            token_decl = f'\n    val {token_base}: Dp get() = BridgeDimensionRegistry.get("{token_base}", {new_val_str}.{kind})'
                            new_ds = ds_content[:ins_pos] + token_decl + ds_content[ins_pos:]
                            if "import com.composebridge.agent.BridgeDimensionRegistry" not in new_ds:
                                pkg_m = re.search(r'^(package\s+[^\n]+)', new_ds, re.MULTILINE)
                                if pkg_m:
                                    new_ds = new_ds.replace(pkg_m.group(1), f'{pkg_m.group(1)}\n\nimport com.composebridge.agent.BridgeDimensionRegistry', 1)
                            ds.write_text(new_ds, encoding="utf-8")
                            log_info(f"[DIMENSION-SPLIT] Created token '{token_base}' in {ds.name}")
            except Exception as e:
                log_error(f"[DIMENSION-SPLIT] Token creation warning: {e}")

        replaced = False

        # 1. إذا كان السطر مغلفاً مسبقاً بـ BridgeDimensionRegistry (سواء بالمفتاح المحدد أو من Edit All)، حدّث المفتاح والقيمة
        if "BridgeDimensionRegistry.get(" in old_line:
            new_line = re.sub(
                r'BridgeDimensionRegistry\.get\(\s*"[^"]+"\s*,\s*[^)]+\)',
                f'BridgeDimensionRegistry.get("{registry_key}", {new_val_str}.{kind})',
                old_line,
                count=1,
            )
            replaced = new_line != old_line

        # 1c. Ternary if/else support when unwrapped: e.g. if (isDesktop) 72.dp else 140.dp
        if not replaced and re.search(r'\bif\s*\(.*?\)\s*.*?else\s+\d+(?:\.\d+)?\.(?:dp|sp)\b', old_line):
            repl_val = (
                f'BridgeDimensionRegistry.get("{registry_key}", {new_val_str}.{kind})'
                if registry_key
                else f'{new_val_str}.{kind}'
            )
            new_line = re.sub(
                r'(\belse\s+)(\d+(?:\.\d+)?\.(?:dp|sp)\b)',
                rf'\g<1>{repl_val}',
                old_line,
                count=1,
            )
            replaced = new_line != old_line

        # 2. Precise regex matching for property named parameter (e.g. padding_vertical, vertical, padding_horizontal, horizontal, top, etc.)
        if not replaced and property_name:
            prop_kws = [property_name]
            if property_name.startswith("padding_"):
                prop_kws.append(property_name.replace("padding_", ""))
            for kw in ("vertical", "horizontal", "start", "end", "top", "bottom", "width", "height", "size"):
                if kw in property_name:
                    prop_kws.append(kw)
            if "corner" in property_name.lower():
                prop_kws.extend(["cornerRadius", "corner", "shape"])
            prop_pattern = '|'.join(map(re.escape, sorted(set(prop_kws), key=len, reverse=True)))

            repl_val = (
                f'BridgeDimensionRegistry.get("{registry_key}", {new_val_str}.{kind})'
                if registry_key
                else f'{new_val_str}.{kind}'
            )

            # Wrap ternary or direct named parameter: e.g. horizontal = if (...) 16.dp else 8.dp or vertical = 8.dp
            named_m = re.search(rf'(\b(?:{prop_pattern})\s*=\s*(?:if\s*\(.*?\)\s*.*?else\s+)?)(\d+(?:\.\d+)?\.(?:dp|sp)\b)', old_line)
            if named_m:
                new_line = re.sub(
                    rf'(\b(?:{prop_pattern})\s*=\s*(?:if\s*\(.*?\)\s*.*?else\s+)?)(\d+(?:\.\d+)?\.(?:dp|sp)\b)',
                    rf'\g<1>{repl_val}',
                    old_line,
                    count=1,
                )
                replaced = new_line != old_line

        # 3. Fallback: try replacing matching old value variant
        if not replaced:
            for variant in old_val_variants:
                pattern = rf'(\b{re.escape(variant)})(\.(?:dp|sp)\b)'
                if re.search(pattern, old_line):
                    repl_str = (
                        f'BridgeDimensionRegistry.get("{registry_key}", {new_val_str}.{kind})'
                        if registry_key
                        else f'{new_val_str}.{kind}'
                    )
                    new_line = re.sub(pattern, repl_str, old_line, count=1)
                    replaced = True
                    break

        # ⭐ المستوى الثاني (Level 2): التطابق الدلالي لاسم الخاصية (Semantic Property Matching)
        if not replaced and property_name:
            prop_kws = [property_name]
            if property_name.startswith("padding_"):
                prop_kws.append(property_name.replace("padding_", ""))
            if "vertical" in property_name:
                prop_kws.extend(["vertical", "padding_vertical"])
            if "horizontal" in property_name:
                prop_kws.extend(["horizontal", "padding_horizontal"])
            if "corner" in property_name.lower():
                prop_kws.extend(["cornerRadius", "corner", "shape"])
            for kw in ("start", "end", "top", "bottom", "width", "height", "size"):
                if kw in property_name:
                    prop_kws.append(kw)
            prop_pattern = '|'.join(map(re.escape, sorted(set(prop_kws), key=len, reverse=True)))

            repl_val = (
                f'BridgeDimensionRegistry.get("{registry_key}", {new_val_str}.{kind})'
                if registry_key
                else f'{new_val_str}.{kind}'
            )

            named_matches = list(re.finditer(rf'\b(?:{prop_pattern})\s*=\s*', old_line))
            func_matches = list(re.finditer(rf'\.(?:{prop_pattern})\s*\(', old_line))
            total_prop_matches = len(named_matches) + len(func_matches)

            # إذا وُجدت الخاصية مرة واحدة فقط في السطر، استبدل قيمتها
            if total_prop_matches == 1:
                if len(named_matches) == 1:
                    if "else " in old_line:
                        new_line = re.sub(
                            rf'(\b(?:{prop_pattern})\s*=\s*.*?else\s+)([A-Za-z0-9_.]+(?:\([^)]*\))?)',
                            rf'\g<1>{repl_val}',
                            old_line,
                            count=1
                        )
                    else:
                        new_line = re.sub(
                            rf'(\b(?:{prop_pattern})\s*=\s*)([A-Za-z0-9_.]+(?:\([^)]*\))?)',
                            rf'\g<1>{repl_val}',
                            old_line,
                            count=1
                        )
                    replaced = (new_line != old_line)
                elif len(func_matches) == 1:
                    if "else " in old_line:
                        new_line = re.sub(
                            rf'(\.(?:{prop_pattern})\s*\(\s*.*?else\s+)([A-Za-z0-9_.]+(?:\([^)]*\))?)',
                            rf'\g<1>{repl_val}',
                            old_line,
                            count=1
                        )
                    else:
                        new_line = re.sub(
                            rf'(\.(?:{prop_pattern})\s*\(\s*)([A-Za-z0-9_.]+(?:\([^)]*\))?)',
                            rf'\g<1>{repl_val}',
                            old_line,
                            count=1
                        )
                    replaced = (new_line != old_line)
            elif total_prop_matches > 1:
                # ⭐ حارس الأمان (المستوى الثالث): إذا وُجدت الخاصية أكثر من مرة، أرجع خطأ
                return {
                    "success": False,
                    "error": f"Could not find unique line for property={property_name} (found {total_prop_matches} matches in line {actual_line}). Ambiguity guard triggered.",
                    "line_content": old_line.strip(),
                }

        # 4. Fallback: search full pattern prefix
        if not replaced:
            pattern = rf'(\.\w+\s*\(\s*|\b\w+\s*=\s*)(\d+(?:\.\d+)?\.(?:dp|sp))(\s*\)?)'
            def _repl(m):
                prefix = m.group(1)
                suffix = m.group(3)
                val = (
                    f'BridgeDimensionRegistry.get("{registry_key}", {new_val_str}.{kind})'
                    if registry_key
                    else f'{new_val_str}.{kind}'
                )
                return f'{prefix}{val}{suffix}'

            new_line = re.sub(pattern, _repl, old_line, count=1)
            replaced = new_line != old_line

        if not replaced:
            return {
                "success": False,
                "error": f"Could not replace in line {line}",
                "line_content": old_line.strip(),
            }

        # Clean duplicate modifier in line
        new_line = _clean_duplicate_modifier(new_line)

        # ⭐ 5. Validate Kotlin line before writing
        is_valid, error = _validate_kotlin_line(new_line)
        if not is_valid:
            return {
                "success": False,
                "error": f"Invalid Kotlin: {error}",
                "new_line": new_line,
            }

        # Clean up any leftover duplicate modifier line if previous line is duplicated
        if target_idx > 0 and "modifier =" in lines[target_idx - 1] and "modifier =" in new_line:
            prev_line = lines[target_idx - 1].strip()
            if "BridgeDimensionRegistry" not in prev_line and "modifier = Modifier.size" in prev_line:
                lines.pop(target_idx - 1)
                target_idx -= 1
                actual_line = target_idx + 1

        # Create safe backup before writing modifications
        from core.ast_finder import safe_create_backup, validate_kotlin_syntax
        is_val_pre, err_pre = validate_kotlin_syntax(file_path)
        if not is_val_pre:
            return {"success": False, "error": f"الملف مكسور مسبقاً قبل التعديل: {err_pre}"}

        ok_bak, err_bak, _ = safe_create_backup(file_path, project_root=project_root)
        if not ok_bak:
            return {"success": False, "error": f"فشل إنشاء النسخة الاحتياطية للملف: {err_bak}"}

        lines[target_idx] = new_line

        # ⭐ الخطوة 1: حقن الاستيراد في الذاكرة قبل التحقق والحفظ لمنع الـ Deadlock
        was_added = False
        import_line = -1
        import_stmt = "import com.composebridge.agent.BridgeDimensionRegistry"
        has_registry = any("BridgeDimensionRegistry" in l for l in lines)
        has_import = any(import_stmt in l for l in lines)

        if has_registry and not has_import:
            last_import_idx = -1
            package_idx = -1
            for i, l in enumerate(lines):
                stripped = l.strip()
                if stripped.startswith("package "):
                    package_idx = i
                elif stripped.startswith("import "):
                    last_import_idx = i

            if last_import_idx >= 0:
                insert_idx = last_import_idx + 1
                lines.insert(insert_idx, import_stmt)
                import_line = insert_idx + 1
                was_added = True
            elif package_idx >= 0:
                lines.insert(package_idx + 1, "")
                lines.insert(package_idx + 2, import_stmt)
                import_line = package_idx + 3
                was_added = True
            else:
                lines.insert(0, import_stmt)
                import_line = 1
                was_added = True

            if was_added and (import_line - 1) <= target_idx:
                target_idx += (2 if package_idx >= 0 and last_import_idx < 0 else 1)
                actual_line = target_idx + 1

        updated_content = "\n".join(lines) + "\n"

        from common.file_ops import apply_safe_edit
        ok_edit, msg_edit, _ = apply_safe_edit(
            file_path=file_path,
            new_content=updated_content,
            original_content=content,
            pattern_id=f"dimension_{property_name}_{actual_line}",
            project_root=project_root,
        )
        if not ok_edit:
            return {"success": False, "error": msg_edit}

        journal = PatternJournal(project_root)
        try:
            rel_file = str(file_path.relative_to(project_root)).replace("\\", "/")
        except Exception:
            rel_file = file_rel

        journal.record_change(
            file=rel_file,
            line=actual_line,
            old_line=old_line,
            new_line=new_line,
            pattern_id=f"dimension_{property_name}_{actual_line}",
        )

        if was_added:
            journal.record_change(file=rel_file, line=import_line, old_line="", new_line=import_stmt, pattern_id=f"import_bridge_dim_{registry_key}")
            log_info(f"[DIMENSION] Added BridgeDimensionRegistry import to {file_path.name}")

        final_content = file_path.read_text(encoding="utf-8")
        is_file_valid, file_err = _validate_modified_file(file_path, final_content)
        if not is_file_valid:
            log_error(f"[DIMENSION] File validation warning: {file_err}")

        return {
            "success": True, "file": rel_file, "line": actual_line,
            "old": old_value, "new": new_val_str, "property": property_name,
            "registry_key": registry_key, "wrapped_in_registry": bool(registry_key),
        }
    except Exception as e:
        import traceback
        return {"success": False, "error": str(e), "traceback": traceback.format_exc()[:500]}

def apply_multiple_dimension_edits(edits: List[dict], project_root: Path) -> List[dict]:
    """Applies multiple dimension edits in reverse line order to prevent offset drift."""
    edits_sorted = sorted(edits, key=lambda e: int(e.get("line") or 0), reverse=True)
    return [_apply_dimension_edit(e, project_root) for e in edits_sorted]

