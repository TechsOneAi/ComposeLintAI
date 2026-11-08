# -*- coding: utf-8 -*-
"""
وحدة تحليل النصوص ومراجع الموارد في نطاق كود الـ Composable (Text Scope Analyzer).
Extracts and resolves hardcoded strings and AppStrings references within element AST scope.
"""

import re
from pathlib import Path
from typing import List, Dict, Optional, Any


def _find_matching_parenthesis(text: str, start_paren_idx: int) -> int:
    """
    Finds the index of the matching closing parenthesis ')' for the '(' at start_paren_idx.
    Handles nested parentheses, strings, and escape sequences in linear O(N) time.
    Returns -1 if not found.
    """
    depth = 0
    in_string = False
    escape = False
    i = start_paren_idx
    n = len(text)
    while i < n:
        c = text[i]
        if in_string:
            if escape:
                escape = False
            elif c == '\\':
                escape = True
            elif c == '"':
                in_string = False
        else:
            if c == '"':
                in_string = True
            elif c == '(':
                depth += 1
            elif c == ')':
                depth -= 1
                if depth == 0:
                    return i
        i += 1
    return -1


def _extract_texts_in_scope(
    search_scope: str,
    start_line_in_file: int,
    dim_scope_text: str,
    dim_scope_start_line: int,
    actual_source_file: str,
    effective_file: str,
    content: str,
    root: Path,
    parent_line: Optional[int],
    runtime_text_hint: Optional[str],
) -> List[Dict[str, Any]]:
    """
    استخراج السلاسل النصية ومراجع الموارد من نطاق العنصر.
    Extracts hardcoded string literals and AppStrings resource references from scope.
    """
    from ..text_ref_analyzer import _resolve_app_strings_value

    def _extract_from_text(scope_str: str, base_line: int, file_p: str, filter_call_line: Optional[int] = None) -> List[Dict]:
        results = []
        seen = set()

        target_start_pattern = re.compile(
            r'\b(?:Text|BasicText|Button|OutlinedButton|FilledTonalButton|TextButton|'
            r'Icon|IconButton|Badge|ExtendedFloatingActionButton)\s*\('
        )

        for match in target_start_pattern.finditer(scope_str):
            call_offset = match.start()
            open_paren = match.end() - 1
            close_paren = _find_matching_parenthesis(scope_str, open_paren)
            if close_paren != -1:
                call_text = scope_str[call_offset : close_paren + 1]
            else:
                call_text = scope_str[call_offset:]
            line_in_file = base_line + scope_str[:call_offset].count("\n")

            if filter_call_line and abs(line_in_file - filter_call_line) > 5:
                continue

            for m in re.finditer(r'AppStrings(?:\.[a-zA-Z0-9_]+)+', call_text):
                ref_key = m.group(0)
                resolved = _resolve_app_strings_value(root, ref_key, runtime_text_hint)
                key = ("ref", ref_key, line_in_file)
                if key not in seen:
                    seen.add(key)
                    results.append({
                        "property": "text",
                        "text": resolved or ref_key,
                        "file": file_p,
                        "line": line_in_file,
                        "is_resource_or_ref": True,
                        "ref_key": ref_key,
                    })

            for m in re.finditer(r'"([^"\\]*(?:\\.[^"\\]*)*)"', call_text):
                raw = m.group(1)
                prefix_context = call_text[:m.start()]
                if re.search(r'(?:tag|key|route|name|testTag|action)\s*=\s*$', prefix_context):
                    continue
                clean = raw.replace('\\"', '"').replace('\\\\', '\\')
                key = ("hardcoded", clean, line_in_file)
                if key not in seen:
                    seen.add(key)
                    results.append({
                        "property": "text",
                        "text": clean,
                        "file": file_p,
                        "line": line_in_file,
                        "is_resource_or_ref": False,
                        "ref_key": None,
                    })

        for m in re.finditer(r'\btext\s*=\s*([a-zA-Z0-9_]+)\b', scope_str):
            var_name = m.group(1)
            if var_name in ["it", "text", "value"]:
                continue
            before_scope = content[:content.find(scope_str)] if scope_str in content else ""
            func_defs = list(re.finditer(r'fun\s+([A-Za-z0-9_]+)\s*\(([^)]*)\)', before_scope))
            if func_defs:
                last_func = func_defs[-1]
                func_name = last_func.group(1)
                params = last_func.group(2)
                if re.search(rf'\b{var_name}\b', params):
                    all_calls = []
                    call_start_pat = re.compile(rf'\b{re.escape(func_name)}\s*\(')
                    for cm in call_start_pat.finditer(content):
                        c_open = cm.end() - 1
                        c_close = _find_matching_parenthesis(content, c_open)
                        if c_close != -1:
                            all_calls.append((cm.start(), content[c_open + 1 : c_close]))
                    chosen_call = None

                    target_line = filter_call_line or parent_line
                    if target_line:
                        for c_start, call_body in all_calls:
                            c_line = content[:c_start].count("\n") + 1
                            if abs(c_line - target_line) <= 5:
                                chosen_call = (c_start, call_body)
                                break

                    if not chosen_call and runtime_text_hint:
                        for c_start, call_body in all_calls:
                            if runtime_text_hint in call_body:
                                chosen_call = (c_start, call_body)
                                break
                            for ref_m in re.finditer(r'AppStrings(?:\.[a-zA-Z0-9_]+)+', call_body):
                                res_val = _resolve_app_strings_value(root, ref_m.group(0), runtime_text_hint)
                                if res_val == runtime_text_hint:
                                    chosen_call = (c_start, call_body)
                                    break
                            if chosen_call:
                                break

                    calls_to_process = [chosen_call] if chosen_call else all_calls
                    for c_start, call_body in calls_to_process:
                        param_m = re.search(rf'{var_name}\s*=\s*([^\n,]+)', call_body)
                        if param_m:
                            arg_val = param_m.group(1).strip()
                            call_line = content[:c_start].count("\n") + 1
                            if arg_val.startswith("AppStrings."):
                                ref_k = arg_val
                                resolved_val = _resolve_app_strings_value(root, ref_k, runtime_text_hint)
                                key = ("ref", ref_k)
                                if key not in seen:
                                    seen.add(key)
                                    results.append({
                                        "property": var_name,
                                        "text": resolved_val or ref_k,
                                        "file": actual_source_file,
                                        "line": call_line,
                                        "is_resource_or_ref": True,
                                        "ref_key": ref_k,
                                    })
                            elif arg_val.startswith('"') and arg_val.endswith('"'):
                                raw_val = arg_val[1:-1].replace('\\"', '"').replace('\\\\', '\\')
                                key = ("hardcoded", raw_val)
                                if key not in seen:
                                    seen.add(key)
                                    results.append({
                                        "property": var_name,
                                        "text": raw_val,
                                        "file": actual_source_file,
                                        "line": call_line,
                                        "is_resource_or_ref": False,
                                        "ref_key": None,
                                    })
        return results

    structured_texts = _extract_from_text(search_scope, start_line_in_file, actual_source_file)
    if not structured_texts and dim_scope_text != search_scope:
        structured_texts = _extract_from_text(dim_scope_text, dim_scope_start_line, effective_file, filter_call_line=start_line_in_file)

    return structured_texts

