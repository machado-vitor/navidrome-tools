# navidrome-tools

Small standalone scripts for tending a self-hosted music library (built
against Navidrome, but library-server agnostic).

## organize.py

Moves finished downloads into a tag-organized library tree
(`Artist/Album/NN - Title.ext`), replacing lossy duplicates with a genuine
upgrade (lossless, or a materially higher bitrate) and leaving everything
else alone. Never deletes the old copy until the new one is safely in place.
Untagged files still get filed correctly by falling back to folder-name
parsing (`Artist - Album [FLAC]/01. Title.flac`, the common Soulseek/torrent
layout).

```sh
organize --downloads ~/Music/downloads --library ~/Music/library     # dry run
organize --downloads ~/Music/downloads --library ~/Music/library --go
```

`--restart-hint` prints a reminder command after a real move (e.g. a
`docker restart` or a scan trigger) instead of guessing at your setup.

## fetch_lyrics.py

Fetches synced (`.lrc`) lyrics from [LRCLIB](https://lrclib.net) for every
track in a library, skipping tracks that already have a sidecar file. Falls
back to plain lyrics when no synced version exists. Please pass
`--user-agent` identifying your own tool/contact when running this at scale;
LRCLIB is a free community service.

```sh
fetch-lyrics ~/Music/library --user-agent "my-tool/1.0 (https://example.com)"
```

## Requirements

- Python 3.9+
- [`mutagen`](https://mutagen.readthedocs.io/) for tag reading

```sh
pip install git+https://github.com/machado-vitor/navidrome-tools
```

## Not included: onplay_watcher.py

A webhook/poll daemon that triggered Soulseek re-fetches the instant a lossy
track was played is deliberately left out of this repo. It only makes sense
wired into a specific reverse-proxy convention (a "ticket file" dropped for
every blocked lossy stream) and calls a private Soulseek automation script
that isn't part of this project's scope, so publishing it as-is would be a
config snippet nobody else could run, not a reusable tool.

## License

MIT
