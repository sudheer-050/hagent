"""Bounded, local-only skill bundle loading. Never extract or execute files."""

from pathlib import Path, PurePosixPath, PureWindowsPath
import stat
import zipfile
import click

MAX_BYTES = 5 * 1024 * 1024
MAX_FILES = 200


def safe_name(name):
    name = name.replace("\\", "/")
    path = PurePosixPath(name)
    if path.is_absolute() or PureWindowsPath(name).drive or ".." in path.parts or ":" in name:
        raise click.ClickException("Unsafe skill filename")
    return path.as_posix()


def read_source(source):
    if "://" in source or source.startswith(("\\\\", "//")):
        raise click.ClickException("Skill imports accept local directories or ZIP files only")
    root = Path(source)
    if root.is_symlink() or not root.exists():
        raise click.ClickException("Skill source missing or a symbolic link")
    files = []
    total = 0

    def add(name, data):
        nonlocal total
        total += len(data)
        if len(files) >= MAX_FILES or total > MAX_BYTES:
            raise click.ClickException("Skill bundle exceeds 200 files or 5 MiB")
        files.append((safe_name(name), data.decode("utf-8")))

    try:
        if root.is_dir():
            for path in sorted(root.rglob("*")):
                if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
                    raise click.ClickException("Skill links are not allowed")
                if path.is_file():
                    if path.stat().st_size + total > MAX_BYTES:
                        raise click.ClickException("Skill bundle exceeds 5 MiB")
                    add(path.relative_to(root).as_posix(), path.read_bytes())
        elif zipfile.is_zipfile(root):
            with zipfile.ZipFile(root) as archive:
                names = set()
                for info in archive.infolist():
                    name = safe_name(info.filename)
                    if stat.S_ISLNK(info.external_attr >> 16):
                        raise click.ClickException("Skill links are not allowed")
                    if info.is_dir():
                        continue
                    if name.casefold() in names or info.file_size + total > MAX_BYTES:
                        raise click.ClickException("Duplicate filename or oversized skill bundle")
                    names.add(name.casefold())
                    add(name, archive.read(info))
        else:
            raise click.ClickException("Use a local directory or ZIP archive")
    except (OSError, UnicodeError, zipfile.BadZipFile) as exc:
        raise click.ClickException(f"Cannot read skill bundle: {exc}") from exc
    if not files:
        raise click.ClickException("Skill bundle is empty")
    return files, None
