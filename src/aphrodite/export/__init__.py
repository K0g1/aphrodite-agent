"""Character and memory import/export system."""

from __future__ import annotations

import json
import logging
import os
import secrets
import shutil
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from ..character import validate_character_id
from ..config import Config
from ..db.database import Database

logger = logging.getLogger("aphrodite.export")


MAX_ARCHIVE_MEMBERS = 256
MAX_ARCHIVE_MEMBER_BYTES = 10 * 1024 * 1024
MAX_ARCHIVE_TOTAL_BYTES = 50 * 1024 * 1024


def _safe_extract_tar(tar: tarfile.TarFile, destination: Path) -> None:
    """Extract regular files/directories after validating the archive incrementally.

    Members are read one at a time (``tar.next()``) so a crafted archive with a
    huge member count is rejected early instead of being fully materialized in
    memory first.
    """
    validated: list[tuple[tarfile.TarInfo, PurePosixPath]] = []
    seen: set[str] = set()
    total_size = 0
    member = tar.next()
    while member is not None:
        if len(validated) >= MAX_ARCHIVE_MEMBERS:
            raise ValueError("archive contains too many members")
        name = member.name
        posix_path = PurePosixPath(name)
        parts = posix_path.parts
        normalized = posix_path.as_posix()
        if (
            not name
            or len(name) > 255
            or posix_path.is_absolute()
            or ".." in parts
            or "\\" in name
            or (parts and ":" in parts[0])
            or normalized in seen
            or not (member.isfile() or member.isdir())
        ):
            raise ValueError(f"unsafe archive member: {name!r}")
        if member.size < 0 or member.size > MAX_ARCHIVE_MEMBER_BYTES:
            raise ValueError(f"unsafe archive member size: {name!r}")
        total_size += member.size
        if total_size > MAX_ARCHIVE_TOTAL_BYTES:
            raise ValueError("archive expands beyond the size limit")
        seen.add(normalized)
        validated.append((member, posix_path))
        member = tar.next()

    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    for member, posix_path in validated:
        target = (root / Path(*posix_path.parts)).resolve(strict=False)
        if not target.is_relative_to(root):
            raise ValueError(f"unsafe archive member: {member.name!r}")
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        source = tar.extractfile(member)
        if source is None:
            raise ValueError(f"could not read archive member: {member.name!r}")
        with source, target.open("xb") as output:
            shutil.copyfileobj(source, output)


