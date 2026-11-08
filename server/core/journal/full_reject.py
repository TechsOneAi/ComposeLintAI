# -*- coding: utf-8 -*-
"""
وحدة التراجع عن تعديلات ملف كامل (Full File Rejection).
Handles file-level rollback, dependency guard checks, external modification protection,
and fallback file restorations.
"""

import os
import re
import json
import shutil
import hashlib
import subprocess
from pathlib import Path
from typing import Dict, Any

from common.file_ops import assert_inside, is_ignored_path, is_file_dirty_in_ide
from core.journal.backup_verifier import compute_normalized_hash, compute_deep_normalized_hash


class FullRejectMixin:
    """Mixin class providing file-level rejection logic for PatternJournal."""

    def reject_file_changes(self, file_path: str, force: bool = False) -> Dict:
        """Revert all changes for a specific file with external modification guard."""
        changes = self.get_file_changes(file_path)
        full_path = assert_inside(self.project_root, self.project_root / file_path)
        if not full_path.exists():
            matches = [m for m in self.project_root.rglob(Path(file_path).name) if not is_ignored_path(m, self.project_root)]
            if matches:
                full_path = matches[0]
            else:
                return {"success": False, "error": f"File '{file_path}' not found"}

        backup_path = Path(str(full_path) + ".bridge.bak")
        if not backup_path.exists():
            alt_matches = [m for m in self.project_root.rglob(full_path.name + ".bridge.bak") if "build" not in str(m) and ".composebridge-state" not in str(m)]
            if alt_matches:
                backup_path = alt_matches[0]
        if not backup_path.exists():
            mb_dir = self.project_root / ".composebridge-state" / "migration_backups"
            if mb_dir.exists():
                mb_candidates = sorted(mb_dir.glob(f"{full_path.stem}_*.kt.bak"), key=lambda x: x.stat().st_mtime, reverse=True)
                if mb_candidates:
                    backup_path = mb_candidates[0]

        # ═══ Dependency Guard: Refuse rejecting DesignSystem if its tokens are used by other files ═══
        target_name = Path(file_path).name
        registry_names = {
            "designsystem.kt", "appcolors.kt", "theme.kt", "appdimens.kt",
            "appstrings.kt", "themetokens.kt", "colors.kt", "dimens.kt", "strings.kt"
        }
        if target_name.lower() in registry_names and full_path.exists() and not force:
            try:
                curr_text = full_path.read_text(encoding="utf-8", errors="ignore")
                bak_text = ""
                if backup_path.exists():
                    bak_text = backup_path.read_text(encoding="utf-8", errors="ignore")

                curr_tokens = set(re.findall(r'\bval\s+([A-Za-z0-9_]+)\b', curr_text))
                bak_tokens = set(re.findall(r'\bval\s+([A-Za-z0-9_]+)\b', bak_text)) if bak_text else set()
                lost_tokens = curr_tokens - bak_tokens

                for entry in changes:
                    tname = entry.get("token") or entry.get("token_name")
                    if tname:
                        lost_tokens.add(tname)
                    pid = str(entry.get("pattern_id", ""))
                    if pid.startswith("create_token_"):
                        lost_tokens.add(pid.replace("create_token_", ""))
                    new_val = str(entry.get("new", "") or entry.get("new_line", ""))
                    for t in re.findall(r'\bval\s+([A-Za-z0-9_]+)\b', new_val):
                        lost_tokens.add(t)

                if lost_tokens:
                    for kt in self.project_root.rglob("*.kt"):
                        if is_ignored_path(kt, self.project_root):
                            continue
                        try:
                            if kt.resolve() == full_path.resolve():
                                continue
                            kt_content = kt.read_text(encoding="utf-8", errors="ignore")
                        except Exception:
                            continue

                        for t in lost_tokens:
                            pattern = rf'\b(?:AppColors|AppDimens|AppStrings|DesignSystem|Theme)\.{re.escape(t)}\b'
                            if re.search(pattern, kt_content) or f".{t}" in kt_content:
                                try:
                                    rel_kt = str(kt.relative_to(self.project_root)).replace("\\", "/")
                                except Exception:
                                    rel_kt = str(kt).replace("\\", "/")
                                return {
                                    "success": False,
                                    "error": f"Cannot reject registry files directly. Reject the UI files that use them instead. Cannot reject {target_name} because its tokens are used by other approved files (file '{rel_kt}' uses token '{t}').",
                                    "message": f"Cannot reject registry files directly. Reject the UI files that use them instead. Cannot reject {target_name} because its tokens are used by other approved files (file '{rel_kt}' uses token '{t}').",
                                    "error_code": "DEPENDENCY_BLOCK",
                                    "blocking_file": rel_kt,
                                    "blocking_token": t
                                }
            except Exception:
                pass

        is_git_tracked = False
        try:
            rel_file = str(full_path.relative_to(self.project_root)).replace("\\", "/")
            chk = subprocess.run(
                ["git", "ls-files", "--error-unmatch", rel_file],
                cwd=str(self.project_root),
                capture_output=True,
                text=True
            )
            is_git_tracked = (chk.returncode == 0)
        except Exception:
            is_git_tracked = False

        if not backup_path.exists() and not changes and not is_git_tracked:
            return {"success": False, "error": f"No changes or backup found for {file_path}"}

        curr_content = full_path.read_text(encoding="utf-8")
        curr_bytes = full_path.read_bytes()
        curr_checksum_bytes = hashlib.sha256(curr_bytes).hexdigest()
        curr_checksum_text = hashlib.sha256(curr_content.encode("utf-8")).hexdigest()
        curr_checksum_norm = compute_normalized_hash(curr_bytes)
        curr_checksum_deep = compute_deep_normalized_hash(curr_bytes)

        # Check for external modifications (verify against the latest change state)
        external_modified = False
        mismatch_reason = ""

        latest_change = changes[-1] if changes else None
        if latest_change and latest_change.get("checksum_after"):
            expected_chk = latest_change["checksum_after"]
            valid_hashes = (curr_checksum_bytes, curr_checksum_text, curr_checksum_norm, curr_checksum_deep)
            if expected_chk not in valid_hashes:
                # Secondary verification: Check if the change made by the tool is still intact in the file.
                is_actually_modified = True
                if latest_change.get("new"):
                    curr_norm = "\n".join(l.rstrip() for l in curr_content.replace("\r\n", "\n").replace("\r", "\n").splitlines())
                    target_norm = "\n".join(l.rstrip() for l in str(latest_change["new"]).replace("\r\n", "\n").replace("\r", "\n").splitlines()).strip()
                    if target_norm in curr_norm or target_norm.strip() in curr_norm:
                        is_actually_modified = False
                
                if is_actually_modified:
                    external_modified = True
                    mismatch_reason = "checksum_mismatch"

        if not external_modified and latest_change and latest_change.get("new"):
            curr_norm = curr_content.replace("\r\n", "\n").replace("\r", "\n")
            target_norm = str(latest_change["new"]).replace("\r\n", "\n").replace("\r", "\n")
            if target_norm not in curr_norm and target_norm.strip() not in curr_norm:
                external_modified = True
                mismatch_reason = "content_missing" 

        if not external_modified and backup_path.exists() and not changes:
            # Check if file is already identical to backup under deep normalization
            try:
                bak_bytes = backup_path.read_bytes()
                if compute_deep_normalized_hash(curr_bytes) == compute_deep_normalized_hash(bak_bytes):
                    pass # Identical to backup, no external modification
                else:
                    external_modified = True
                    mismatch_reason = "orphan_backup_modified"
            except Exception:
                external_modified = True
                mismatch_reason = "orphan_backup_modified"

        if is_file_dirty_in_ide(full_path, self.project_root):
            external_modified = True
            mismatch_reason = "dirty_buffer"

        file_name = Path(file_path).name
        msg_en = (
            f"⚠️ Warning: File '{file_name}' was modified externally (manually or by another agent).\n"
            f"Restoring the backup will overwrite these changes.\n"
            f"Are you sure?"
        )

        if external_modified and not force:
            try:
                from core.sync_client import send_warning_to_bridge
                send_warning_to_bridge(
                    warning_type="external_modification_detected",
                    file_path=str(file_path),
                    message=msg_en,
                    error_code="EXTERNAL_MODIFICATION_DETECTED",
                    suggested_action="confirm_restore",
                )
            except Exception:
                pass

            return {
                "success": False,
                "error_code": "EXTERNAL_MODIFICATION_DETECTED",
                "file": file_path,
                "message": msg_en,
                "error": msg_en,
                "mismatch_reason": mismatch_reason,
                "suggested_action": "confirm_restore",
                "can_force": True,
            }

        reverted = 0
        errors = []

        # 1. Primary rollback: Git checkout (if file is tracked in Git)
        if is_git_tracked:
            try:
                rel_file = str(full_path.relative_to(self.project_root)).replace("\\", "/")
                res = subprocess.run(
                    ["git", "-C", str(self.project_root), "checkout", "--", rel_file],
                    cwd=str(self.project_root),
                    capture_output=True,
                    text=True
                )
                if res.returncode == 0:
                    from core.ast_finder import validate_kotlin_syntax
                    is_valid, err_msg = validate_kotlin_syntax(full_path)
                    if not is_valid:
                        return {
                            "success": False,
                            "file": file_path,
                            "error": f"❌ فشل التراجع: الملف المسترجع يحتوي على أخطاء برمجية ({err_msg}). يرجى التحقق من Git أو إصلاح الملف يدوياً.",
                            "message": f"❌ فشل التراجع: الملف المسترجع يحتوي على أخطاء برمجية ({err_msg}). يرجى التحقق من Git أو إصلاح الملف يدوياً.",
                        }
                    if backup_path.exists():
                        try:
                            backup_path.unlink()
                        except Exception:
                            pass
                    self._clean_pending_edits(file_path)
                    self._clean_journal_for_file(file_path)
                    cascade_info = self._handle_cascade_token_reject(file_path, changes)
                    msg = f"تمت استعادة الملف '{file_path}' بنجاح عبر Git."
                    if cascade_info.get("removed_tokens"):
                        msg += f" وتم حذف التوكنات اليتيمة تلقائياً: {', '.join(cascade_info['removed_tokens'])}."
                    if cascade_info.get("cleaned_files"):
                        msg += f" وتنظيف ملفات التسجيل: {', '.join(cascade_info['cleaned_files'])}."
                    if cascade_info.get("warning"):
                        msg += f"\n{cascade_info['warning']}"
                    return {
                        "success": True,
                        "file": file_path,
                        "reverted_count": 1,
                        "cascade_removed_tokens": cascade_info.get("removed_tokens", []),
                        "cascade_cleaned_files": cascade_info.get("cleaned_files", []),
                        "warning": cascade_info.get("warning"),
                        "message": msg,
                    }
                else:
                    errors.append({"file": file_path, "error": f"Git checkout returned code {res.returncode}: {res.stderr}"})
            except Exception as e:
                errors.append({"file": file_path, "error": f"Git checkout error: {e}"})

        # 2. Secondary rollback: .bridge.bak backup (holds true session baseline if not tracked in Git)
        if backup_path.exists():
            try:
                from core.ast_finder import validate_kotlin_syntax
                is_valid_bak, bak_err = validate_kotlin_syntax(backup_path)
            except Exception:
                is_valid_bak = True

            if is_valid_bak:
                try:
                    orig_content = full_path.read_text(encoding="utf-8")
                    shutil.copy2(str(backup_path), str(full_path))
                    from core.ast_finder import validate_kotlin_syntax
                    is_valid, err_msg = validate_kotlin_syntax(full_path)
                    if not is_valid:
                        full_path.write_text(orig_content, encoding="utf-8")
                        return {
                            "success": False,
                            "file": file_path,
                            "error": f"❌ فشل التراجع: الملف المسترجع يحتوي على أخطاء برمجية ({err_msg}). يرجى التحقق من الملف يدوياً.",
                            "message": f"❌ فشل التراجع: الملف المسترجع يحتوي على أخطاء برمجية ({err_msg}). يرجى التحقق من الملف يدوياً.",
                        }
                    try:
                        backup_path.unlink()
                    except Exception:
                        pass
                    reverted += 1
                    self._clean_pending_edits(file_path)
                    self._clean_journal_for_file(file_path)
                    cascade_info = self._handle_cascade_token_reject(file_path, changes)
                    msg = f"تمت استعادة الملف '{file_path}' بنجاح من النسخة الاحتياطية الأصلية (.bridge.bak)."
                    if cascade_info.get("removed_tokens"):
                        msg += f" وتم حذف التوكنات اليتيمة تلقائياً: {', '.join(cascade_info['removed_tokens'])}."
                    if cascade_info.get("cleaned_files"):
                        msg += f" وتنظيف ملفات التسجيل: {', '.join(cascade_info['cleaned_files'])}."
                    if cascade_info.get("warning"):
                        msg += f"\n{cascade_info['warning']}"
                    return {
                        "success": True,
                        "file": file_path,
                        "reverted_count": reverted,
                        "cascade_removed_tokens": cascade_info.get("removed_tokens", []),
                        "cascade_cleaned_files": cascade_info.get("cleaned_files", []),
                        "warning": cascade_info.get("warning"),
                        "message": msg,
                    }
                except Exception as e:
                    errors.append({"file": file_path, "error": f"Failed to restore backup: {e}"})
            else:
                return {
                    "success": False,
                    "file": file_path,
                    "error": f"❌ فشل التراجع: النسخة الاحتياطية تحتوي على أخطاء برمجية ({bak_err}). يرجى التحقق من Git أو إصلاح الملف يدوياً.",
                    "message": f"❌ فشل التراجع: النسخة الاحتياطية تحتوي على أخطاء برمجية ({bak_err}). يرجى التحقق من Git أو إصلاح الملف يدوياً.",
                }

        # 3. Tertiary check: If no backup and not tracked in Git, fail with clear error
        if not backup_path.exists() and not is_git_tracked:
            return {"success": False, "file": file_path, "error": f"❌ لا توجد نسخة احتياطية (.bridge.bak) ولا تتبع Git للملف '{file_path}' للتراجع عنها."}

        # Granular line-targeted change reversal as last resort
        for change in reversed(changes):
            try:
                content = full_path.read_text(encoding="utf-8")
                target_new = change["new"]
                target_old = change["old"]
                lines = content.splitlines()
                target_line = change.get("line", 0)
                reverted_this = False

                if target_old == "":
                    # Line was inserted (e.g. import). Delete it completely.
                    if 1 <= target_line <= len(lines) + 5:
                        for offset in [0, 1, -1, 2, -2, 3, -3, 4, -4, 5, -5]:
                            idx = target_line - 1 + offset
                            if 0 <= idx < len(lines):
                                if lines[idx].strip() == target_new.strip():
                                    del lines[idx]
                                    full_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                                    reverted += 1
                                    reverted_this = True
                                    break
                    if not reverted_this:
                        for line_cand in [target_new.strip() + "\r\n", target_new.strip() + "\n"]:
                            if line_cand in content:
                                content = content.replace(line_cand, "", 1)
                                full_path.write_text(content, encoding="utf-8")
                                reverted += 1
                                reverted_this = True
                                break
                    if not reverted_this:
                        errors.append({"file": file_path, "error": "New content not found"})
                    continue

                if 1 <= target_line <= len(lines) + 5:
                    for offset in [0, 1, -1, 2, -2, 3, -3, 4, -4, 5, -5]:
                        idx = target_line - 1 + offset
                        if 0 <= idx < len(lines):
                            if target_new in lines[idx]:
                                lines[idx] = lines[idx].replace(target_new, target_old, 1)
                                full_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                                reverted += 1
                                reverted_this = True
                                break
                            elif target_new.strip() in lines[idx]:
                                lines[idx] = lines[idx].replace(target_new.strip(), target_old.strip(), 1)
                                full_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                                reverted += 1
                                reverted_this = True
                                break

                if not reverted_this:
                    if target_new in content:
                        content = content.replace(target_new, target_old, 1)
                        full_path.write_text(content, encoding="utf-8")
                        reverted += 1
                    elif target_new.strip() in content:
                        for line_cand in [target_new.strip() + "\r\n", target_new.strip() + "\n", target_new.strip()]:
                            if line_cand in content:
                                content = content.replace(line_cand, target_old, 1)
                                full_path.write_text(content, encoding="utf-8")
                                reverted += 1
                                reverted_this = True
                                break
                        if not reverted_this:
                            errors.append({"file": file_path, "error": "New content not found"})
                    else:
                        errors.append({"file": file_path, "error": "New content not found"})
            except Exception as e:
                errors.append({"file": file_path, "error": str(e)})

        # Remove reverted entries from journal
        target_name = Path(file_path).name
        remaining = []
        if self.journal_file.exists():
            with open(self.journal_file, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        entry = json.loads(line.strip())
                        efile = entry.get("file", "")
                        if efile != file_path and Path(efile).name != target_name:
                            remaining.append(line)
                    except Exception:
                        continue

            with open(self.journal_file, "w", encoding="utf-8") as f:
                f.writelines(remaining)

        self._clean_pending_edits(file_path)
        return {
            "success": len(errors) == 0,
            "file": file_path,
            "reverted_count": reverted,
            "error_count": len(errors),
            "errors": errors,
        }

