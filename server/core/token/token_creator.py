# -*- coding: utf-8 -*-
"""
وحدة إنشاء وحذف وإعادة استخدام التوكنز (Token Creator & Registry Manager).
Handles creating color tokens, removing tokens from registry files,
and smart token reuse when existing tokens match the target value.
"""

import re
import shutil
from pathlib import Path
from typing import Dict, Optional, Tuple

from common.file_ops import _atomic_write_text, assert_inside, is_ignored_path
from core.write_lock import acquire_write_lock


class TokenCreatorMixin:
    """Mixin class providing token creation, removal, and smart reuse logic for TokenMigrator."""

    def create_color_token(
        self,
        token_name: str,
        value_expr: str,
        original_value: str = None,
    ) -> Dict:
        """
        Create a color token safely.
        - Normalize hex → Color(0xAARRGGBB)
        - Ensure single set of parentheses
        - Validate token_name (Kotlin identifier)
        """
        try:
            with acquire_write_lock():
                if not re.match(r'^[A-Za-z][A-Za-z0-9_]*$', token_name):
                    return {
                        "success": False,
                        "error": f"Invalid token name: '{token_name}' (must be a valid Kotlin identifier)",
                    }

                def _normalize(val: str) -> str:
                    if not val:
                        return "Color.Transparent"
                    val = val.strip()
                    if val.startswith("Color."):
                        return val
                    if val.startswith("Color("):
                        match = re.search(r'0x([0-9A-Fa-f]+)', val)
                        if not match:
                            return "Color.Transparent"
                        hex_str = match.group(1).upper()
                    elif val.startswith("0x") or val.startswith("0X"):
                        hex_str = val[2:].upper()
                    elif val.startswith("#"):
                        hex_str = val[1:].upper()
                    else:
                        hex_str = val.upper()

                    if len(hex_str) == 6:
                        hex_str = "FF" + hex_str
                    elif len(hex_str) == 3:
                        hex_str = "FF" + "".join(c * 2 for c in hex_str)
                    elif len(hex_str) != 8:
                        return "Color.Transparent"

                    return f"Color(0x{hex_str})"

                default_expr = _normalize(original_value) if original_value else _normalize(value_expr)
                override_expr = _normalize(value_expr)

                design_system = self._find_design_system()
                if not design_system or not design_system.exists():
                    return {
                        "success": False,
                        "error_code": "NEEDS_MANUAL_MAPPING",
                        "error": "NEEDS_MANUAL_MAPPING: No design system file found. Use set_design_file_mapping to configure it.",
                    }

                content = design_system.read_text(encoding="utf-8")

                if re.search(rf'\bval\s+{re.escape(token_name)}\b', content):
                    return {
                        "success": True,
                        "already_existed": True,
                        "token_name": token_name,
                        "default_value": default_expr,
                        "override_value": override_expr,
                        "file": str(design_system.relative_to(self.project_root)),
                    }

                match = re.search(r'object\s+AppColors\s*\{', content)
                if not match:
                    return {
                        "success": False,
                        "error_code": "NEEDS_MANUAL_MAPPING",
                        "error": "NEEDS_MANUAL_MAPPING: 'object AppColors' not found in design file. Configure an appropriate registry file using set_design_file_mapping.",
                    }

                token_line = (
                    f'    val {token_name}: Color get() = '
                    f'BridgeThemeRegistry.getColor("{token_name}", {default_expr})'
                )

                is_valid, error = self._validate_kotlin_syntax(token_line)
                if not is_valid:
                    return {
                        "success": False,
                        "error": f"Generated invalid Kotlin: {error}",
                        "generated_line": token_line,
                    }

                insert_pos = match.end()
                newline_pos = content.find('\n', insert_pos)
                if newline_pos < 0:
                    return {"success": False, "error": "Invalid DesignSystem structure"}

                new_content = (
                    content[:newline_pos + 1]
                    + token_line + '\n'
                    + content[newline_pos + 1:]
                )
                new_content = self._ensure_registry_imports(new_content, "color", "registry")

                if ')))' in new_content:
                    return {
                        "success": False,
                        "error": "Generated content has malformed parens",
                        "generated_line": token_line,
                    }

                try:
                    from core.ast_finder import safe_create_backup
                    safe_create_backup(design_system, project_root=self.project_root)
                except Exception:
                    pass

                try:
                    from core.pattern_journal import PatternJournal
                    journal = PatternJournal(self.project_root)
                    journal.record_change(
                        file=str(design_system.relative_to(self.project_root)),
                        line=content[:newline_pos + 1].count('\n') + 1,
                        old_line="",
                        new_line=token_line,
                        pattern_id=f"create_token_{token_name}",
                    )
                except Exception as e:
                    import logging
                    logging.error(f"[TOKEN_MIGRATOR] Journal record failed: {e}")

                _atomic_write_text(design_system, new_content)

                return {
                    "success": True,
                    "token_name": token_name,
                    "default_value": default_expr,
                    "override_value": override_expr,
                    "token_line": token_line,
                    "file": str(design_system.relative_to(self.project_root)),
                }

        except Exception as e:
            import traceback
            return {
                "success": False,
                "error": str(e),
                "traceback": traceback.format_exc(),
            }

    @staticmethod
    def _validate_kotlin_syntax(token_line: str) -> Tuple[bool, str]:
        """Basic sanity check for Kotlin token declaration syntax."""
        pattern = r'^\s*val\s+[A-Za-z_][A-Za-z0-9_]*\s*:\s*Color\s+get\(\)\s*=\s*.+$'
        if not re.match(pattern, token_line):
            return False, f"Doesn't match 'val X: Color get() = ...' pattern"
        if token_line.count("(") != token_line.count(")"):
            return False, "Unbalanced parentheses"
        if ")))" in token_line:
            return False, "Triple parentheses detected"
        if not token_line.rstrip().endswith(")"):
            return False, "Line must end with )"
        return True, ""

    def _backup_file(self, file_path: Path) -> Path:
        """Create a backup of the file using a single fixed .bridge.bak after syntax validation."""
        from core.ast_finder import safe_create_backup
        ok, err_msg, backup = safe_create_backup(file_path)
        if not ok:
            raise ValueError(err_msg)
        return backup

    def _is_safe_to_replace(self, line_content: str, value: str) -> Tuple[bool, str]:
        """Check if line is safe to replace."""
        if "//" in line_content:
            comment_idx = line_content.index("//")
            value_idx = line_content.find(value)
            if value_idx != -1 and value_idx > comment_idx:
                return (False, f"Value '{value}' is in a comment")
        
        if "/*" in line_content and "*/" in line_content:
            comment_start = line_content.index("/*")
            comment_end = line_content.index("*/")
            value_idx = line_content.find(value)
            if comment_start < value_idx < comment_end:
                return (False, f"Value '{value}' is in a comment")

        strings = re.findall(r'"[^"]*"', line_content)
        for s in strings:
            if value in s:
                return (False, f"Value '{value}' is inside a string")
        
        if re.search(rf':\s*[\w<>?\.]+\s*=\s*{re.escape(value)}\b', line_content):
            return (False, f"Value '{value}' appears to be a parameter default")
        
        return (True, "OK")

    def _extract_package(self, content: str) -> str:
        """Extract package name from Kotlin source."""
        match = re.search(r'package\s+([\w.]+)', content)
        return match.group(1) if match else ""

    def remove_token(self, token_name: str, token_type: str = "color") -> bool:
        """Remove a token definition from the registry file."""
        with acquire_write_lock():
            files = self.find_all_token_files(token_type)
            for f in files:
                try:
                    content = f.read_text(encoding="utf-8")
                    patterns = [
                        rf'\n?[ \t]*val\s+{re.escape(token_name)}\s*:\s*Color\s*\n[ \t]*get\(\)\s*=\s*BridgeThemeRegistry\.getColor\("[^"]+",\s*[^)]+\)',
                        rf'\n?[ \t]*val\s+{re.escape(token_name)}\s*:\s*Dp\s*\n[ \t]*get\(\)\s*=\s*BridgeDimensionRegistry\.get\("[^"]+",\s*[^)]+\)',
                        rf'\n?[ \t]*val\s+{re.escape(token_name)}\s*:\s*Color\s+get\(\)\s*=\s*[^\n]+',
                        rf'\n?[ \t]*val\s+{re.escape(token_name)}\s*:\s*Dp\s+get\(\)\s*=\s*[^\n]+',
                        rf'\n?[ \t]*val\s+{re.escape(token_name)}\s*=\s*Color\([^)]+\)',
                        rf'\n?[ \t]*val\s+{re.escape(token_name)}\s*=\s*[\d.]+\.(?:dp|sp)',
                        rf'\n?[ \t]*val\s+{re.escape(token_name)}\b.*?(?:\n[ \t]*)?get\(\)\s*=\s*[^\n]+',
                    ]
                    new_content = content
                    for pattern in patterns:
                        new_content = re.sub(pattern, "", new_content)

                    if new_content != content:
                        try:
                            from core.pattern_journal import PatternJournal
                            journal = PatternJournal(self.project_root)
                            rel_file = str(f.relative_to(self.project_root)) if f.is_relative_to(self.project_root) else f.name
                            journal.record_change(
                                file=rel_file,
                                line=1,
                                old_line=content,
                                new_line=new_content,
                                pattern_id=f"remove_token_{token_name}",
                            )
                        except Exception as jerr:
                            import logging
                            logging.error(f"[TOKEN_MIGRATOR] Journal record failed in remove_token: {jerr}")

                        _atomic_write_text(f, new_content)
                        return True
                except Exception:
                    continue
            return False

    def _handle_token_reuse(
        self,
        file_name: str,
        line: int,
        value: str,
        token_name: str,
        token_type: str,
        token_info: Optional[Dict],
        existing_value: Optional[str],
    ) -> Tuple[bool, str, Optional[dict]]:
        """Handles reusing an existing token in target file when the value matches exactly."""
        target_files = [f for f in self.project_root.rglob(Path(file_name).name)
                      if not is_ignored_path(f, self.project_root)]
        if not target_files:
            return (False, f"File not found: {file_name}", None)
        target_file = target_files[0]
        target_file = assert_inside(self.project_root, target_file)

        try:
            content = target_file.read_text(encoding="utf-8")
        except Exception as e:
            return (False, f"Cannot read file: {e}", None)

        lines = content.splitlines()
        if line < 1 or line > len(lines):
            return (False, f"Line {line} out of range (file has {len(lines)} lines)", None)

        target_line = lines[line - 1]
        if value not in target_line:
            return (False, f"Line {line} does not contain '{value}'", {
                "line_content": target_line.strip(),
            })

        is_safe, reason = self._is_safe_to_replace(target_line, value)
        if not is_safe:
            return (False, f"Cannot replace: {reason}", {
                "line_content": target_line.strip(),
                "reason": reason,
            })

        target_backup = self._backup_file(target_file)
        try:
            from core.ast_finder import safe_create_backup
            safe_create_backup(target_file, project_root=self.project_root)
        except Exception:
            pass
        try:
            reg_file = token_info["file"] if token_info else None
            reg_content = token_info["content"] if token_info else (reg_file.read_text(encoding="utf-8") if reg_file else "")
            reg_pkg = self._extract_package(reg_content)
            target_pkg = self._extract_package(content)

            obj_name = token_info.get("object_name") if token_info else (reg_file.stem if reg_file else None)
            if obj_name:
                scoped_name = f"{obj_name}.{token_name}"
                if reg_pkg and reg_pkg != target_pkg:
                    new_import = f"import {reg_pkg}.{obj_name}"
                else:
                    new_import = None
            else:
                scoped_name = token_name
                if reg_pkg and reg_pkg != target_pkg:
                    new_import = f"import {reg_pkg}.{token_name}"
                else:
                    new_import = None

            old_line = lines[line - 1]
            if token_type == "dimension":
                pattern = re.compile(rf'\b{re.escape(value)}\b')
            else:
                if f"Color({value})" in old_line:
                    pattern = re.compile(rf'Color\(\s*{re.escape(value)}\s*\)')
                else:
                    pattern = re.compile(rf'(?<![\w]){re.escape(value)}(?![\w])')

            new_line = pattern.sub(scoped_name, old_line, count=1)
            if new_line == old_line:
                shutil.copy2(target_backup, target_file)
                return (False, f"Replacement failed: '{value}' not found as standalone", None)

            lines[line - 1] = new_line
            target_line_ending = "\r\n" if "\r\n" in content else "\n"
            updated_target_content = target_line_ending.join(lines) + target_line_ending
            if new_import and self._needs_import(updated_target_content, new_import):
                updated_target_content = self._add_import(updated_target_content, new_import)

            _atomic_write_text(target_file, updated_target_content)
            try:
                from core.pattern_journal import PatternJournal
                pj = PatternJournal(self.project_root)
                rel_target = str(target_file.relative_to(self.project_root)).replace("\\", "/")
                if new_import and self._needs_import(content, new_import):
                    pj.record_change(file=rel_target, line=1, old_line="", new_line=new_import.strip(), pattern_id=f"reuse_token_{token_name}")
                pj.record_change(file=rel_target, line=line, old_line=old_line.strip(), new_line=new_line.strip(), pattern_id=f"reuse_token_{token_name}")
            except Exception as e:
                import sys
                sys.stderr.write(f"[TOKEN_MIGRATOR] Journal record failed on reuse: {e}\n")
            return (
                True,
                f"Reused existing token '{token_name}' in {file_name}:{line}",
                {
                    "action": "reuse_existing",
                    "token_name": token_name,
                    "replaced_in": f"{file_name}:{line}",
                    "reused": True,
                    "old_line": old_line.strip(),
                    "new_line": new_line.strip(),
                }
            )
        except Exception as e:
            shutil.copy2(target_backup, target_file)
            return (False, f"Target edit failed: {e}", None)
