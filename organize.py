#!/usr/bin/env python3
"""Move finished downloads into a music library, replacing lossy copies.
BitTorrent and Soulseek clients drop files in whatever folder layout the
uploader used. A library-oriented server (Navidrome, Airsonic, etc.) wants a
predictable tree, and a track that exists twice (128 kbps mp3 plus a new
FLAC) shows up as a duplicate in every client. This script reads the real
tags, files each track as Artist/Album/NN - Title.ext, and deletes the lossy
version it supersedes.

Safety: refuses to delete anything unless the replacement is verifiably better
(lossless beating lossy, or a materially higher bitrate), and never deletes an
original until the new file is in place.

Usage:
    organize.py                                    # dry run, default paths
    organize.py --go                                # move files
    organize.py --downloads DIR --library DIR --go
"""
import argparse
import pathlib
import re
import shutil
import unicodedata

from mutagen import File as MutagenFile

AUDIO_SUFFIXES = {".flac", ".wav", ".ape", ".alac", ".aiff",
                  ".mp3", ".m4a", ".ogg", ".opus"}
LOSSLESS_SUFFIXES = {".flac", ".wav", ".ape", ".alac", ".aiff"}
# A lossy file at or above this bitrate is treated as "good enough" and is not
# churned for a lossless replacement; anything below it still gets replaced.
GOOD_ENOUGH_BITRATE = 256_000
# Filesystem-hostile characters, replaced rather than dropped.
UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def clean(text, fallback="Unknown"):
    text = (text or "").strip()
    if not text:
        return fallback
    text = UNSAFE.sub("-", text)
    text = re.sub(r"\s+", " ", text).strip(". ")
    return text[:120] or fallback


def key_of(artist, title):
    """Normalised identity for matching the same song across files."""
    joined = f"{artist} {title}"
    joined = unicodedata.normalize("NFKD", joined)
    joined = "".join(c for c in joined if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", joined.lower()).strip()


def read(path):
    """Return a dict describing the audio file, or None if unreadable."""
    try:
        audio = MutagenFile(path, easy=True)
    except Exception:
        return None
    if audio is None or audio.info is None:
        return None
    tags = audio.tags or {}

    def first(*names):
        for name in names:
            value = tags.get(name)
            if value:
                return value[0] if isinstance(value, list) else str(value)
        return None

    artist = first("albumartist", "artist")
    title = first("title")
    if not artist or not title:
        return None
    track = first("tracknumber") or "0"
    track = re.split(r"[/-]", str(track))[0].strip()
    return {
        "path": path,
        "artist": artist,
        "title": title,
        "album": first("album") or "Unknown Album",
        "track": int(track) if track.isdigit() else 0,
        "bitrate": getattr(audio.info, "bitrate", 0) or 0,
        "lossless": path.suffix.lower() in LOSSLESS_SUFFIXES,
    }


def from_path(path):
    """Guess metadata from folder/file names when a file carries no tags.

    Soulseek and torrent shares are usually laid out as
    "Artist - Album [FLAC]/01. Title.flac", which is enough to file the track
    correctly instead of discarding it.
    """
    stem = path.stem
    # Strip a leading track number: "02. Title", "02 - Title", "02 Title".
    match = re.match(r"^\s*(\d{1,3})\s*[.\-_ ]\s*(.+)$", stem)
    track, title = (int(match.group(1)), match.group(2)) if match else (0, stem)
    parent = path.parent.name
    # "Artist - Album (1999) [FLAC]" -> artist, album
    parent = re.sub(r"\[[^\]]*\]|\{[^}]*\}", "", parent).strip()
    if " - " in parent:
        artist, album = parent.split(" - ", 1)
    else:
        artist, album = parent, parent
    album = re.sub(r"\(\d{4}\)|\b(19|20)\d{2}\b", "", album).strip(" -_")
    if not artist.strip() or not title.strip():
        return None
    try:
        audio = MutagenFile(path, easy=True)
        bitrate = getattr(audio.info, "bitrate", 0) or 0
    except Exception:
        bitrate = 0
    return {
        "path": path,
        "artist": artist.strip(),
        "title": title.strip(),
        "album": album or "Unknown Album",
        "track": track,
        "bitrate": bitrate,
        "lossless": path.suffix.lower() in LOSSLESS_SUFFIXES,
    }


def is_incomplete(path):
    """True when a file is still being written by a downloader.

    Two traps, both learned the hard way:
      * aria2 PRE-ALLOCATES each selected file at its final size before any
        data arrives, so checking the size finds a "22 MB" FLAC that is all
        zeros. Compare blocks actually on disk against the apparent size.
      * a `.aria2` control file sits beside the payload until the transfer
        finishes.
    """
    stat = path.stat()
    if stat.st_size == 0:
        return True
    # st_blocks counts 512-byte blocks really allocated; a sparse placeholder
    # has far fewer than its size implies.
    if stat.st_blocks * 512 < stat.st_size * 0.9:
        return True
    if path.with_suffix(path.suffix + ".aria2").exists():
        return True
    # Only the file's OWN directory matters: scanning the parent would let one
    # unfinished download block every unrelated download sitting beside it.
    try:
        if any(path.parent.glob("*.aria2")):
            return True
    except OSError:
        pass
    # Final guard: a real audio file must be parseable.
    try:
        if MutagenFile(path) is None:
            return True
    except Exception:
        return True
    return False


def index_library(library):
    """Map every track already in the library by normalised artist+title."""
    existing = {}
    for path in library.rglob("*"):
        if path.suffix.lower() not in AUDIO_SUFFIXES:
            continue
        info = read(path)
        if info:
            existing.setdefault(key_of(info["artist"], info["title"]), []).append(info)
    return existing


def better(new, old, good_enough_bitrate):
    """True when `new` is a genuine upgrade over `old`."""
    if old["lossless"] or old["bitrate"] >= good_enough_bitrate:
        return False
    if new["lossless"]:
        return True
    return new["bitrate"] > old["bitrate"] * 1.2


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--downloads", default="~/Music/downloads",
                    help="folder to scan for finished downloads (default: %(default)s)")
    ap.add_argument("--library", default="~/Music/library",
                    help="target library folder (default: %(default)s)")
    ap.add_argument("--go", action="store_true", help="actually move/delete files (default: dry run)")
    ap.add_argument("--good-enough-bitrate", type=int, default=GOOD_ENOUGH_BITRATE,
                    help="lossy bitrate (bps) at or above which a file is not replaced (default: %(default)s)")
    ap.add_argument("--restart-hint",
                    help="command to print as a reminder after a real move, e.g. "
                         "'docker restart navidrome'")
    return ap


