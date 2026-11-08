# -*- coding: utf-8 -*-
"""
وحدة شلال التراجع عن التوكنات (Cascade Token Revert).
Handles inspecting whether a rejected file was part of token creation/pattern operation,
and safely cascading token removals from registry files when unreferenced.
"""

import os
import re
import json
from pathlib import Path
from typing import Dict, Any

from common.file_ops import assert_inside, is_ignored_path


class CascadeRevertMixin:
    """Mixin class providing cascade token rejection logic for PatternJournal."""

    def _handle_cascade_token_reject(self, file_path: str, file_changes: list) -> Dict[str, Any]:
        """
        Inspect whether the rejected file was part of a larger token creation or pattern operation.
        If a token was added in the session and linked to this file:
        - If other pending files in the journal still use this token, generates an informative warning.
        - If NO other pending files use this token, safely removes the token definition from the
          registry file (e.g. DesignSystem.kt / AppColors.kt).
        - If the registry file has no further changes after token removal, cleans up its backup and journal.
        """
        result = {
            "removed_tokens": [],
            "cleaned_files": [],
            "warning": None,
        }
        if not self.journal_file.exists():
            return result

        try:
            all_entries = []
            with open(self.journal_file, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        all_entries.append(json.loads(line.strip()))
                    except Exception:
                        continue

            pattern_ids = set()
            tokens_in_changes = set()
            target_name = Path(file_path).name

            for c in (file_changes or []):
                pid = c.get("pattern_id")
                if pid:
                    pattern_ids.add(str(pid))
                new_val = str(c.get("new", "") or c.get("new_line", ""))
                found_tokens = re.findall(r'\b(?:AppColors|AppDimens|DesignSystem|Theme)\.(\w+)\b', new_val)
                tokens_in_changes.update(found_tokens)

            candidate_tokens = []
            for entry in all_entries:
                etype = entry.get("type")
                if etype == "token_added":
                    tname = entry.get("token_name")
                    pid = entry.get("pattern_id", "")
                    reg_file = entry.get("file", "")
                    ttype = entry.get("token_type", "color")
                    if pid in pattern_ids or tname in tokens_in_changes or f"create_token_{tname}" in pattern_ids:
                        candidate_tokens.append((tname, pid, reg_file, ttype))
                elif etype == "pattern_summary":
                    tname = entry.get("token_name")
                    pid = entry.get("pattern_id", "")
                    reg_file = entry.get("token_file", "")
                    ttype = "color"
                    if pid in pattern_ids or tname in tokens_in_changes:
                        candidate_tokens.append((tname, pid, reg_file, ttype))
                else:
                    pid = entry.get("pattern_id", "")
                    if pid.startswith("create_token_"):
                        tname = pid.replace("create_token_", "")
                        if pid in pattern_ids or tname in tokens_in_changes:
                            reg_file = entry.get("file", "")
                            candidate_tokens.append((tname, pid, reg_file, "color"))

            unique_candidates = {}
            for tname, pid, reg_file, ttype in candidate_tokens:
                if tname and tname not in unique_candidates:
                    unique_candidates[tname] = (pid, reg_file, ttype)

            if not unique_candidates:
                return result

            warnings = []
            tokens_to_remove = []

            from core.token_baseline import is_pre_existing, check_token_usages_in_project

            for tname, (pid, reg_file, ttype) in unique_candidates.items():
                # 1. BASELINE IMMUNITY CHECK:
                # If the token is pre-existing in baseline, it MUST NEVER be cascade-deleted!
                if is_pre_existing(tname, self.project_root):
                    warnings.append(
                        f"🛡️ حماية التوكن الأساسي: التوكن '{tname}' مسجل في الفهرس الأساسي للمشروع (Baseline) ولا يمكن حذفه تلقائياً."
                    )
                    continue

                other_files_using_token = set()
                for entry in all_entries:
                    efile = str(entry.get("file", "")).replace("\\", "/")
                    if not efile:
                        continue
                    if efile == file_path or Path(efile).name == target_name:
                        continue
                    if reg_file and (efile == reg_file or Path(efile).name == Path(reg_file).name):
                        continue
                    
                    entry_pid = str(entry.get("pattern_id", ""))
                    entry_content = str(entry.get("new", "") or entry.get("new_line", ""))
                    if (pid and entry_pid == pid) or (tname in entry_content):
                        other_files_using_token.add(Path(efile).name)

                # Check usages via token_baseline index & PROJECT_CACHE
                try:
                    baseline_usages = check_token_usages_in_project(tname, self.project_root, exclude_file=file_path)
                    for bu in baseline_usages:
                        b_name = Path(bu).name
                        if b_name != target_name and b_name != Path(file_path).name:
                            other_files_using_token.add(b_name)
                except Exception:
                    pass

                # Check remaining disk usages across all kotlin files in the project
                try:
                    disk_usages = self.check_remaining_token_usages(tname)
                    for du in disk_usages:
                        if du != target_name and du != Path(file_path).name:
                            other_files_using_token.add(du)
                except Exception:
                    pass

                # Atomic check: verify target_file actually no longer contains the token on disk!
                target_still_uses_token = False
                try:
                    target_full = assert_inside(self.project_root, self.project_root / file_path)
                    if not target_full.exists():
                        matches = [m for m in self.project_root.rglob(Path(file_path).name) if not is_ignored_path(m, self.project_root)]
                        if matches:
                            target_full = matches[0]
                    if target_full.exists():
                        target_code = target_full.read_text(encoding="utf-8")
                        if re.search(rf'\b(?:AppColors|AppDimens|DesignSystem|Theme)\.{re.escape(tname)}\b', target_code):
                            target_still_uses_token = True
                except Exception:
                    pass

                if target_still_uses_token:
                    warnings.append(
                        f"⚠️ تحذير: التوكن '{tname}' لم يُحذف من ملف التصميم لأن الملف '{target_name}' ما زال يحتوي عليه على القرص."
                    )
                    continue

                if other_files_using_token:
                    files_str = ", ".join(sorted(other_files_using_token))
                    warnings.append(
                        f"⚠️ تحذير: التوكن '{tname}' ما زال موجوداً في ملف التصميم لأنه مستخدم في ملفات أخرى ({files_str})."
                    )
                else:
                    tokens_to_remove.append((tname, pid, reg_file, ttype))

            if tokens_to_remove:
                from core.token_migrator import TokenMigrator
                migrator = TokenMigrator(self.project_root)
                affected_reg_files = set()

                for tname, pid, reg_file, ttype in tokens_to_remove:
                    removed = migrator.remove_token(tname, "color")
                    if not removed:
                        removed = migrator.remove_token(tname, "dimension")
                    if removed:
                        result["removed_tokens"].append(tname)
                        if reg_file:
                            affected_reg_files.add(reg_file)

                removed_set = set(result["removed_tokens"])
                clean_lines = []
                with open(self.journal_file, "r", encoding="utf-8") as f:
                    for line in f:
                        try:
                            ent = json.loads(line.strip())
                            tn = ent.get("token_name")
                            pid = ent.get("pattern_id", "")
                            if tn in removed_set or pid.replace("create_token_", "").replace("remove_token_", "") in removed_set:
                                continue
                            clean_lines.append(line)
                        except Exception:
                            clean_lines.append(line)
                with open(self.journal_file, "w", encoding="utf-8") as f:
                    f.writelines(clean_lines)

                remaining_journal_entries = []
                if self.journal_file.exists():
                    with open(self.journal_file, "r", encoding="utf-8") as f:
                        for line in f:
                            try:
                                remaining_journal_entries.append(json.loads(line.strip()))
                            except Exception:
                                pass

                for rf in affected_reg_files:
                    rf_path = self.project_root / rf
                    if not rf_path.exists():
                        matches = [m for m in self.project_root.rglob(Path(rf).name) if not is_ignored_path(m, self.project_root)]
                        if matches:
                            rf_path = matches[0]
                    if rf_path.exists():
                        bak = rf_path.with_name(rf_path.name + ".bridge.bak")
                        rf_norm = str(rf_path).replace("\\", "/").lower()
                        rf_name = rf_path.name.lower()
                        has_other_changes = any(
                            (str(e.get("file", "")).replace("\\", "/").lower() == rf_norm or Path(e.get("file", "")).name.lower() == rf_name)
                            and e.get("type") not in ("token_added", "pattern_summary")
                            for e in remaining_journal_entries
                        )
                        if not has_other_changes and bak.exists():
                            try:
                                orig_c = bak.read_text(encoding="utf-8")
                                rf_path.write_text(orig_c, encoding="utf-8")
                                bak.unlink()
                                rel_rf = str(rf_path.relative_to(self.project_root)).replace("\\", "/")
                                self._clean_pending_edits(rel_rf)
                                self._clean_journal_for_file(rel_rf)
                                result["cleaned_files"].append(rf_path.name)
                            except Exception:
                                pass

            if warnings:
                result["warning"] = "\n".join(warnings)

        except Exception:
            pass

        return result

