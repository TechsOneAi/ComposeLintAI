# -*- coding: utf-8 -*-
"""
أدوات التوكنز الأساسية لخادم MCP (Core Token Tools).
Contains add_token, get_naming_convention, list_tokens, and set_token.
Re-exports advanced/specific inspection tools from token_specific_tools.
"""

import sys
import json
import asyncio
from pathlib import Path
from typing import Optional, List, Dict, Any

from mcp.app import app, log, PROJECT_ROOT, state_manager
from mcp.types import ToolAnnotations
from mcp.tools.misc import notify_bridge_server, notify_agent_lifecycle, run_async, send_refresh_signal

from core.token_migrator import TokenMigrator
from core.theme_indexer import get_index
from core.project_root import get_project_root
from core.project_scanner import ProjectScanner
from core.write_lock import acquire_write_lock

# استيراد وإعادة تصدير الأدوات المتخصصة (Re-export specific tools)
from .token_specific_tools import (
    find_token_by_color,
    list_orphans,
    get_token_impact,
    suggest_token_split,
    detect_conditional_variants,
    get_token_siblings,
    get_all_token_groups,
)


@app.tool(
    annotations=ToolAnnotations(
        read_only_hint=False,
        destructive_hint=True,
        idempotent_hint=False,
        open_world_hint=False,
    )
)
def add_token(
    file: str,
    line: int,
    value: str,
    token_name: str,
    token_type: str,
    preferred_token_file: str = None,
    force_create: bool = False,
) -> str:
    """Convert a hardcoded value to a design token."""
    from core.token_migrator import TokenMigrator
    from core.project_root import get_project_root
    p_root = get_project_root()

    if not preferred_token_file:
        if token_type == "color":
            for cand in ["DesignSystem.kt", "AppColors.kt", "Theme.kt", "Color.kt", "Colors.kt"]:
                if list(p_root.rglob(cand)):
                    preferred_token_file = cand
                    break
        elif token_type == "dimension":
            for cand in ["AppDimens.kt", "DesignSystem.kt", "Dimens.kt", "Dimensions.kt", "Spacing.kt"]:
                if list(p_root.rglob(cand)):
                    preferred_token_file = cand
                    break

    try:
        migrator = TokenMigrator(p_root)
        success, message, info = migrator.migrate(
            file_name=file,
            line=line,
            value=value,
            token_name=token_name,
            token_type=token_type,
            preferred_token_file=preferred_token_file,
            force_create=force_create,
        )
        
        if success:
            try:
                from core.agent_session_tracker import SESSION_TRACKER
                SESSION_TRACKER.record_touched_file(file, p_root)
                if preferred_token_file:
                    SESSION_TRACKER.record_touched_file(preferred_token_file, p_root)
            except Exception:
                pass

            state_manager.events.log(
                "token_added",
                {
                    "token_name": token_name,
                    "value": value,
                    "type": token_type,
                    "file": file,
                    "line": line,
                },
                source="agent",
            )
            state_manager.save_all()
            
            try:
                run_async(notify_bridge_server(token_name, value))
            except Exception:
                pass
            try:
                send_refresh_signal()
            except Exception:
                pass
            
            return json.dumps({
                "success": True,
                "message": message,
                "info": info,
                "warning": "force_create was used. This bypassed duplicate detection." if force_create else None,
            }, indent=2, ensure_ascii=False)
        else:
            error_type = "unknown"
            if "Token name invalid" in message:
                error_type = "semantic_validation"
            elif "already exists as" in message:
                error_type = "same_value_different_name"
            elif "may cause semantic confusion" in message:
                error_type = "same_base_different_value"
            elif "already exist" in message:
                error_type = "duplicate"
            
            if info and "safe_alternative" in info:
                info["recommended_name"] = info["safe_alternative"]

            recommended_name = None
            if info:
                recommended_name = info.get("safe_alternative") or info.get("recommended_name") or info.get("suggested_name")

            if error_type in ("same_value_different_name", "duplicate") or "already exist" in message:
                rec_target = recommended_name
                if not rec_target and info:
                    if "existing_tokens" in info and info["existing_tokens"]:
                        rec_target = info["existing_tokens"][0]
                    elif "existing_token" in info:
                        rec_target = info["existing_token"]
                if not rec_target:
                    rec_target = token_name
                warn_msg = f"DUPLICATE_TOKEN_WARNING: Value '{value}' already exists as '{rec_target}'. Consider reusing '{rec_target}' instead of creating '{token_name}'."
                try:
                    from mcp.network_client import send_agent_warning
                    send_agent_warning("Duplicate Token Intercepted", warn_msg)
                except Exception:
                    pass
                return json.dumps({
                    "success": True,
                    "warning": warn_msg,
                    "message": warn_msg,
                    "info": info,
                    "recommended_name": rec_target,
                    "error_type": error_type,
                }, indent=2, ensure_ascii=False)

            return json.dumps({
                "success": False,
                "error": message,
                "error_type": error_type,
                "info": info,
                "recommended_name": recommended_name,
            }, indent=2, ensure_ascii=False)
    
    except Exception as e:
        return json.dumps({
            "success": False,
            "error": str(e),
        }, indent=2, ensure_ascii=False)