def main():
    args = build_parser().parse_args()
    downloads = pathlib.Path(args.downloads).expanduser()
    library = pathlib.Path(args.library).expanduser()
    go = args.go

    existing = index_library(library)
    print(f"library holds {sum(len(v) for v in existing.values())} tracks "
          f"({'MOVING' if go else 'dry run'})\n")

    # Collect first, so that several copies of the same song downloaded from
    # different sources compete against each other and only the best one moves.
    incoming = {}
    unreadable = 0
    for path in sorted(downloads.rglob("*")):
        if path.suffix.lower() not in AUDIO_SUFFIXES or not path.is_file():
            continue
        if is_incomplete(path):
            print(f"  ~  still downloading, skipped: {path.name}")
            continue
        info = read(path)
        if info is None:
            # Untagged files still carry artist/album in their folder names;
            # fall back to that rather than abandoning the file.
            info = from_path(path)
        if info is None:
            unreadable += 1
            print(f"  ?  no tags and unguessable, left alone: {path.name}")
            continue
        key = key_of(info["artist"], info["title"])
        current = incoming.get(key)
        if current is None or better(info, current, args.good_enough_bitrate):
            incoming[key] = info

    moved = replaced = skipped = 0
    for key, info in sorted(incoming.items()):
        path = info["path"]
        duplicates = existing.get(key, [])
        superseded = [old for old in duplicates if better(info, old, args.good_enough_bitrate)]
        if duplicates and not superseded:
            skipped += 1
            print(f"  =  already have as good or better: "
                  f"{info['artist']} - {info['title']}")
            continue

        folder = library / clean(info["artist"]) / clean(info["album"])
        name = (f"{info['track']:02d} - {clean(info['title'])}{path.suffix.lower()}"
                if info["track"] else f"{clean(info['title'])}{path.suffix.lower()}")
        target = folder / name

        label = "LOSSLESS" if info["lossless"] else f"{info['bitrate'] // 1000}k"
        action = "replace" if superseded else "add"
        print(f"  {action:7} {label:9} {info['artist']} - {info['title']}")

        if not go:
            continue
        folder.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(target))
        moved += 1
        # Only now that the upgrade is safely in place, drop what it replaced.
        for old in superseded:
            if old["path"] != target and old["path"].exists():
                sidecar = old["path"].with_suffix(".lrc")
                old["path"].unlink()
                if sidecar.exists() and not target.with_suffix(".lrc").exists():
                    sidecar.rename(target.with_suffix(".lrc"))
                replaced += 1

    print(f"\nmoved {moved} | lossy copies removed {replaced} | "
          f"skipped {skipped} | unreadable {unreadable}")
    if go and args.restart_hint:
        print(f"\nnow run:  {args.restart_hint}")


if __name__ == "__main__":
    main()
