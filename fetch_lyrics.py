#!/usr/bin/env python3
"""Fetch synced lyrics (.lrc) from LRCLIB for every track in a music library.

Servers like Navidrome serve a track's lyrics from a sidecar .lrc file sitting
next to the audio file (or from embedded tags). LRCLIB is a free, no-auth,
community lyrics database that indexes by artist/title/album/duration.

Idempotent: a track that already has a .lrc is skipped, so re-running only
fills the gaps. Falls back to plain (unsynced) lyrics when no synced version
exists.

Usage:  fetch-lyrics [library_path] [--user-agent NAME/VERSION (URL)]
"""
import argparse
import json
import pathlib
import time
import urllib.error
import urllib.parse
import urllib.request

from mutagen import File as MutagenFile

AUDIO_EXTENSIONS = {".mp3", ".flac", ".m4a", ".ogg", ".opus", ".wav"}
API = "https://lrclib.net/api/get"
# LRCLIB asks clients to identify themselves with a real contact/project URL.
DEFAULT_USER_AGENT = "navidrome-tools-fetch-lyrics/1.0 (https://github.com/machado-vitor/navidrome-tools)"


def read_tags(path):
    """Return (artist, title, album, duration_seconds) or None if unreadable."""
    try:
        audio = MutagenFile(path, easy=True)
    except Exception:
        return None
    if audio is None:
        return None
    tags = audio.tags or {}

    def first(*keys):
        for key in keys:
            value = tags.get(key)
            if value:
                return value[0] if isinstance(value, list) else str(value)
        return None

    artist = first("artist", "albumartist")
    title = first("title")
    album = first("album")
    duration = int(audio.info.length) if audio.info else None
    if not artist or not title:
        return None
    return artist, title, album, duration


def fetch(artist, title, album, duration, user_agent):
    """Query LRCLIB; return (lyrics_text, is_synced) or (None, False)."""
    params = {"artist_name": artist, "track_name": title}
    if album:
        params["album_name"] = album
    if duration:
        params["duration"] = duration
    url = f"{API}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": user_agent})
    # LRCLIB is a free service and answers 503 when hit too fast; back off and
    # retry rather than aborting a run that may cover thousands of tracks.
    for attempt in range(5):
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                data = json.load(response)
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None, False
            if exc.code in (429, 503):
                time.sleep(5 * (attempt + 1))
                continue
            return None, False
        except Exception:
            time.sleep(2 * (attempt + 1))
    else:
        print("  !  giving up after repeated errors (service throttling)")
        return None, False
    if data.get("syncedLyrics"):
        return data["syncedLyrics"], True
    if data.get("plainLyrics"):
        return data["plainLyrics"], False
    return None, False


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("library", nargs="?", default="~/Music/library",
                    help="library folder to scan (default: %(default)s)")
    ap.add_argument("--user-agent", default=DEFAULT_USER_AGENT,
                    help="User-Agent sent to LRCLIB; identify your own tool/contact")
    ap.add_argument("--request-delay", type=float, default=1.0,
                    help="seconds to sleep between requests (default: %(default)s)")
    return ap


def main():
    args = build_parser().parse_args()
    library = pathlib.Path(args.library).expanduser()
    tracks = sorted(
        p for p in library.rglob("*") if p.suffix.lower() in AUDIO_EXTENSIONS
    )
    synced = plain = missing = skipped = unreadable = 0

    for track in tracks:
        sidecar = track.with_suffix(".lrc")
        if sidecar.exists():
            skipped += 1
            continue
        tags = read_tags(track)
        if tags is None:
            unreadable += 1
            print(f"  ?  no tags: {track.name}")
            continue
        artist, title, album, duration = tags
        lyrics, is_synced = fetch(artist, title, album, duration, args.user_agent)
        # Retry without the album: LRCLIB matches are stricter with one set.
        if lyrics is None and album:
            lyrics, is_synced = fetch(artist, title, None, duration, args.user_agent)
        if lyrics is None:
            missing += 1
            print(f"  -  none:    {artist} - {title}")
        else:
            sidecar.write_text(lyrics, encoding="utf-8")
            if is_synced:
                synced += 1
                print(f"  ok synced:  {artist} - {title}")
            else:
                plain += 1
                print(f"  ok plain:   {artist} - {title}")
        time.sleep(args.request_delay)  # be polite to a free community API

    print(
        f"\n{len(tracks)} tracks | synced {synced} | plain {plain} | "
        f"none {missing} | already had {skipped} | unreadable {unreadable}"
    )


if __name__ == "__main__":
    main()