@app.tool(
    annotations=ToolAnnotations(
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    )
)
def get_naming_convention(token_type: str = "dimension") -> str:
    """Read-only analysis. Detects if project uses camelCase, snake_case, or PascalCase for tokens."""
    from core.token_migrator import TokenMigrator
    try:
        migrator = TokenMigrator(PROJECT_ROOT)
        convention = migrator.detect_naming_convention(token_type)
        token_files = migrator.find_all_token_files(token_type)
        
        return json.dumps({
            "success": True,
            "token_type": token_type,
            "style": convention["style"],
            "total_tokens": convention.get("total_count", 0),
            "examples": convention["examples"],
            "common_suffixes": convention.get("common_suffixes", []),
            "token_files": [
                str(f.relative_to(PROJECT_ROOT)) for f in token_files
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
def list_tokens(project_path: str = None) -> str:
    """List all design tokens in the project."""
    from core.project_scanner import ProjectScanner
    from core.project_root import get_project_root
    
    try:
        root = get_project_root(Path(project_path) if project_path else None)
        scanner = ProjectScanner(root)
        report = scanner.scan()
        
        return json.dumps({
            "success": True,
            "summary": report.summary(),
            "registered_tokens": [
                {
                    "name": t.name,
                    "kind": t.kind,
                    "value": t.value,
                    "location": f"{t.file}:{t.line}",
                    "usage_count": t.usage_count,
                }
                for t in report.registered_tokens
            ],
            "hardcoded_values": [
                {
                    "value": h.value,
                    "kind": h.kind,
                    "file": h.file,
                    "line": h.line,
                    "location": f"{h.file}:{h.line}",
                    "suggestion": h.suggestion_name,
                    "category": h.category,
                }
                for h in report.hardcoded_values
            ],
            "duplicates": [
                {
                    "value": d.value,
                    "kind": d.kind,
                    "tokens": d.tokens,
                    "locations": d.locations,
                }
                for d in report.duplicates
            ],
            "orphans": [
                {
                    "name": o.name,
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
        read_only_hint=False,
        destructive_hint=True,
        idempotent_hint=False,
        open_world_hint=False,
    )
)
def set_token(token_name: str, value: str) -> str:
    """DESTRUCTIVE: Modifies .kt files. Surgically edit a design token value in source code."""
    with acquire_write_lock():
        try:
            started = False
            try:
                started = asyncio.run(notify_agent_lifecycle("agent_start"))
                print(f"[MCP] agent_start notified: {started}", file=sys.stderr)
            except Exception as e:
                print(f"[MCP] agent_start failed: {e}", file=sys.stderr)
            
            from core.state_editor import update_theme_color, update_token_default
            val_clean = value.strip()
            is_color = (
                val_clean.startswith(('#', '0x', '0X')) or
                val_clean.startswith('Color(') or
                val_clean.startswith('Color.') or
                token_name.startswith('AppColors.')
            )

            if is_color:
                color_val = val_clean
                if color_val.startswith('Color(') and color_val.endswith(')'):
                    color_val = color_val[6:-1].strip()
                color_key = token_name.split('.')[-1] if '.' in token_name else token_name
                ok, msg, info = update_theme_color(color_key, color_val)
            else:
                ok, msg, info = update_token_default(token_name, value)
            
            if not ok:
                state_manager.failures.record(
                    operation="set_token",
                    token=token_name,
                    location="unknown",
                    error=msg,
                    severity="error",
                )
                state_manager.save_all()
                
                try:
                    asyncio.run(notify_agent_lifecycle("agent_end"))
                except Exception:
                    pass
                
                err_dict = {"success": False, "error": msg}
                if isinstance(info, dict) and "error_code" in info:
                    err_dict["error_code"] = info["error_code"]
                return json.dumps(err_dict, indent=2, ensure_ascii=False)
            
            state_manager.history.record_value(
                token=token_name,
                old_value="",
                new_value=value,
                source="agent",
            )
            state_manager.events.log(
                "mcp_set_token",
                {"token": token_name, "value": value},
                source="agent",
            )
            state_manager.save_all()

            try:
                from core.agent_session_tracker import SESSION_TRACKER
                if isinstance(info, dict):
                    f_path = info.get("file") or info.get("file_path")
                    if f_path:
                        SESSION_TRACKER.record_touched_file(f_path)
            except Exception:
                pass

            dup_warning = None
            try:
                if is_color:
                    idx = get_index()
                    existing_match = idx.find_token_by_color(value)
                    short_token = token_name.split('.')[-1]
                    if existing_match and existing_match.lower() != short_token.lower():
                        dup_warning = f"DUPLICATE_TOKEN_WARNING: Color value '{value}' is already declared as '{existing_match}'. Consider reusing '{existing_match}' to avoid redundancy."
            except Exception:
                pass
            
            bridge_status = {"bridge_connected": False, "device_connected": False, "forwarded_to": 0}
            try:
                notify_res = asyncio.run(
                    notify_bridge_server(token_name, value)
                )
                if isinstance(notify_res, dict):
                    bridge_status = notify_res
                elif notify_res is True:
                    bridge_status = {"bridge_connected": True, "device_connected": True, "forwarded_to": 1}
            except Exception as e:
                print(f"[MCP] notify error: {e}", file=sys.stderr)

            device_connected = bridge_status.get("device_connected", False)
            live_applied = device_connected and ok
            
            try:
                send_refresh_signal()
            except Exception as e:
                print(f"[MCP] refresh notify error: {e}", file=sys.stderr)

            ended = False
            try:
                ended = asyncio.run(notify_agent_lifecycle("agent_end"))
                print(f"[MCP] agent_end notified: {ended}", file=sys.stderr)
            except Exception as e:
                print(f"[MCP] agent_end failed: {e}", file=sys.stderr)

            warning_msg = None
            if dup_warning:
                warning_msg = dup_warning
                try:
                    from mcp.network_client import send_agent_warning
                    send_agent_warning("Duplicate Token Intercepted", dup_warning)
                except Exception:
                    pass
            elif not device_connected:
                warning_msg = "Code modified on disk, but live preview failed because no connected device was found."

            return json.dumps({
                "success": True,
                "token": token_name,
                "value": value,
                "disk_updated": True,
                "device_connected": device_connected,
                "live_applied": live_applied,
                "notified_live": device_connected,
                "warning": warning_msg,
                "agent_started": started,
                "agent_ended": ended,
                "auto_wrapped": info.get("auto_wrapped", False) if isinstance(info, dict) else False,
            }, indent=2, ensure_ascii=False)
        
        except Exception as e:
            state_manager.failures.record(
                operation="set_token",
                token=token_name,
                location="unknown",
                error=str(e),
                severity="error",
            )
            state_manager.save_all()
            
            try:
                asyncio.run(notify_agent_lifecycle("agent_end"))
            except Exception:
                pass
            
            return json.dumps({"error": str(e)})


__all__ = [
    "add_token",
    "get_naming_convention",
    "list_tokens",
    "set_token",
    "find_token_by_color",
    "list_orphans",
    "get_token_impact",
    "suggest_token_split",
    "detect_conditional_variants",
    "get_token_siblings",
    "get_all_token_groups",
]
