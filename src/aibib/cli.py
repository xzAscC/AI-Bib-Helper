"""aibib: find a paper on DBLP and print its BibTeX, preferring the publisher's own export.

BibTeX goes to stdout; what was matched and where the entry came from goes to
stderr, so an agent can check the match before using the entry.
"""

import argparse
import json
import sys

from . import __version__, bibtex, net, sources


def describe(g):
    authors = ", ".join(g["authors"][:3]) + (" et al." if len(g["authors"]) > 3 else "")
    kind = "preprint" if sources.is_preprint(g) else "published"
    return f'{g["year"]} {g["venue"]} [{kind}] {g["title"]} — {authors}'


def cmd_search(args):
    groups = sources.search(args.query, args.n)
    if args.json:
        json.dump([{k: g[k] for k in ("title", "authors", "year", "venue", "dblp", "doi")}
                   | {"preprint": sources.is_preprint(g),
                      "official": (sources.best_official(g) or [None])[0],
                      "versions": [v["dblp"] for v in g["versions"]]} for g in groups],
                  sys.stdout, ensure_ascii=False, indent=1)
        print()
    for i, g in enumerate(groups, 1):
        if not args.json:
            print(f"{i}. {describe(g)}\n   {g['dblp']}")
            for v in g["versions"][1:]:
                print(f"   also: {v['year']} {v['venue']} {v['dblp']}")
    if not groups:
        print("no DBLP results", file=sys.stderr)
        return 1
    return 0


def resolve(query, pick, use_official):
    """Return (bib, note) for a title/keywords, DOI or paper URL."""
    if use_official and (target := sources.official(query)):
        return sources.official_bib(*target), f"{target[0]} {query}"
    if doi := sources.doi_of(query):
        return sources.doi_bib(doi), f"DOI {doi}"
    groups = sources.search(query, max(pick, 5))
    if len(groups) < pick:
        raise net.FetchError(f"no DBLP match #{pick} for: {query}")
    g = groups[pick - 1]
    bib, source = sources.lookup(g, use_official)
    return bib, f"{source} | {describe(g)}"


def cmd_get(args):
    if args.key and len(args.query) > 1:
        sys.exit("--key needs exactly one query")
    status = 0
    for q in args.query:
        try:
            bib, note = resolve(q, args.pick, not args.doi)
        except net.RateLimited as e:
            print(f"% stopped: {e}", file=sys.stderr)
            return 2
        except (net.FetchError, bibtex.BibError) as e:
            print(f"% failed: {q}: {e}", file=sys.stderr)
            status = 1
            continue
        if args.key:
            bib = bibtex.set_key(bib, args.key)
        print(f"% {note}", file=sys.stderr)
        print(bib + "\n")
    return status


def main(argv=None):
    p = argparse.ArgumentParser(prog="aibib", description=__doc__.split("\n")[0])
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--refresh", action="store_true", help="ignore the cache and fetch again")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search", help="list DBLP matches (grouped by paper)")
    s.add_argument("query")
    s.add_argument("-n", type=int, default=5, help="number of papers (default 5)")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_search)

    g = sub.add_parser("get", help="print BibTeX for each query")
    g.add_argument("query", nargs="+", help="title/keywords, DOI or paper URL")
    g.add_argument("--pick", type=int, default=1, help="use the Nth search match (default 1)")
    g.add_argument("--doi", action="store_true", help="use the DOI agency's export, not the publisher's")
    g.add_argument("--key", help="replace the citation key")
    g.set_defaults(func=cmd_get)

    args = p.parse_args(argv)
    net.refresh = args.refresh
    try:
        return args.func(args)
    except net.FetchError as e:
        print(f"% {e}", file=sys.stderr)
        return 2 if isinstance(e, net.RateLimited) else 1


if __name__ == "__main__":
    sys.exit(main())
