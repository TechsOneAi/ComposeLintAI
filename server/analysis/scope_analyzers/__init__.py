# -*- coding: utf-8 -*-
"""
حزمة محللات نطاقات عناصر Compose (Scope Analyzers).
Modular analyzers for element scope: colors, dimensions, and text resources.
"""

from .color_scope import (
    _extract_colors_in_scope,
    _is_theme_dependent,
    _find_token_by_value,
    _update_existing_token,
    _find_all_occurrences,
    get_token_value_map,
    get_global_usage_counts,
)
from .dimension_scope import _extract_dimensions_in_scope
from .text_scope import _extract_texts_in_scope, _find_matching_parenthesis
from .composable_scope import (
    _find_enclosing_function_or_class,
    _extract_composable_scope,
    _count_function_calls,
)

__all__ = [
    "_extract_colors_in_scope",
    "_is_theme_dependent",
    "_find_token_by_value",
    "_update_existing_token",
    "_find_all_occurrences",
    "get_token_value_map",
    "get_global_usage_counts",
    "_extract_dimensions_in_scope",
    "_extract_texts_in_scope",
    "_find_matching_parenthesis",
    "_find_enclosing_function_or_class",
    "_extract_composable_scope",
    "_count_function_calls",
]
