# -*- coding: utf-8 -*-
"""
واجهة إدارة التراجع عن التعديلات والـ Rollback المتقدم وشلال التراجع (Rollback Manager Facade).
Facade combining hunk rejection, full file rollback, cascade revert, and pattern rollback.
"""

from .hunk_reject import HunkRejectMixin
from .cascade_revert import CascadeRevertMixin
from .full_reject import FullRejectMixin
from .pattern_rollback import PatternRollbackMixin


class RollbackMixin(HunkRejectMixin, CascadeRevertMixin, FullRejectMixin, PatternRollbackMixin):
    """
    Mixin class providing complete rollback and rejection logic for PatternJournal.
    Inherits all specialized sub-mixins for modularity and maintainability:
    - HunkRejectMixin: Single granular change (hunk) rejections.
    - CascadeRevertMixin: Dependency checks and unreferenced token cleanup.
    - FullRejectMixin: Complete file restorations and external modification protection.
    - PatternRollbackMixin: Bulk reverse restorations and pattern rollbacks.
    """
    pass


__all__ = [
    "RollbackMixin",
    "HunkRejectMixin",
    "CascadeRevertMixin",
    "FullRejectMixin",
    "PatternRollbackMixin",
]
