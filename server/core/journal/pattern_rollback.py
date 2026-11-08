# -*- coding: utf-8 -*-
"""
وحدة التراجع الشامل والتراجع حسب النمط (Pattern Rollback & Bulk Revert).
Handles reverting all pending changes in reverse order and rolling back
specific operation patterns.
"""

import json
import shutil
import re
from pathlib import Path
from typing import Dict, Any


class PatternRollbackMixin:
    """Mixin class providing bulk reversal and pattern rollback logic for PatternJournal."""

    def reject_all_files(self, force: bool = False) -> Dict:
        """
        Revert all pending changes in REVERSE order (latest edit first).
        """
        try:
            # 1. Restore any *.bridge.bak files on disk
            disk_reverted = 0
            restored_from_backup_files = set()
            try:
                for bak in list(self.project_root.rglob("*.bridge.bak")):
                    if any(ign in bak.parts for ign in ["build", ".gradle", ".git", ".composebridge-state"]):
                        continue
                    orig_str = str(bak)[:-len(".bridge.bak")]
                    orig_path = Path(orig_str)
                    try:
                        from core.ast_finder import validate_kotlin_syntax
                        is_valid_bak, _ = validate_kotlin_syntax(bak)
                        if is_valid_bak:
                            shutil.copy2(str(bak), str(orig_path))
                            bak.unlink()
                            disk_reverted += 1
                            try:
                                rel_b = str(orig_path.relative_to(self.project_root)).replace("\\", "/")
                            except Exception:
                                rel_b = orig_path.name
                            restored_from_backup_files.add(rel_b)
                            restored_from_backup_files.add(orig_path.name)
                    except Exception:
                        pass
            except Exception:
                pass

            # Clean pending_edits.json
            for pfile in [self.project_root / ".composebridge-state" / "pending_edits.json", self.project_root / "pending_edits.json"]:
                if pfile.exists():
                    try:
                        pfile.unlink()
                    except Exception:
                        pass

            if not self.journal_file.exists():
                return {
                    "success": True,
                    "reverted_count": disk_reverted,
                    "message": f"Reverted {disk_reverted} file(s) from disk backup",
                }

            entries = self._read_entries()
            if not entries:
                if self.journal_file.exists():
                    try:
                        self.journal_file.unlink()
                    except Exception:
                        pass
                return {
                    "success": True,
                    "reverted_count": 0,
                    "message": "Journal was empty",
                }

            # ⭐ اقرأ في ترتيب عكسي (الأحدث أولاً)
            reversed_entries = list(reversed(entries))

            reverted = 0
            errors = []
            changed_files = set()

            for entry in reversed_entries:
                file_rel = entry.get("file")
                # Do not apply reverse text replacements to files already restored from disk backup!
                if file_rel in restored_from_backup_files or Path(str(file_rel)).name in restored_from_backup_files:
                    continue
                line_no = entry.get("line", 0)
                old_line = entry.get("old_line") or entry.get("old") or entry.get("old_content") or ""
                new_line = entry.get("new_line") or entry.get("new") or entry.get("new_content") or ""

                if not file_rel:
                    continue

                file_path = self.project_root / file_rel
                if not file_path.exists():
                    matches = [m for m in self.project_root.rglob(Path(file_rel).name)
                               if "build" not in str(m) and ".composebridge-state" not in str(m) and ".bak" not in str(m)]
                    if not matches:
                        errors.append({"file": file_rel, "error": f"File not found: {file_rel}"})
                        continue
                    file_path = matches[0]

                try:
                    content = file_path.read_text(encoding="utf-8")
                    lines = content.splitlines()

                    # إذا كان old_line فارغاً و new_line موجوداً -> سطر تمت إضافته (import/token)
                    if old_line == "" and new_line != "":
                        target_idx = -1
                        if 1 <= line_no <= len(lines):
                            for offset in [0, 1, -1, 2, -2, 3, -3, 4, -4, 5, -5]:
                                idx = line_no - 1 + offset
                                if 0 <= idx < len(lines) and (new_line.strip() == lines[idx].strip() or new_line in lines[idx]):
                                    target_idx = idx
                                    break
                        if target_idx < 0:
                            for idx, l in enumerate(lines):
                                if new_line.strip() == l.strip() or new_line in l:
                                    target_idx = idx
                                    break
                        if target_idx >= 0:
                            del lines[target_idx]
                            file_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                            reverted += 1
                            changed_files.add(str(file_path.relative_to(self.project_root)))
                        continue

                    # استبدال new_line -> old_line
                    found = False
                    start_search = max(0, line_no - 6)
                    end_search = min(len(lines), line_no + 5)
                    for i in range(start_search, end_search):
                        if new_line in lines[i]:
                            lines[i] = lines[i].replace(new_line, old_line, 1)
                            found = True
                            break
                        elif new_line.strip() and new_line.strip() == lines[i].strip():
                            lines[i] = old_line
                            found = True
                            break
                        elif new_line.strip() and new_line.strip() in lines[i]:
                            lines[i] = lines[i].replace(new_line.strip(), old_line.strip(), 1)
                            found = True
                            break

                    # Fallback: في كامل الملف
                    if not found and new_line.strip():
                        for i, l in enumerate(lines):
                            if new_line in l:
                                lines[i] = lines[i].replace(new_line, old_line, 1)
                                found = True
                                break
                            elif new_line.strip() == l.strip():
                                lines[i] = old_line
                                found = True
                                break
                            elif new_line.strip() in l:
                                lines[i] = lines[i].replace(new_line.strip(), old_line.strip(), 1)
                                found = True
                                break

                    if found:
                        file_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                        reverted += 1
                        changed_files.add(str(file_path.relative_to(self.project_root)))
                    else:
                        # قد يكون السطر بالفعل قد استرجع قيمته القديمة
                        if old_line and any(old_line.strip() == l.strip() or old_line in l for l in lines):
                            pass
                        else:
                            errors.append({"file": file_rel, "line": line_no, "error": f"Target content not found: {new_line[:40]}"})

                except Exception as e:
                    errors.append({"file": file_rel, "error": str(e)})

            # 4. احذف الـ journal
            if self.journal_file.exists():
                try:
                    self.journal_file.unlink()
                except Exception:
                    pass

            # 5. نظّف backups
            backups_dir = self.project_root / ".composebridge-state" / "reject_backups"
            if backups_dir.exists():
                for f in backups_dir.glob("snapshot_*.json"):
                    try:
                        f.unlink()
                    except Exception:
                        pass

            return {
                "success": len(errors) == 0,
                "reverted_count": reverted,
                "files_affected": len(changed_files),
                "errors": errors[:10],
                "forced": force,
            }
        
        except Exception as e:
            import traceback
            return {
                "success": False,
                "error": str(e),
                "traceback": traceback.format_exc()[:500],
            }

    def rollback_pattern(self, pattern_id: str) -> Dict:
        """
        Rollback all changes for a specific pattern:
        1. Remove any added tokens from registry files.
        2. Revert every file modification in reverse order.
        3. Clean up journal entries.
        """
        if not self.journal_file.exists():
            return {"success": False, "error": f"Pattern '{pattern_id}' not found in journal"}

        # 1. ابحث عن tokens مُضافة
        tokens_added = []
        with open(self.journal_file, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    if (entry.get("pattern_id") == pattern_id and 
                        entry.get("type") == "token_added"):
                        tokens_added.append(entry)
                except Exception:
                    continue

        changes = self.get_pattern_changes(pattern_id)
        if not tokens_added and not changes:
            return {"success": False, "error": f"Pattern '{pattern_id}' not found in journal"}

        errors = []
        tokens_removed = []

        # 2. احذف كل token مُضاف
        from core.token_migrator import TokenMigrator
        migrator = TokenMigrator(self.project_root)
        for t in tokens_added:
            token_name = t.get("token_name")
            if not token_name:
                continue
            try:
                success = migrator.remove_token(token_name, "color")
                if success:
                    tokens_removed.append(token_name)
                else:
                    success_dim = migrator.remove_token(token_name, "dimension")
                    if success_dim:
                        tokens_removed.append(token_name)
                    else:
                        errors.append({
                            "token": token_name,
                            "error": f"Token '{token_name}' definition not found in registry file",
                        })
            except Exception as e:
                errors.append({
                    "token": token_name,
                    "error": str(e),
                })

        # 3. اعكس كل الاستبدالات من الأحدث للأقدم
        reverted = []
        for change in reversed(changes):
            file_name = change.get("file", "")
            if not file_name or not str(file_name).strip():
                continue
            file_path = self.project_root / file_name
            if not file_path.exists():
                matches = list(self.project_root.rglob(Path(file_name).name))
                matches = [m for m in matches if "build" not in str(m) and ".composebridge-state" not in str(m)]
                if matches:
                    file_path = matches[0]
                else:
                    errors.append({"file": file_name, "line": change.get("line"), "error": "File not found"})
                    continue

            try:
                content = file_path.read_text(encoding="utf-8")
                target_new = change["new"]
                target_old = change["old"]
                lines = content.splitlines()
                target_line = change.get("line", 0)
                reverted_this = False

                # First try line-targeted replacement with local search (+/- 5 lines)
                if 1 <= target_line <= len(lines) + 5:
                    for offset in [0, 1, -1, 2, -2, 3, -3, 4, -4, 5, -5]:
                        idx = target_line - 1 + offset
                        if 0 <= idx < len(lines):
                            if target_new in lines[idx]:
                                lines[idx] = lines[idx].replace(target_new, target_old, 1)
                                file_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                                reverted.append({"file": file_name, "line": target_line})
                                reverted_this = True
                                break
                            elif target_new.strip() in lines[idx]:
                                lines[idx] = lines[idx].replace(target_new.strip(), target_old.strip(), 1)
                                file_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                                reverted.append({"file": file_name, "line": target_line})
                                reverted_this = True
                                break

                # Fallback to whole file replacement
                if not reverted_this:
                    if target_new in content:
                        content = content.replace(target_new, target_old, 1)
                        file_path.write_text(content, encoding="utf-8")
                        reverted.append({"file": file_name, "line": target_line})
                        reverted_this = True
                    elif target_new.strip() in content:
                        for line_cand in [target_new.strip() + "\r\n", target_new.strip() + "\n", target_new.strip()]:
                            if line_cand in content:
                                content = content.replace(line_cand, target_old, 1)
                                file_path.write_text(content, encoding="utf-8")
                                reverted.append({"file": file_name, "line": target_line})
                                reverted_this = True
                                break
                        if not reverted_this:
                            errors.append({
                                "file": file_name,
                                "line": target_line,
                                "error": "New content not found in file",
                            })
                    else:
                        errors.append({
                            "file": file_name,
                            "line": target_line,
                            "error": "New content not found in file",
                        })
            except Exception as e:
                errors.append({"file": file_name, "line": change.get("line"), "error": str(e)})

        # 4. احذف التعديلات من الـ journal
        remaining = []
        with open(self.journal_file, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    if entry.get("pattern_id") != pattern_id:
                        remaining.append(line)
                except Exception:
                    continue

        with open(self.journal_file, "w", encoding="utf-8") as f:
            f.writelines(remaining)

        is_success = (len(errors) == 0) and (len(reverted) > 0 or len(tokens_removed) > 0)
        return {
            "success": is_success,
            "pattern_id": pattern_id,
            "reverted_count": len(reverted),
            "tokens_removed": tokens_removed,
            "error_count": len(errors),
            "reverted": reverted,
            "errors": errors,
        }

