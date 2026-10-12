"""VERONICA's context and state management system.

This module provides tools for auditing token usage and persisting 
conversation state via checkpoints, enabling long-term autonomy.
"""
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

class ContextDoctor:
    """Audits the conversation context to prevent token overflow."""
    
    def __init__(self, max_tokens: int = 64000):
        self.max_tokens = max_tokens

    def audit(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Estimates the current token usage and suggests a pruning strategy."""
        total_chars = 0
        message_costs = []

        for msg in messages:
            content = msg.get('content', '')
            if isinstance(content, list):
                text_parts = []
                for block in content:
                    if isinstance(block, dict):
                        text_parts.append(block.get('text', ''))
                    elif hasattr(block, 'text'):
                        text_parts.append(block.text)
                    else:
                        text_parts.append(str(block))
                text = ' '.join(text_parts)
            else:
                text = str(content)

            # Rough heuristic: 1 token ~ 4 chars
            cost = len(text) // 4
            total_chars += len(text)
            message_costs.append(cost)

        estimated_tokens = total_chars // 4
        
        return {
            "estimated_tokens": estimated_tokens,
            "usage_percent": (estimated_tokens / self.max_tokens) * 100,
            "needs_pruning": estimated_tokens > (self.max_tokens * 0.8),
            "suggested_action": "summarize" if estimated_tokens > (self.max_tokens * 0.8) else "none"
        }

class StateEncoder(json.JSONEncoder):
    """Handles SDK-specific objects that are not JSON serializable by default."""
    def default(self, obj):
        if hasattr(obj, '__dict__'):
            return obj.__dict__
        if hasattr(obj, 'model_dump'):
            return obj.model_dump()
        return super().default(obj)

class CheckpointManager:
    """Handles persistence of agent state to allow resuming complex tasks."""

    def __init__(self, storage_dir: Path):
        self.storage_dir = storage_dir
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def save_checkpoint(self, session_id: str, state: Dict[str, Any]) -> str:
        """Saves the current state to a JSON file."""
        timestamp = int(time.time())
        filename = f"checkpoint_{session_id}_{timestamp}.json"
        path = self.storage_dir / filename

        with open(path, 'w', encoding='utf-8') as f:
            json.dump(state, f, indent=2, cls=StateEncoder)

        return str(path)

    def load_latest_checkpoint(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Loads the most recent checkpoint for a given session."""
        files = sorted(self.storage_dir.glob(f"checkpoint_{session_id}_*.json"), reverse=True)
        if not files:
            return None
        
        with open(files[0], 'r', encoding='utf-8') as f:
            return json.load(f)

    def clear_old_checkpoints(self, session_id: str, keep_last: int = 5):
        """Removes old checkpoints to save disk space."""
        files = sorted(self.storage_dir.glob(f"checkpoint_{session_id}_*.json"), reverse=True)
        for old_file in files[keep_last:]:
            old_file.unlink()
