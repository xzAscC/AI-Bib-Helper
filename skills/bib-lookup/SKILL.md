---
name: bib-lookup
description: Get correct BibTeX for papers — DBLP through the `aibib` CLI (AI Bib Helper; the publisher's official export, the DOI agency's, or DBLP metadata), then Google Scholar in the user's Chrome, then arXiv through `aibib --arxiv`. Use when the user asks to 找 bib, 加引用, get/fix a BibTeX entry, fill a .bib file, or cite a paper by title, DOI or URL. Never write BibTeX from memory.
---

# Bib Lookup

BibTeX written from memory has wrong venues, years and author lists. Every
entry must come from one of these sources, in this order:

1. DBLP, through `aibib` (https://github.com/xzAscC/AI-Bib-Helper).
2. Google Scholar's BibTeX export, read in the user's Chrome.
3. arXiv, through `aibib --arxiv`.

If `aibib` is not installed, ask before you install it with
`uv tool install git+https://github.com/xzAscC/AI-Bib-Helper`.

## 1. DBLP

1. Get the exact title from the paper or from the user. Do not guess a title
   from a topic. If you only have keywords, run `aibib search "<keywords>" -n 5`
   and let the user pick. Keywords find a paper only when they stand
   together in its title, or when its title starts with the first letter of
   the first keyword and contains the four longest keywords.
2. Run `aibib get "<exact title>"` (several titles in one call are fine; the
   tool spaces the requests). A DOI or paper URL also works.
3. Read the `%` line on stderr for each entry. Check the title, the year and the
   first author against what the user meant. Also check the version:
   - `[published]` is preferred.
   - If the line says `[preprint]` but the paper was published, run
     `aibib search` and take the published version with `--pick N`.
4. Go to Google Scholar when aibib says `no DBLP match`, or when the paper was
   published but DBLP lists only the preprint. DBLP lags behind new
   proceedings and does not list some venues (for example COLM).

## 2. Google Scholar

Google Scholar's robots.txt disallows scripts: never fetch it with aibib,
curl or WebFetch. Read it in the user's Chrome with the Claude in Chrome
tools (read the `claude-in-chrome` skill first).

1. Open a new tab at `https://scholar.google.com/scholar?hl=en&q=` followed by
   the URL-encoded title.
2. Find the result whose title matches. Scholar shows one version first,
   often the arXiv one; if the paper was published, open "All N versions"
   and read the page text for the published version.
3. If that version's title links to a page that aibib supports
   (proceedings.neurips.cc, proceedings.mlr.press, aclanthology.org,
   openaccess.thecvf.com, ojs.aaai.org, ijcai.org, link.springer.com), run
   `aibib get "<that URL>"`: the publisher's own export is better than
   Scholar's.
4. Otherwise open its Cite dialog: the Cite button, or, for a version without
   one, `https://scholar.google.com/scholar?q=info:<id>:scholar.google.com/&output=cite&hl=en`
   with the id from that version's links. Read the exact BibTeX href with
   `read_page` (not `find`, which may alter it) and navigate to it. The
   BibTeX page is on scholar.googleusercontent.com, where page text cannot be
   read: zoom into the top left of a screenshot and copy the entry exactly.
5. Look up one paper at a time, a few seconds apart. If Scholar shows a
   CAPTCHA or an "unusual traffic" page, or the BibTeX link returns 404
   twice in a row, stop and tell the user. Do not solve a CAPTCHA.
6. Close the tab when you are done.

Scholar's entries often have no DOI or pages and an abbreviated venue. Check
the venue and the year against the paper.

## 3. arXiv

If Scholar has no match or cannot be used, run `aibib get --arxiv "<title>"`.
It searches the arXiv API and takes the arXiv version's DataCite record (shown
as `DOI` and `[preprint]`). `aibib search --arxiv` lists the candidates.

## Report and add

1. Report the source to the user:
   - `NeurIPS`, `PMLR`, `ACL`, `CVF`, `AAAI`, `IJCAI`, `Springer`: the
     publisher's official export.
   - `DOI`: the Crossref or DataCite record.
   - `DBLP-metadata`: built from DBLP's record. Mention this.
   - `Scholar`: Google Scholar's export. Mention this.
2. Add the entry to the `.bib` file only when the user asks. Keep the official
   key unless the user wants their own key; then use `--key` (or change the
   key of a Scholar entry by hand). Before you append, check the file for a
   duplicate key or title.

## Limits

- Exit code 2 means the run was rate-limited, or a DBLP query reached the
  server's time limit (the server is busy). Stop, tell the user, and do not
  retry in a loop.
- Do not use OpenReview or dblp.org pages directly; they block scripts. The
  user's browser userscript Overleaf-Bib-Helper (Chrome Profile 4,
  `Alt+Shift+B` in an Overleaf project) covers those cases by hand.
- `aibib --refresh` refetches. Use it only if the cached entry is wrong.
