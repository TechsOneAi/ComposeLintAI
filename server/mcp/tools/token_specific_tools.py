# -*- coding: utf-8 -*-
"""
أدوات التوكنز المتخصصة والمتقدمة لخادم MCP (Token Specific Inspection Tools).
Contains find_token_by_color, list_orphans, get_token_impact, suggest_token_split,
detect_conditional_variants, get_token_siblings, and get_all_token_groups.
"""

import sys
import json
import traceback
from pathlib import Path
from typing import Optional, List, Dict, Any

from mcp.app import app, log, PROJECT_ROOT
from mcp.types import ToolAnnotations
from core.theme_indexer import get_index


@app.tool(
    name="find_token_by_color",
    description="Reverse lookup: Find token(s) matching an exact hex color.",
    annotations=ToolAnnotations(
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    ),
)
def find_token_by_color(hex_color: str) -> str:
    """Reverse lookup: Find token(s) matching a hex color. Match is EXACT (case-insensitive)."""
    log(f"🔧 find_token_by_color: {hex_color}")
    try:
        idx = get_index()
        token = idx.find_token_by_color(hex_color)
        return json.dumps({"token": token})
    except Exception as e:
        log(f"❌ find_token_by_color error: {e}")
        log(traceback.format_exc())
        return json.dumps({"error": str(e)})


@app.tool(
    annotations=ToolAnnotations(
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    )
)
def list_orphans(project_path: str = None) -> str:
    """List all orphan tokens (registered but never used)."""
    from core.project_scanner import ProjectScanner
    from core.project_root import get_project_root
    
    try:
        root = get_project_root(Path(project_path) if project_path else None)
        scanner = ProjectScanner(root)
        report = scanner.scan(include_hardcoded=False)
        
        return json.dumps({
            "success": True,
            "count": len(report.orphans),
            "orphans": [
                {
                    "name": o.name,
                    "kind": o.kind,
                    "value": o.value,
                    "location": f"{o.file}:{o.line}",
                }
                for o in report.orphans
            ],
        }, indent=2, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})


@app.tool(
    annotations=ToolAnnotations(
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    )
)
def get_token_impact(token_name: str, project_path: str = None) -> str:
    """Get impact analysis for a specific token."""
    from core.project_scanner import ProjectScanner
    from core.project_root import get_project_root
    
    try:
        root = get_project_root(Path(project_path) if project_path else None)
        scanner = ProjectScanner(root)
        report = scanner.scan(include_hardcoded=False)
        
        impact = next(
            (i for i in report.token_impacts if i.token_name == token_name),
            None,
        )
        
        if not impact:
            return json.dumps({
                "success": False,
                "error": f"Token '{token_name}' not found",
            })
        
        return json.dumps({
            "success": True,
            "token_name": impact.token_name,
            "token_value": impact.token_value,
            "token_kind": impact.token_kind,
            "token_location": impact.token_location,
            "total_impact": impact.total_impact,
            "affected_composables": impact.affected_composables,
            "usages": [
                {
                    "file": u.file,
                    "line": u.line,
                    "line_content": u.line_content,
                    "enclosing_composable": u.enclosing_composable,
                    "enclosing_line": u.enclosing_line,
                }
                for u in impact.usages
            ],
        }, indent=2, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})


@app.tool(
    annotations=ToolAnnotations(
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    )
)
def suggest_token_split(token_name: str = None, project_path: str = None) -> str:
    """Read-only analysis. Suggests splitting tokens used in multiple contexts."""
    from core.project_scanner import ProjectScanner
    from core.project_root import get_project_root
    try:
        root = get_project_root(Path(project_path) if project_path else None)
        scanner = ProjectScanner(root)
        report = scanner.scan(include_hardcoded=False)

        suggestions = report.split_suggestions
        if token_name:
            suggestions = [s for s in suggestions if s.token_name == token_name]

        return json.dumps({
            "success": True,
            "count": len(suggestions),
            "suggestions": [
                {
                    "token_name": s.token_name,
                    "token_value": s.token_value,
                    "token_kind": s.token_kind,
                    "contexts": {k: len(v) for k, v in s.contexts.items()},
                    "suggested_tokens": [
                        {
                            "name": st["name"],
                            "context": st["context"],
                            "hierarchy_path": st.get("hierarchy_path", []),
                            "composables": list(set(u["enclosing_composable"] for u in st["usages"])),
                            "usage_count": len(st["usages"]),
                        }
                        for st in s.suggested_tokens
                    ],
                }
                for s in suggestions
            ],
        }, indent=2, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})


@app.tool(
    annotations=ToolAnnotations(
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    )
)
def detect_conditional_variants(token_name: str = None) -> str:
    """Detect repeated conditional patterns."""
    from core.pattern_detector import PatternDetector
    try:
        detector = PatternDetector(PROJECT_ROOT)
        patterns = detector.detect(token_name)
        if token_name:
            patterns = [p for p in patterns if token_name.lower() in p.suggested_token_name.lower()]
        patterns.sort(key=lambda p: len(p.occurrences), reverse=True)
        return json.dumps({
            "success": True,
            "count": len(patterns),
            "patterns": [
                {
                    "pattern_id": p.pattern_id,
                    "template": p.template,
                    "occurrences": len(p.occurrences),
                    "suggested_token_name": p.suggested_token_name,
                    "suggested_token_value": p.suggested_token_value,
                    "affected_files": list(set(o.file for o in p.occurrences)),
                    "details": [
                        {"file": o.file, "line": o.line, "composable": o.enclosing_composable}
                        for o in p.occurrences[:20]
                    ],
                }
                for p in patterns
            ],
        }, indent=2, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})


@app.tool(
    annotations=ToolAnnotations(
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    )
)
def get_token_siblings(token_name: str) -> str:
    """Read-only. Returns semantically related tokens."""
    from core.token_linker import TokenLinker
    try:
        linker = TokenLinker(PROJECT_ROOT)
        siblings = linker.get_siblings(token_name)
        return json.dumps({
            "success": True,
            "token_name": token_name,
            "siblings": siblings,
            "count": len(siblings),
        }, indent=2, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})


@app.tool(
    annotations=ToolAnnotations(
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    )
)
def get_all_token_groups() -> str:
    """Read-only. Returns ALL semantic token groups in the project."""
    from core.token_linker import TokenLinker
    try:
        linker = TokenLinker(PROJECT_ROOT)
        groups = linker.detect_groups()
        return json.dumps({
            "success": True,
            "groups": groups,
            "count": len(groups),
        }, indent=2, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})


__all__ = [
    "find_token_by_color",
    "list_orphans",
    "get_token_impact",
    "suggest_token_split",
    "detect_conditional_variants",
    "get_token_siblings",
    "get_all_token_groups",
]

