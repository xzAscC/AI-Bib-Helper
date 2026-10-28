# AI Bib Helper

A command-line tool that gives coding agents (Claude Code, Codex, pi, …) correct
BibTeX for a paper title, DOI or paper URL. It prefers the publisher's own
export over generated entries.

It ports the lookup logic of the
[Overleaf-Bib-Helper](https://github.com/MLNLP-World/Overleaf-Bib-Helper)
userscript to a script that runs without a browser. No dependencies beyond
the Python standard library.

## Install

```bash
uv tool install git+https://github.com/xzAscC/AI-Bib-Helper
# or, from a clone:  uv tool install .
```

## Use

```bash
aibib get "Attention is all you need"              # best match, official export
aibib get "Denoising Diffusion Implicit Models" "Deep Residual Learning for Image Recognition"
aibib get 10.18653/v1/N19-1423                     # DOI or paper URL
aibib get "Scaling laws for neural language models" --key kaplan2020scaling
aibib search "low-rank adaptation" -n 5            # list candidates
aibib search "..." --json                          # for scripts and agents
aibib get "..." --pick 2                           # take the 2nd candidate
aibib get --arxiv "..."                            # find it on arXiv, not DBLP (also for search)
aibib --refresh get "..."                          # ignore the cache
```

BibTeX goes to stdout. A line on stderr says which paper matched and where the
entry came from, for example:

```
% NeurIPS | 2017 NIPS [published] Attention is All you Need — Ashish Vaswani, Noam Shazeer, Niki Parmar et al.
```

Check that line before you use the entry. Title search can match a different
paper.

Exit codes: 0 all found, 1 some failed, 2 rate-limited (stop and try later).

## Lookup order for agents

`aibib` covers DBLP and arXiv. Google Scholar disallows scripts, so an agent
reads it in the user's own browser instead. The full order is:

1. DBLP through `aibib get`.
2. Google Scholar in the browser, if DBLP has no match or lists only the
   preprint of a published paper.
3. arXiv through `aibib get --arxiv`.

The skill [skills/bib-lookup/SKILL.md](skills/bib-lookup/SKILL.md) gives the
steps and the checks for each source. Agents that read `SKILL.md` files
(Claude Code, Codex, pi) can use it from a link:

```bash
ln -s "$PWD/skills/bib-lookup" ~/.claude/skills/bib-lookup
```

## How an entry is found

1. **Search.** DBLP, through its SPARQL endpoint `sparql.dblp.org`. The
   query is matched as a phrase in the titles that start with its first word,
   then by its four longest words in the titles that start with its first
   letter; QLever answers these from its sorted index in a few seconds at
   most. If both fail, the phrase is matched in all titles, a scan of about
   10 s of the server's 30 s limit. So keywords find a paper only when its
   title starts with the first keyword's letter or the keywords stand
   together in it. Versions of the same paper are grouped, and the published
   version comes before the arXiv/CoRR preprint.
2. **Official export**, if DBLP lists a supported edition. The order is
   CVF, NeurIPS, PMLR, ACL Anthology, AAAI, IJCAI, Springer (also ECCV).
   DOI-based exports are checked against the paper's DOI.
3. **DOI export.** doi.org content negotiation, which returns the
   registration agency's BibTeX (Crossref for IEEE, ACM and others; DataCite
   for arXiv).
4. **DBLP metadata.** If there is no export and no DOI (for example, ICLR
   papers on OpenReview, or JMLR), the entry is built from DBLP's record. This
   is the same data that dblp.org's own export uses. The source shows as
   `DBLP-metadata`.

With `--arxiv`, step 1 searches arXiv instead, through its API
`export.arxiv.org` (the title as a phrase, then its four longest words), for
papers that DBLP does not list yet. The entry is the arXiv version's DataCite
record (step 3).

### Differences from the userscript

- **dblp.org's web API.** The userscript uses it, but the site now shows a bot
  check (Anubis) and its robots.txt disallows scripts. AI Bib Helper uses
  `sparql.dblp.org`, which allows scripts with a 10-second crawl delay.
- **Not implemented:**
  - Google Scholar: it blocks scripts.
  - OpenReview: its API now requires a browser challenge.
  - IEEE Xplore: its citation endpoint refuses scripts.
  - ACM: its export needs a browser tab.
  - BMVC and KR.
  Papers from IEEE and ACM fall back to their DOI. OpenReview-only papers
  fall back to DBLP metadata.

## Polite use

- There is one request at a time, also across processes, at least 3 s apart
  (10 s for DBLP SPARQL).
- The User-Agent names the tool.
- Responses are cached in `~/.cache/aibib` (or `$AIBIB_CACHE`), so a paper
  is never fetched twice.
- Bot-check pages and invalid responses are not cached.
- HTTP 429/503 stops the run with no retry. QLever, the engine behind
  `sparql.dblp.org`, also answers 429 when a query reaches its 30 s time
  limit (`Operation timed out.`). The searches above take at most about 10 s,
  so this means the server is busy; the run stops with a message that says so.

## Development

```bash
PYTHONPATH=src python -m unittest discover -s tests -v   # offline tests
```

## License

MIT. Derived from Overleaf-Bib-Helper © 2025 Xunjian Yin. See [LICENSE](LICENSE).
