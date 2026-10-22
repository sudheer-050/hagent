# Hagent website release archive

The public guide is served at `https://myhai.org/hagent/`. Deploy `hagent.html`
as both `index.html` and `landing.html`, and deploy `releases.js` beside them.
The HAI static server uses `landing.html` as its directory index.

The Versions section ships usable HTML notes and downloads for the current
three tagged releases. It does not depend on JavaScript for those links.
`release-snapshot` contains the same records for merging with live GitHub data.

On each page visit, `releases.js` reads public GitHub releases and source tags.
It sorts stable semantic versions newest first and displays at most five.
Prereleases are excluded. Each version offers its source ZIP and tarball;
published releases also show their attached files and release notes.
If GitHub is unavailable or rate-limited, the shipped archive remains visible.
Versions outside this five-entry list remain available through GitHub's tags.
This is a display limit, not a request to delete older repository history.

For a new application release, update the hero version, What's new section,
and static archive/snapshot together. Publish release notes and any packaged
files on GitHub. The live archive will discover the new version even before
the next website deployment.
