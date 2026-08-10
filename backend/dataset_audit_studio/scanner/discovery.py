from __future__ import annotations

import os
import stat
from pathlib import Path, PurePosixPath

from dataset_audit_studio.runtime import PROJECT_ROOT
from dataset_audit_studio.scanner.config import ScanConfig
from dataset_audit_studio.scanner.types import DiscoveredMedia, DiscoveryResult

STATIC_IMAGE_EXTENSIONS = frozenset(
    {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".avif"}
)
ANIMATION_EXTENSIONS = frozenset({".gif"})
VIDEO_EXTENSIONS = frozenset({".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v"})
MEDIA_EXTENSIONS = STATIC_IMAGE_EXTENSIONS | ANIMATION_EXTENSIONS | VIDEO_EXTENSIONS


class SourceLayoutError(ValueError):
    """Raised when a built-in profile source is not flat or one level deep."""


def validate_builtin_profile_input_layout(
    source_root: Path,
    config: ScanConfig,
    *,
    project_root: Path | None = None,
) -> None:
    """Validate a built-in profile source without changing discovery semantics."""
    layout_config = config.model_copy(update={"recursive": True})
    discovery = discover_media(source_root, layout_config, project_root=project_root)
    root_media: list[str] = []
    one_level_media: list[str] = []
    nested_media: list[str] = []
    for item in discovery.items:
        depth = len(PurePosixPath(item.relative_path).parts)
        if depth == 1:
            root_media.append(item.relative_path)
        elif depth == 2:
            one_level_media.append(item.relative_path)
        else:
            nested_media.append(item.relative_path)

    if nested_media:
        example = min(nested_media, key=lambda value: (value.casefold(), value))
        first_part = PurePosixPath(example).parts[0]
        resolution_hint = (
            " Choose the specific resolution directory instead of its parent."
            if first_part.isdecimal()
            else ""
        )
        raise SourceLayoutError(
            "Source layout error [nested_media]: media may be at most one directory "
            f"below source_root (maximum depth 1); nested example: {example}."
            f"{resolution_hint}"
        )

    if root_media and one_level_media:
        root_example = min(root_media, key=lambda value: (value.casefold(), value))
        one_level_example = min(
            one_level_media,
            key=lambda value: (value.casefold(), value),
        )
        raise SourceLayoutError(
            "Source layout error [mixed_media]: media cannot be mixed between "
            "source_root and first-level directories; "
            f"root example: {root_example}; first-level example: {one_level_example}"
        )


def _is_reparse(stat_result: os.stat_result) -> bool:
    attributes = getattr(stat_result, "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _kind(extension: str) -> str:
    if extension in VIDEO_EXTENSIONS:
        return "video"
    if extension in ANIMATION_EXTENSIONS:
        return "animation"
    return "image"


def _stable_key(path: Path) -> tuple[str, str]:
    value = path.as_posix()
    return value.casefold(), value


def discover_media(
    source_root: Path,
    config: ScanConfig,
    *,
    project_root: Path | None = None,
) -> DiscoveryResult:
    source_root = source_root.resolve(strict=True)
    project_root = (project_root or PROJECT_ROOT).resolve(strict=False)
    excluded_names = set(config.excluded_directory_names)
    project_exclusions = {
        (project_root / name).resolve(strict=False)
        for name in (".runtime", ".venv", ".setup", "models", "data", "output")
    }
    media_paths: list[Path] = []
    ignored_reparse = 0
    ignored_directories = 0

    stack = [source_root]
    while stack:
        directory = stack.pop()
        with os.scandir(directory) as entries:
            ordered = sorted(entries, key=lambda entry: (entry.name.casefold(), entry.name))
        for entry in ordered:
            path = Path(entry.path)
            try:
                entry_stat = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if entry.is_symlink() or _is_reparse(entry_stat):
                ignored_reparse += 1
                continue
            if entry.is_dir(follow_symlinks=False):
                if (
                    path.name.casefold() in excluded_names
                    or path.resolve(strict=False) in project_exclusions
                ):
                    ignored_directories += 1
                    continue
                if config.recursive:
                    stack.append(path)
                continue
            if not entry.is_file(follow_symlinks=False):
                continue
            extension = path.suffix.casefold()
            if extension in MEDIA_EXTENSIONS:
                media_paths.append(path)

    media_paths.sort(key=lambda path: _stable_key(path.relative_to(source_root)))

    items: list[DiscoveredMedia] = []
    for path in media_paths:
        relative = path.relative_to(source_root)
        item_stat = path.stat()
        parts = relative.parts
        artist_scope = parts[0] if len(parts) > 1 else "__root__"
        items.append(
            DiscoveredMedia(
                absolute_path=path,
                relative_path=relative.as_posix(),
                source_size=item_stat.st_size,
                source_mtime_ns=item_stat.st_mtime_ns,
                media_kind_hint=_kind(path.suffix.casefold()),
                artist_scope=artist_scope,
            )
        )

    return DiscoveryResult(
        items=tuple(items),
        ignored_reparse_count=ignored_reparse,
        ignored_directory_count=ignored_directories,
    )
