# -*- coding: utf-8 -*-
"""
وحدة التراجع الجزئي على مستوى التغييرات الفردية (Hunk Reject).
Handles single granular change (hunk) rejection in files.
"""

from typing import Dict


class HunkRejectMixin:
    """Mixin class providing granular hunk rejection logic for PatternJournal."""

    def reject_change(self, file_path: str, change_id: str) -> Dict:
        """Reject a single granular change (hunk) in file."""
        from core.diff_engine import reject_change
        return reject_change(file_path, change_id, self.project_root)

