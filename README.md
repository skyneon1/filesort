# FileSort

Sorts a messy folder into neat subfolders — by extension (`jpg/`, `pdf/`),
by file type (`Images/`, `Documents/`) or by month (`2026-11/`).
Preview first, never overwrites, one-click undo.

Live site: https://www.filesort.in/

| Part | Where | What it is |
|------|-------|-----------|
| Website + web app | `site/` | Static files. Sorts folders in the browser (Chrome/Edge), nothing is uploaded. |
| Desktop app | `app.py` | The same tool as a window; `build.py` turns it into a standalone program. |
| Command line | `filesort.py` | The shared sorting logic, also usable from a terminal. |

## Website

    cd site && python3 -m http.server 8000     # then open http://localhost:8000

It is plain HTML + JS with no build step and no backend, so any free static host works
(GitHub Pages, Cloudflare Pages, Netlify, Vercel): upload the `site/` folder.

## Desktop app

Not offered on the website; the code is kept here for anyone who wants to build it.

    pip install pyinstaller
    python build.py          # result in dist/, for the OS you ran it on

An executable can only be built on the OS it is for. To get all three, push to GitHub and
run the **Build apps** workflow (Actions tab); pushing a tag like `v1.0` also publishes
them as a release.

## Command line

    python3 filesort.py ~/Downloads               # preview, then ask
    python3 filesort.py ~/Downloads --by type     # Images/, Documents/, ...
    python3 filesort.py ~/Downloads -r            # include subfolders
    python3 filesort.py ~/Downloads --undo        # reverse the last run

## Tests

    python -m unittest discover -s tests -v

The GitHub workflow runs these on Windows, macOS and Linux, then builds the app on each
and checks that it starts.

## License

Copyright (c) 2026 FileSort. All rights reserved. The code is public so it can be
read and checked, but it may not be copied, modified or redistributed. See `LICENSE`.
