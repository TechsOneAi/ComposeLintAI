# -*- coding: utf-8 -*-
"""
وحدة تحليل الأبعاد والمسافات في نطاق كود الـ Composable (Dimension Scope Analyzer).
Extracts and analyzes dimensions, padding, sizes, and font sizes within element AST scope.
"""

import re
from pathlib import Path
from typing import List, Dict, Tuple, Any


def _extract_dimensions_in_scope(
    content: str,
    search_scope: str,
    start_line_in_file: int,
    actual_source_file: str,
    actual_line_number: int,
    composable_name: str,
    root: Path,
    kt_cache: Any,
) -> Tuple[List[Dict[str, Any]], str, int, str, List[str], List[str], List[str]]:
    """
    استخراج أبعاد العنصر (Padding, Size, Font Sizes, Spacings).
    Extracts dimensions, font sizes, weights, and properties within the composable scope.
    """
    from ..dimension_analyzer import _find_dimension_usage, _extract_dimensions_with_property
    from ..ast_scope_resolver import _extract_composable_scope, _count_function_calls

    dim_scope_text = search_scope
    dim_scope_start_line = start_line_in_file
    effective_file = actual_source_file

    if composable_name and composable_name not in ["Box", "Column", "Row", "Text", "Icon", "Spacer", "_"]:
        comp_scope, comp_start, comp_end, comp_file = _extract_composable_scope(
            content, composable_name, root, current_file=actual_source_file
        )
        if comp_scope:
            dim_scope_text = comp_scope
            dim_scope_start_line = comp_start
            if comp_file:
                effective_file = comp_file

    dimensions_with_props = _extract_dimensions_with_property(dim_scope_text)

    for d in dimensions_with_props:
        rel = d.get("relative_offset", 0)
        lines_before = dim_scope_text[:rel].count("\n")
        d["line"] = dim_scope_start_line + lines_before
        d["file"] = effective_file
        d["default_value"] = d["number"]
        d["current_value"] = d["number"]

    filtered_dims = [d for d in dimensions_with_props if d.get("depth", 1) <= 2]
    if not filtered_dims:
        filtered_dims = dimensions_with_props

    call_count = 0
    if composable_name and composable_name not in ["Box", "Column", "Row", "Text", "Icon", "Surface", "Spacer", "_"]:
        call_count = _count_function_calls(root, composable_name, kt_cache=kt_cache)

    for d in filtered_dims:
        num = d.get("number", 0.0)
        kind = d.get("kind", "dp")
        u_count, u_locs = _find_dimension_usage(root, num, kind, kt_cache=kt_cache)
        d["usage_global"] = u_count
        d["is_shared"] = u_count > 1
        d["locations"] = u_locs
        d["in_shared_function"] = call_count > 1
        d["function_name"] = composable_name
        d["function_call_count"] = call_count

    bridge_token_pattern = re.compile(r'BridgeSpacing\.(\w+)')
    bridge_tokens = list(set(bridge_token_pattern.findall(dim_scope_text)))

    sps = [d["value"] for d in filtered_dims if d["kind"] == "sp"]
    weight_pattern = re.compile(r'FontWeight\.(\w+)')
    weights = list(set(weight_pattern.findall(dim_scope_text)))

    prop_pattern = re.compile(
        r'\.(background|padding|width|height|size|fillMaxWidth|fontSize|'
        r'lineHeight|letterSpacing|fontWeight|color|tint|containerColor|'
        r'cornerRadius|shape|border)\s*[=(]'
    )
    properties = list(set(prop_pattern.findall(dim_scope_text)))

    return filtered_dims, dim_scope_text, dim_scope_start_line, effective_file, bridge_tokens, sps, weights, properties