class ExportManager:
    """Handles character and data export/import."""

    def __init__(self, config: Config, db: Database | None = None):
        self.config = config
        self.db = db

    async def export_character(
        self, character_id: str, output_path: str | None = None, include_memories: bool = True
    ) -> str:
        """Export a character to a .aphrocard file."""
        validate_character_id(character_id)
        char_dir = self.config.characters_dir / character_id
        if not char_dir.exists():
            raise FileNotFoundError(f"Character '{character_id}' not found")

        # Create temp directory for export
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir) / character_id

            # Copy character files (skip symlinks so archives cannot smuggle
            # external file contents or escape the character directory).
            char_files = [f for f in char_dir.rglob("*.md") if f.is_file() and not f.is_symlink()]
            for f in char_files:
                rel_path = f.relative_to(char_dir)
                dest = tmp_path / "character" / rel_path
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dest)

            # Export memories
            memories_written = False
            if include_memories and self.db:
                memories = await self.db.fetch_all(
                    "SELECT * FROM memories WHERE status = 'active' ORDER BY created_at_utc DESC LIMIT 1000"
                )
                mem_file = tmp_path / "memories.json"
                mem_file.write_text(json.dumps([dict(r) for r in memories], indent=2))
                memories_written = True

            # Create manifest
            manifest = {
                "format": "aphrodite-character",
                "version": "1.0",
                "character_id": character_id,
                "exported_at": datetime.now(UTC).isoformat(),
                "files": [str(f.relative_to(char_dir)) for f in char_files],
                "includes_memories": memories_written,
                "license": "CC-BY-4.0",
            }
            (tmp_path / "manifest.json").write_text(json.dumps(manifest, indent=2))

            # Create archive (temp file + atomic rename so a mid-write failure
            # can never clobber an existing archive with a corrupt partial).
            if output_path:
                archive_path = Path(output_path)
            else:
                archive_path = self.config.data_path / f"{character_id}.aphrocard"

            archive_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_archive = archive_path.with_name(f".{archive_path.name}.tmp-{secrets.token_hex(4)}")
            try:
                with tarfile.open(tmp_archive, "w:gz") as tar:
                    tar.add(tmp_path, arcname=character_id)
                os.replace(tmp_archive, archive_path)
            finally:
                tmp_archive.unlink(missing_ok=True)

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
                _safe_extract_tar(tar, tmp_path)

            # Find character files
            extracted = list(tmp_path.rglob("**/character/identity.md"))
            if not extracted:
                # Try direct extraction
                extracted = list(tmp_path.rglob("identity.md"))

            if not extracted:
                raise ValueError(f"No character found in {archive_path}")

            if len(extracted) != 1:
                raise ValueError("Archive must contain exactly one character")
            char_src = extracted[0].parent

            # Locate the character root: <root>/<char_id>/character or a flat <root>.
            char_root = char_src.parent if char_src.name == "character" else char_src

            # Read character ID: manifest is authoritative, directory name is legacy fallback.
            manifest: dict | None = None
            manifest_file = char_root / "manifest.json"
            if manifest_file.exists():
                try:
                    loaded = json.loads(manifest_file.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        manifest = loaded
                except (json.JSONDecodeError, OSError):
                    manifest = None
            char_id = manifest.get("character_id") if manifest else None
            if not isinstance(char_id, str) or not char_id:
                if char_src.name == "character":
                    char_id = char_src.parent.name
                else:
                    # No manifest and no <id>/character layout: the id cannot be
                    # determined (a flat archive falls back to a random temp dir
                    # name, which would import under garbage).
                    raise ValueError(
                        "Archive has no manifest and no <character-id>/character "
                        "layout; cannot determine character id"
                    )
            validate_character_id(char_id)

            # Copy to characters directory via a staging dir so a mid-copy
            # failure leaves no partial character and the next import retry
            # is possible.
            dest_dir = self.config.characters_dir / char_id
            if dest_dir.exists():
                raise FileExistsError(f"Character '{char_id}' already exists")
            stage = self.config.characters_dir / f".tmp-{char_id}-{secrets.token_hex(4)}"
            try:
                stage.mkdir(parents=True)
                for f in char_src.rglob("*.md"):
                    if f.is_symlink() or not f.is_file():
                        continue
                    rel = f.relative_to(char_src)
                    dest = stage / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, dest)

                # Restore exported memories when the archive includes them.
                mem_file = char_root / "memories.json"
                if mem_file.exists() and self.db:
                    await self._import_memories(mem_file)

                stage.rename(dest_dir)
            except Exception:
                shutil.rmtree(stage, ignore_errors=True)
                raise

        return char_id

    async def _import_memories(self, mem_file: Path) -> None:
        """Import memories from an exported memories.json (best-effort per row)."""
        from datetime import datetime

        from ..types import new_id

        if self.db is None:
            return
        db = self.db

        try:
            mem_data = json.loads(mem_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.warning("Skipping unreadable memories.json during import")
            return
        if not isinstance(mem_data, list):
            return

        for row in mem_data:
            if not isinstance(row, dict) or not row.get("content"):
                continue
            try:
                raw_id = row.get("id")
                memory_id: str = raw_id if isinstance(raw_id, str) and raw_id else new_id()
                confidence = float(row.get("confidence", 0.9))
                importance = float(row.get("importance", 0.5))
            except (TypeError, ValueError):
                continue
            await db.save_memory(
                memory_id=memory_id,
                memory_type=str(row.get("memory_type", "fact")),
                content=str(row["content"]),
                confidence=min(1.0, max(0.0, confidence)),
                importance=min(1.0, max(0.0, importance)),
                sensitivity=str(row.get("sensitivity", "normal")),
                created_at=str(row.get("created_at_utc") or datetime.now(UTC).isoformat()),
                source_message_id=str(row.get("source_message_id") or ""),
            )

    async def export_memories(self, output_path: str | None = None) -> str:
        """Export all memories to a JSON file."""
        if not self.db:
            raise RuntimeError("Database not available")

        memories = await self.db.fetch_all("SELECT * FROM memories ORDER BY created_at_utc")

        if output_path:
            out = Path(output_path)
        else:
            out = (
                self.config.data_path
                / f"memories_export_{datetime.now(UTC).strftime('%Y%m%d')}.json"
            )

        data = {
            "exported_at": datetime.now(UTC).isoformat(),
            "count": len(memories),
            "memories": [dict(r) for r in memories],
        }
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp_out = out.with_name(f".{out.name}.tmp-{secrets.token_hex(4)}")
        try:
            tmp_out.write_text(json.dumps(data, indent=2), encoding="utf-8")
            os.replace(tmp_out, out)
        finally:
            tmp_out.unlink(missing_ok=True)
        return str(out)

    async def export_journal(self, output_path: str | None = None) -> str:
        """Export all journal entries to a JSON file."""
        if not self.db:
            raise RuntimeError("Database not available")

        entries = await self.db.fetch_all("SELECT * FROM journal_entries ORDER BY local_date")

        if output_path:
            out = Path(output_path)
        else:
            out = (
                self.config.data_path
                / f"journal_export_{datetime.now(UTC).strftime('%Y%m%d')}.json"
            )

        data = {
            "exported_at": datetime.now(UTC).isoformat(),
            "count": len(entries),
            "entries": [dict(r) for r in entries],
        }
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp_out = out.with_name(f".{out.name}.tmp-{secrets.token_hex(4)}")
        try:
            tmp_out.write_text(json.dumps(data, indent=2), encoding="utf-8")
            os.replace(tmp_out, out)
        finally:
            tmp_out.unlink(missing_ok=True)
        return str(out)

    async def list_characters(self) -> list[dict]:
        """List all available characters."""
        chars = []
        chars_dir = self.config.characters_dir
        if chars_dir.exists():
            for d in sorted(chars_dir.iterdir()):
                if d.is_dir():
                    files = list(d.rglob("*.md"))
                    chars.append(
                        {
                            "id": d.name,
                            "files": len(files),
                            "size_kb": sum(f.stat().st_size for f in files) // 1024,
                        }
                    )
        return chars
