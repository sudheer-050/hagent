// Keep the five newest stable versions visible; retain the shipped archive offline.
(() => {
  const root = document.getElementById('release-list');
  if (!root) return;
  const repository = 'sudheer-050/hagent';
  const stable = /^v?\d+\.\d+\.\d+$/;
  const version = name => name.replace(/^v/, '').split('.').map(Number);
  const compare = (a, b) => {
    const av = version(a.tag), bv = version(b.tag);
    for (let i = 0; i < 3; i++) if (av[i] !== bv[i]) return bv[i] - av[i];
    return 0;
  };
  function render(entries) {
    const fragment = document.createDocumentFragment();
    entries.filter(item => stable.test(item.tag)).sort(compare).slice(0, 5).forEach((item, index) => {
      const card = document.createElement('details'); card.className = 'release-card'; card.open = index === 0;
      const summary = document.createElement('summary');
      summary.textContent = `${item.tag}${index === 0 ? ' · Latest' : ''}${item.date ? ' · ' + item.date.slice(0, 10) : ''}`;
      const notes = document.createElement('div'); notes.className = 'release-notes';
      notes.textContent = (item.notes || 'This source snapshot is available from GitHub. Separate release notes have not been published.')
        .replace(/^#{1,6}\s*/gm, '').replace(/\[([^\]]+)\]\([^)]*\)/g, '$1').replace(/\*\*/g, '');
      const actions = document.createElement('div'); actions.className = 'release-downloads';
      const links = [
        {name: 'Source ZIP', url: `https://github.com/${repository}/archive/refs/tags/${item.tag}.zip`},
        {name: 'Source tar.gz', url: `https://github.com/${repository}/archive/refs/tags/${item.tag}.tar.gz`},
        ...(item.assets || []),
        {name: item.published ? 'Full release notes' : 'Browse source', url: item.published ? `https://github.com/${repository}/releases/tag/${item.tag}` : `https://github.com/${repository}/tree/${item.tag}`}
      ];
      links.forEach(link => {
        const url = new URL(link.url);
        if (url.protocol !== 'https:' || url.hostname !== 'github.com') return;
        const anchor = document.createElement('a'); anchor.className = 'btn ghost';
        anchor.href = url.href; anchor.textContent = link.name; actions.appendChild(anchor);
      });
      card.append(summary, notes, actions); fragment.appendChild(card);
    });
    if (fragment.childElementCount) root.replaceChildren(fragment);
  }
  async function refresh() {
    const fallback = JSON.parse(document.getElementById('release-snapshot').textContent);
    try {
      const responses = await Promise.all([
        fetch(`https://api.github.com/repos/${repository}/releases?per_page=20`, {signal: AbortSignal.timeout(7000)}),
        fetch(`https://api.github.com/repos/${repository}/tags?per_page=100`, {signal: AbortSignal.timeout(7000)})
      ]);
      if (responses.some(response => !response.ok)) return;
      const [releases, tags] = await Promise.all(responses.map(response => response.json()));
      if (!Array.isArray(releases) || !Array.isArray(tags)) return;
      const entries = new Map(fallback.map(item => [item.tag, item]));
      // Drafts and previews never appear as stable source downloads.
      const previews = new Set(releases.filter(item => item.draft || item.prerelease).map(item => item.tag_name));
      tags.filter(item => stable.test(item.name) && !previews.has(item.name)).forEach(item => {
        if (!entries.has(item.name)) entries.set(item.name, {tag: item.name});
      });
      releases.filter(item => !item.draft && !item.prerelease && stable.test(item.tag_name)).forEach(item => {
        entries.set(item.tag_name, {tag: item.tag_name, date: item.published_at, notes: item.body,
          published: true, assets: (item.assets || []).map(asset => ({name: asset.name, url: asset.browser_download_url}))});
      });
      render([...entries.values()]);
    } catch (_) {
      // The static notes and download links stay usable if GitHub is unavailable.
    }
  }
  refresh();
})();
