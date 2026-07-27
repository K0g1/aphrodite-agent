"""Character and memory import/export system."""

from __future__ import annotations

import json
import shutil
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..character import Character, parse_character
from ..db.database import Database
from ..config import Config


class ExportManager:
    """Handles character and data export/import."""

    def __init__(self, config: Config, db: Database | None = None):
        self.config = config
        self.db = db

    async def export_character(self, character_id: str,
                                output_path: str | None = None,
                                include_memories: bool = True) -> str:
        """Export a character to a .aphrocard file."""
        char_dir = self.config.characters_dir / character_id
        if not char_dir.exists():
            raise FileNotFoundError(f"Character '{character_id}' not found")

        # Create temp directory for export
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir) / character_id

            # Copy character files
            char_files = list(char_dir.rglob("*.md"))
            for f in char_files:
                rel_path = f.relative_to(char_dir)
                dest = tmp_path / "character" / rel_path
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dest)

            # Export memories
            if include_memories and self.db:
                memories = await self.db.fetch_all(
                    "SELECT * FROM memories WHERE status = 'active' ORDER BY created_at_utc DESC LIMIT 1000"
                )
                mem_file = tmp_path / "memories.json"
                mem_file.write_text(json.dumps([dict(r) for r in memories], indent=2))

            # Create manifest
            manifest = {
                "format": "aphrodite-character",
                "version": "1.0",
                "character_id": character_id,
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "files": [str(f.relative_to(char_dir)) for f in char_files],
                "includes_memories": include_memories,
                "license": "CC-BY-4.0",
            }
            (tmp_path / "manifest.json").write_text(json.dumps(manifest, indent=2))

            # Create archive
            if output_path:
                archive_path = Path(output_path)
            else:
                archive_path = self.config.data_path / f"{character_id}.aphrocard"

            with tarfile.open(archive_path, "w:gz") as tar:
                tar.add(tmp_path, arcname=character_id)

        return str(archive_path)

    async def import_character(self, archive_path: str) -> str:
        """Import a character from a .aphrocard file."""
        path = Path(archive_path)
        if not path.exists():
            raise FileNotFoundError(f"Archive not found: {archive_path}")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            # Extract archive
            with tarfile.open(path, "r:gz") as tar:
                tar.extractall(tmp_path)

            # Find character files
            extracted = list(tmp_path.rglob("**/character/identity.md"))
            if not extracted:
                # Try direct extraction
                extracted = list(tmp_path.rglob("identity.md"))

            if not extracted:
                raise ValueError(f"No character found in {archive_path}")

            char_src = extracted[0].parent

            # Read character ID
            char_id = char_src.parent.name if char_src.parent.name != "character" else char_src.parent.parent.name

            # Copy to characters directory
            dest_dir = self.config.characters_dir / char_id
            dest_dir.mkdir(parents=True, exist_ok=True)

            for f in char_src.rglob("*.md"):
                rel = f.relative_to(char_src)
                dest = dest_dir / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dest)

        return char_id

    async def export_memories(self, output_path: str | None = None) -> str:
        """Export all memories to a JSON file."""
        if not self.db:
            raise RuntimeError("Database not available")

        memories = await self.db.fetch_all(
            "SELECT * FROM memories ORDER BY created_at_utc"
        )

        if output_path:
            out = Path(output_path)
        else:
            out = self.config.data_path / f"memories_export_{datetime.now(timezone.utc).strftime('%Y%m%d')}.json"

        data = {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "count": len(memories),
            "memories": [dict(r) for r in memories],
        }
        out.write_text(json.dumps(data, indent=2))
        return str(out)

    async def export_journal(self, output_path: str | None = None) -> str:
        """Export all journal entries to a JSON file."""
        if not self.db:
            raise RuntimeError("Database not available")

        entries = await self.db.fetch_all(
            "SELECT * FROM journal_entries ORDER BY local_date"
        )

        if output_path:
            out = Path(output_path)
        else:
            out = self.config.data_path / f"journal_export_{datetime.now(timezone.utc).strftime('%Y%m%d')}.json"

        data = {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "count": len(entries),
            "entries": [dict(r) for r in entries],
        }
        out.write_text(json.dumps(data, indent=2))
        return str(out)

    async def list_characters(self) -> list[dict]:
        """List all available characters."""
        chars = []
        chars_dir = self.config.characters_dir
        if chars_dir.exists():
            for d in sorted(chars_dir.iterdir()):
                if d.is_dir():
                    files = list(d.rglob("*.md"))
                    chars.append({
                        "id": d.name,
                        "files": len(files),
                        "size_kb": sum(f.stat().st_size for f in files) // 1024,
                    })
        return chars
