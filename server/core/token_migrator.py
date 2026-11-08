# -*- coding: utf-8 -*-
"""
منسق ترحيل القيم إلى توكنز (Token Migrator Coordinator) لخادم ComposeBridge.
Coordinates replacing hardcoded values with design tokens, creating tokens, and modifying AST.
"""

import re
import shutil
from pathlib import Path
from typing import List, Dict, Optional, Tuple

try:
    from common.file_ops import _atomic_write_text, _find_registry_file, is_ignored_path, assert_inside, _rank
except ImportError:
    try:
        from server.common.file_ops import _atomic_write_text, _find_registry_file, is_ignored_path, assert_inside, _rank
    except ImportError:
        from ..common.file_ops import _atomic_write_text, _find_registry_file, is_ignored_path, assert_inside, _rank

from .write_lock import acquire_write_lock
from core.token.naming_advisor import NamingAdvisorMixin, CONTEXT_RULES, COLOR_CONTEXT_RULES
from core.token.import_injector import ImportInjectorMixin
from core.token.token_creator import TokenCreatorMixin


class TokenMigrator(NamingAdvisorMixin, ImportInjectorMixin, TokenCreatorMixin):
    """
    منسق ترحيل القيم المكررة واستبدالها بتوكنز في نظام التصميم.
    Coordinates migrating raw hardcoded values into Design System tokens.
    """

    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.state_dir = project_root / ".composebridge-state"
        self.state_dir.mkdir(exist_ok=True)
        self.backup_dir = self.state_dir / "migration_backups"
        self.backup_dir.mkdir(exist_ok=True)
        from .state_events import EventLog
        self.events = EventLog(self.state_dir / "events.jsonl")
        self.package_name = self.detect_package_name(self.project_root)

    def detect_package_name(self, project_root: Path) -> str:
        """Detect the project's package name."""
        for manifest in project_root.rglob("AndroidManifest.xml"):
            if is_ignored_path(manifest, project_root):
                continue
            try:
                content = manifest.read_text(encoding="utf-8")
                match = re.search(r'package="([^"]+)"', content)
                if match:
                    return match.group(1)
            except Exception:
                pass
        
        for gradle in project_root.rglob("build.gradle.kts"):
            if is_ignored_path(gradle, project_root):
                continue
            try:
                content = gradle.read_text(encoding="utf-8")
                match = re.search(r'namespace\s*=\s*"([^"]+)"', content)
                if match:
                    return match.group(1)
            except Exception:
                pass
        
        for kt in project_root.rglob("*.kt"):
            if is_ignored_path(kt, project_root):
                continue
            try:
                content = kt.read_text(encoding="utf-8")
                match = re.search(r'^\s*package\s+([\w.]+)', content, re.MULTILINE)
                if match:
                    return match.group(1)
            except Exception:
                pass
        
        return "com.example.app"
    
    def _find_registry_file(self, token_type: str) -> Optional[Path]:
        """Find the file where tokens are stored."""
        candidates = {
            "dimension": ["AppDimens.kt", "DesignSystem.kt", "Dimens.kt", "Dimensions.kt", "Theme.kt"],
            "color": ["AppColors.kt", "DesignSystem.kt", "Color.kt", "Colors.kt", "Theme.kt"],
        }

        for name in candidates.get(token_type, []):
            found = _find_registry_file(name, self.project_root)
            if found and not is_ignored_path(found, self.project_root):
                return found
            matches = [f for f in self.project_root.rglob(name) if not is_ignored_path(f, self.project_root)]
            if matches:
                matches.sort(key=lambda p: (_rank(p), len(str(p))))
                return matches[0]

        if token_type == "color":
            pattern = re.compile(r'val\s+\w+\s*[:=].*Color\(')
        else:
            pattern = re.compile(r'val\s+\w+\s*[:=].*\.dp')

        matches = []
        for kt in self.project_root.rglob("*.kt"):
            if is_ignored_path(kt, self.project_root):
                continue
            try:
                content = kt.read_text(encoding="utf-8")
                if pattern.search(content):
                    matches.append(kt)
            except Exception:
                continue
        if matches:
            matches.sort(key=lambda p: (_rank(p), len(str(p))))
            return matches[0]

        return None

    def _find_design_system(self) -> Optional[Path]:
        """Find the DesignSystem.kt, AppColors.kt, or mapped design file in the project."""
        try:
            from analysis.universal_scanner import resolve_target_design_file
            target, _ = resolve_target_design_file("color", self.project_root)
            if target and target.exists():
                return target
        except Exception:
            pass
        return self._find_registry_file("color")

    def find_all_token_files(self, token_type: str) -> List[Path]:
        """Find ALL files that contain tokens of the given type."""
        candidates = []
        for kt in self.project_root.rglob("*.kt"):
            if is_ignored_path(kt, self.project_root):
                continue
            try:
                content = kt.read_text(encoding="utf-8")
            except Exception:
                continue
            
            if token_type == "dimension":
                if re.search(r'val\s+\w+.*[:=].*[\d.]+\.dp', content) or "BridgeDimensionRegistry" in content:
                    candidates.append(kt)
            elif token_type == "color":
                if re.search(r'val\s+\w+.*[:=].*Color\(', content) or "BridgeThemeRegistry" in content:
                    candidates.append(kt)
        
        def sort_key(path):
            name = path.stem.lower()
            score = 0
            if "token" in name: score -= 100
            if "theme" in name: score -= 50
            if "dimens" in name or "colors" in name: score -= 25
            return score
        
        candidates.sort(key=sort_key)
        return candidates

    def _check_token_exists(self, token_name: str) -> bool:
        """Check if token already exists anywhere."""
        for kt in self.project_root.rglob("*.kt"):
            if is_ignored_path(kt, self.project_root):
                continue
            try:
                content = kt.read_text(encoding="utf-8")
            except Exception:
                continue
            if f'"{token_name}"' in content or re.search(rf'\bval\s+{re.escape(token_name)}\b', content):
                return True
        return False

    def migrate(
        self,
        file_name: str,
        line: int,
        value: str,
        token_name: str,
        token_type: str,
        preferred_token_file: Optional[str] = None,
        force_create: bool = False,
    ) -> Tuple[bool, str, Optional[dict]]:
        """Migrate hardcoded value to token."""
        with acquire_write_lock():
            try:
                line = int(line)
            except (ValueError, TypeError):
                return (False, f"Invalid line number: {line}", None)

            # 0. Target File & Syntax Validation
            target_matches = [f for f in self.project_root.rglob(Path(file_name).name)
                              if not is_ignored_path(f, self.project_root)]
            if target_matches:
                from core.ast_finder import validate_kotlin_syntax
                is_valid, err_msg = validate_kotlin_syntax(target_matches[0])
                if not is_valid:
                    return (False, err_msg, None)

            # 1. Validate token_type
            if token_type not in ("dimension", "color"):
                return (False, f"Invalid token_type: {token_type}", None)
            
            if token_type == "dimension":
                if not re.match(r'^\d+(\.\d+)?\.dp$', value):
                    return (False, f"Unsupported dimension value: '{value}'. Only numeric .dp values are supported.", None)

            # 2. Check token_name exists / Smart Token Reuse
            if self._check_token_exists(token_name):
                token_info = self._get_existing_token_info(token_name, token_type)
                existing_value = token_info["value"] if token_info else self._get_existing_token_value(token_name, token_type)
                
                new_value_clean = value.replace(".dp", "").replace(".sp", "").strip()
                new_value_clean = new_value_clean.replace("Color(", "").replace(")", "").strip()
                if new_value_clean.startswith("0x") or new_value_clean.startswith("0X"):
                    new_value_clean = new_value_clean.upper()
                    
                existing_value_clean = (existing_value or "").replace(".dp", "").replace(".sp", "").strip()
                existing_value_clean = existing_value_clean.replace("Color(", "").replace(")", "").strip()
                if existing_value_clean.startswith("0x") or existing_value_clean.startswith("0X"):
                    existing_value_clean = existing_value_clean.upper()

                if existing_value and (existing_value_clean == new_value_clean or existing_value == value):
                    return self._handle_token_reuse(file_name, line, value, token_name, token_type, token_info, existing_value)
                else:
                    return (
                        False,
                        f"Token '{token_name}' exists with different value '{existing_value}'",
                        {
                            "conflict_type": "same_name_different_value",
                            "existing_token": token_name,
                            "existing_value": existing_value,
                            "new_value": value,
                        }
                    )
            
            # 3. Find target file
            target_files = [f for f in self.project_root.rglob(Path(file_name).name) 
                          if not is_ignored_path(f, self.project_root)]
            if not target_files:
                return (False, f"File not found: {file_name}", None)
            
            target_file = target_files[0]
            target_file = assert_inside(self.project_root, target_file)
            
            # 4. Read target file
            try:
                with open(target_file, "r", encoding="utf-8", newline="") as tf_in:
                    content = tf_in.read()
            except Exception as e:
                return (False, f"Cannot read file: {e}", None)
            
            lines = content.splitlines()
            if line < 1 or line > len(lines):
                return (False, f"Line {line} out of range (file has {len(lines)} lines)", None)
            
            # 5. Verify line contains expected value
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
            
            if not force_create:
                is_valid, suggestion, reason = self._validate_token_name(
                    target_line, token_name, token_type=token_type, value=value
                )
                if not is_valid:
                    return (
                        False,
                        f"Token name invalid: {reason}",
                        {
                            "line_content": target_line.strip(),
                            "given_name": token_name,
                            "suggested_name": suggestion,
                            "safe_alternative": suggestion,
                            "recommended_name": suggestion,
                            "validation_type": "semantic",
                        }
                    )
            
            if not force_create:
                conflict = self.detect_semantic_conflict(
                    token_name, token_type, value
                )
                if conflict:
                    info_dict = {
                        "conflict_type": conflict["type"],
                        "existing_token": conflict["existing_token"],
                        "existing_value": conflict["existing_value"],
                        "existing_file": conflict["existing_file"],
                        "new_name": token_name,
                        "new_value": value,
                        "suggestions": conflict["suggestions"],
                    }
                    if "safe_alternative" in conflict:
                        info_dict["safe_alternative"] = conflict["safe_alternative"]
                        info_dict["recommended_name"] = conflict["safe_alternative"]
                    return (
                        False,
                        conflict["message"],
                        info_dict,
                    )

            if not force_create:
                existing_tokens = self._find_existing_tokens(value, token_type)
                if existing_tokens:
                    prefix = "AppDimens" if token_type == "dimension" else "AppColors"
                    if len(existing_tokens) > 1:
                        tokens_str = ", ".join(existing_tokens)
                        return (
                            False,
                            f"Multiple tokens with same value already exist: {tokens_str}. "
                            f"Use {prefix}.{existing_tokens[0]} or {prefix}.{existing_tokens[1]} instead of creating '{token_name}'.",
                            {
                                "existing_tokens": existing_tokens,
                                "value": value,
                                "suggestion": f"Use {prefix}.{existing_tokens[0]}",
                            }
                        )
                    else:
                        existing = existing_tokens[0]
                        return (
                            False,
                            f"Token with same value already exists: '{existing}'. "
                            f"Use {prefix}.{existing} instead of creating '{token_name}'.",
                            {
                                "existing_token": existing,
                                "value": value,
                                "suggestion": f"Use {prefix}.{existing}",
                            }
                        )
            
            # 6. Find registry file
            if preferred_token_file:
                matches = [m for m in self.project_root.rglob(Path(preferred_token_file).name)
                           if not is_ignored_path(m, self.project_root)]
                matches.sort(key=lambda p: (_rank(p), len(str(p))))
                registry_file = matches[0] if matches else None
                if not registry_file:
                    return (False, f"Preferred file not found: {preferred_token_file}", None)
            else:
                registry_file = self._find_registry_file(token_type)
                if not registry_file:
                    all_files = self.find_all_token_files(token_type)
                    registry_file = all_files[0] if all_files else None

            if not registry_file or not registry_file.exists():
                return (False, f"NEEDS_MANUAL_MAPPING: Registry file not found for type: {token_type}. Use 'set_design_file_mapping' to configure.", {
                    "error_code": "NEEDS_MANUAL_MAPPING",
                    "token_type": token_type,
                })
            
            registry_file = assert_inside(self.project_root, registry_file)
            style = self.detect_storage_style(registry_file)

            # 7. Generate new registry entry
            new_entry = self._generate_registry_entry(token_name, value, token_type, style=style)
            if not new_entry:
                return (False, "Cannot generate registry entry", None)
            
            # 8. Backup both files
            target_backup = self._backup_file(target_file)
            registry_backup = self._backup_file(registry_file)
            try:
                from core.ast_finder import safe_create_backup
                safe_create_backup(target_file, project_root=self.project_root)
                safe_create_backup(registry_file, project_root=self.project_root)
            except Exception:
                pass
            
            # 9. Modify registry file
            try:
                reg_content = registry_file.read_text(encoding="utf-8")
                reg_content = self._ensure_registry_imports(reg_content, token_type, style)
                insert_idx = self._find_registry_insertion_point(reg_content, token_type=token_type, style=style)
                reg_lines = reg_content.splitlines()
                reg_lines.insert(insert_idx, new_entry)
                _atomic_write_text(registry_file, "\n".join(reg_lines) + "\n")

                saved_reg = registry_file.read_text(encoding="utf-8")
                if token_name not in saved_reg:
                    shutil.copy2(registry_backup, registry_file)
                    return (False, f"Verification failed: token '{token_name}' was not found in {registry_file.name} after write", None)
            except Exception as e:
                shutil.copy2(registry_backup, registry_file)
                return (False, f"Registry edit failed: {e}", None)
            
            # 10. Replace in target file with scoping and auto-import
            try:
                reg_pkg = self._extract_package(reg_content)
                target_pkg = self._extract_package(content)
                
                obj_match = re.search(r'\bobject\s+(\w+)', reg_content)
                obj_name = obj_match.group(1) if obj_match else None

                if style in ("registry", "object") or obj_name:
                    actual_obj = obj_name if obj_name else registry_file.stem
                    scoped_name = f"{actual_obj}.{token_name}"
                    new_import = f"import {reg_pkg}.{actual_obj}" if (reg_pkg and reg_pkg != target_pkg) else None
                else:
                    scoped_name = token_name
                    new_import = f"import {reg_pkg}.{token_name}" if (reg_pkg and reg_pkg != target_pkg) else None

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
                    shutil.copy2(registry_backup, registry_file)
                    return (False, f"Replacement failed: '{value}' not found as standalone", None)
                
                lines[line - 1] = new_line
                
                import_added = False
                target_line_ending = "\r\n" if "\r\n" in content else "\n"
                updated_target_content = target_line_ending.join(lines) + target_line_ending
                if new_import and self._needs_import(updated_target_content, new_import):
                    updated_target_content = self._add_import(updated_target_content, new_import)
                    import_added = True

                _atomic_write_text(target_file, updated_target_content)
            except Exception as e:
                shutil.copy2(target_backup, target_file)
                shutil.copy2(registry_backup, registry_file)
                return (False, f"Target edit failed: {e}", None)
            
            # 11. Record in PatternJournal
            try:
                try:
                    from core.pattern_journal import PatternJournal
                except ImportError:
                    from .pattern_journal import PatternJournal
                journal = PatternJournal(self.project_root)
                pid = f"create_token_{token_name}"
                rel_reg = str(registry_file.relative_to(self.project_root)).replace("\\", "/")
                journal.record_token_added(pattern_id=pid, token_name=token_name, token_value=value, file=rel_reg)
                journal.record_change(file=rel_reg, line=insert_idx + 1, old_line="", new_line=new_entry.strip(), pattern_id=pid)

                rel_target = str(target_file.relative_to(self.project_root)).replace("\\", "/")
                if import_added and new_import:
                    journal.record_change(file=rel_target, line=1, old_line="", new_line=new_import.strip(), pattern_id=pid)
                journal.record_change(file=rel_target, line=line, old_line=old_line.strip(), new_line=new_line.strip(), pattern_id=pid)
            except Exception as e:
                import sys
                sys.stderr.write(f"[TOKEN_MIGRATOR] PatternJournal record failed: {e}\n")

            # 12. Success
            if force_create:
                self.events.log(
                    "token_created_force",
                    {
                        "token_name": token_name,
                        "value": value,
                        "type": token_type,
                        "warning": "force_create used — bypassed duplicate detection",
                    },
                    source="user",
                )
            
            info = {
                "target_file": str(target_file.relative_to(self.project_root)),
                "target_line": line,
                "old_line": old_line.strip(),
                "new_line": new_line.strip(),
                "scoped_name": scoped_name,
                "import_added": import_added,
                "registry_file": str(registry_file.relative_to(self.project_root)),
                "registry_entry": new_entry.strip(),
                "backup_target": str(target_backup),
                "backup_registry": str(registry_backup),
            }
            
            return (
                True,
                f"Added token '{token_name}' and updated {file_name}:{line}",
                info,
            )


CONTEXT_RULES = TokenMigrator.CONTEXT_RULES
COLOR_CONTEXT_RULES = TokenMigrator.COLOR_CONTEXT_RULES
