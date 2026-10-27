"""DBLP search plus official publisher BibTeX exports.

Ported from Overleaf-Bib-Helper (MIT, MLNLP-World). DBLP finds the paper and
lists its electronic editions; when one of them is a supported publisher, the
publisher's own export is used, otherwise the DOI agency's (Crossref/DataCite),
otherwise an entry built from DBLP's own metadata. arXiv search is the
alternative for papers DBLP does not list; their entries come from DataCite.
"""

import difflib
import html
import json
import re
import unicodedata
import urllib.parse
import xml.etree.ElementTree as ET

from . import bibtex
from .net import fetch, FetchError, RateLimited

# Open proceedings first when one record lists several. IEEE Xplore and
# OpenReview block scripts, so their papers fall back to the DOI or DBLP metadata.
PRIORITY = ["CVF", "NeurIPS", "PMLR", "ACL", "AAAI", "IJCAI", "Springer"]


# ---------- DBLP (SPARQL) ----------
# dblp.org's web API sits behind a bot check and robots.txt disallows scripts;
# sparql.dblp.org is the endpoint dblp offers for programs (Crawl-delay: 10).

SPARQL = "https://sparql.dblp.org/sparql"
QUERY = """PREFIX dblp: <https://dblp.org/rdf/schema#>
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
SELECT ?p ?title (SAMPLE(?y) AS ?year) (SAMPLE(?v) AS ?venue) (SAMPLE(?d) AS ?doi)
  (SAMPLE(?bt) AS ?bibtype) (SAMPLE(?pt) AS ?parent) (SAMPLE(?j) AS ?journal)
  (SAMPLE(?vol) AS ?volume) (SAMPLE(?pg) AS ?pages) (SAMPLE(?pp) AS ?page)
  (GROUP_CONCAT(DISTINCT STR(?t); separator=" ") AS ?types)
  (GROUP_CONCAT(DISTINCT STR(?e); separator=" ") AS ?ees)
  (GROUP_CONCAT(DISTINCT CONCAT(STR(?o), "|", ?n); separator="\\n") AS ?authors) WHERE {
 { SELECT ?p ?title WHERE { ?p dblp:title ?title . %s } LIMIT 200 }
 OPTIONAL { ?p dblp:yearOfPublication ?y } OPTIONAL { ?p dblp:publishedIn ?v }
 OPTIONAL { ?p dblp:doi ?d } OPTIONAL { ?p dblp:documentPage ?e } OPTIONAL { ?p rdf:type ?t }
 OPTIONAL { ?p dblp:bibtexType ?bt } OPTIONAL { ?p dblp:publishedAsPartOf/dblp:title ?pt }
 OPTIONAL { ?p dblp:publishedInJournal ?j } OPTIONAL { ?p dblp:publishedInJournalVolume ?vol }
 OPTIONAL { ?p dblp:pagination ?pg } OPTIONAL { ?p dblp:primaryDocumentPage ?pp }
 OPTIONAL { ?p dblp:hasSignature ?s . ?s dblp:signatureOrdinal ?o ; dblp:signatureDblpName ?n }
} GROUP BY ?p ?title"""


def sparql_records(words, starts=()):
    """DBLP records whose lower-cased title contains every string in words.

    starts, if given, keeps only titles that begin with one of its strings.
    QLever answers this prefix filter from its sorted index in under a second;
    without it the query scans every title in DBLP (about 10 s on the server).
    """
    lit = lambda w: '"' + w.replace("\\", "\\\\").replace('"', '\\"') + '"'
    cond = " && ".join(f"CONTAINS(LCASE(STR(?title)), {lit(w)})" for w in words)
    filters = f"FILTER({cond})"
    if starts:
        filters = "FILTER(" + " || ".join(f"STRSTARTS(?title, {lit(s)})" for s in starts) + ") " + filters
    data = urllib.parse.urlencode({"query": QUERY % filters})

    def check(text):
        if "results" not in json.loads(text):
            raise FetchError("DBLP SPARQL returned an error")

    try:
        _, text = fetch(SPARQL, data, {"Accept": "application/sparql-results+json",
                                       "Content-Type": "application/x-www-form-urlencoded"},
                        check=check, interval=10)
    except RateLimited as e:
        # QLever also answers 429 when a query reaches the server's time limit
        # ("Operation timed out."): the query was too slow, not too frequent.
        if "timed out" in e.body:
            raise FetchError("DBLP SPARQL query timed out") from None
        raise
    out = []
    for b in json.loads(text)["results"]["bindings"]:
        v = lambda k: b.get(k, {}).get("value", "")
        authors = sorted((int(o), n) for o, n in (x.split("|", 1) for x in v("authors").splitlines() if "|" in x))
        out.append({
            "title": v("title").rstrip("."),
            "authors": [n for _, n in authors],
            "venue": v("venue"), "year": v("year"),
            "type": v("types").rsplit("#", 1)[-1] if "Informal" not in v("types") else "Informal",
            "dblp": v("p"), "doi": doi_of(v("doi")),
            "ee": v("ees").split(),
            "bibtype": v("bibtype").rsplit("#", 1)[-1].lower() or "misc",
            "booktitle": v("parent").rstrip("."), "journal": v("journal"),
            "volume": v("volume"), "pages": v("pages"), "url": v("page"),
        })
    return out


def search(query, n=5):
    """Return up to n paper groups, best title match first.

    Each group is a dict with title/authors/year/venue of its best version and
    'versions': all DBLP records of the same paper, published before preprint.
    The query is matched as a phrase, then word by word, in the titles that
    start with its first word; if both fail, as a phrase in all titles.
    """
    phrase = " ".join(query.lower().split())
    words = key_words(phrase)
    head, *rest = query.split() or [""]
    first = head + (" " if rest else "")
    # DBLP capitalizes a title's first word; also try the query's own spelling.
    starts = list(dict.fromkeys([first, first[:1].upper() + first[1:]]))
    # Cheapest first: the prefix queries take under a second. The last one scans
    # every title, about 10 s of the server's 30 s limit; a scan for several
    # words takes longer than the limit, so it is not tried.
    hits = []
    for w, s in ([phrase], starts), (words, starts), ([phrase], ()):
        if head and w and (hits := sparql_records(w, s)):
            break

    groups = {}
    for h in hits:
        first = h["authors"][0] if h["authors"] else ""
        groups.setdefault((norm(h["title"]), norm(first)), []).append(h)
    out = []
    for versions in groups.values():
        versions.sort(key=is_preprint)
        out.append(dict(versions[0], versions=versions))
    return by_title(query, out)[:n]


def key_words(text):
    """The four longest words of text, longest first; deterministic so cached queries are reused."""
    return sorted({w for w in re.split(r"[^\w]+", text.lower()) if len(w) > 2}, key=lambda w: (-len(w), w))[:4]


def by_title(query, groups):
    """groups sorted by how close their title is to query, then by year."""
    q = norm(query)
    return sorted(groups, key=lambda g: (-difflib.SequenceMatcher(None, q, norm(g["title"])).ratio(), g["year"]))


# ---------- arXiv ----------
# The arXiv API asks for one request at a time, at least 3 s apart, as fetch does.

ARXIV = "https://export.arxiv.org/api/query"
ATOM = {"a": "http://www.w3.org/2005/Atom"}


def arxiv_records(terms):
    """arXiv papers whose title matches every term (a word or a quoted phrase)."""
    query = urllib.parse.urlencode({"search_query": " AND ".join(f"ti:{t}" for t in terms),
                                    "max_results": 20})

    def check(text):
        if ET.fromstring(text).tag != "{http://www.w3.org/2005/Atom}feed":
            raise FetchError("arXiv API returned no Atom feed")

    _, text = fetch(f"{ARXIV}?{query}", check=check)
    out = []
    for e in ET.fromstring(text).iterfind("a:entry", ATOM):
        # <id> is http://arxiv.org/abs/<id>v<n>; errors come as entries with another <id>.
        aid = e.findtext("a:id", "", ATOM).partition("/abs/")[2]
        if not aid:
            continue
        aid = re.sub(r"v\d+$", "", aid)
        out.append({
            "title": " ".join(e.findtext("a:title", "", ATOM).split()),
            "authors": [a.findtext("a:name", "", ATOM) for a in e.iterfind("a:author", ATOM)],
            "venue": "arXiv", "year": e.findtext("a:published", "", ATOM)[:4],
            "type": "Informal", "dblp": "", "doi": f"10.48550/arXiv.{aid}",
            "ee": [], "url": f"https://arxiv.org/abs/{aid}",
        })
    return out


def arxiv_search(query, n=5):
    """Return up to n arXiv papers, best title match first, shaped like search's groups.

    The query is matched as a title phrase, then by its four longest words.
    """
    # arXiv's query syntax gives punctuation a meaning; keep only the words.
    words = re.findall(r"\w+", query)
    hits = arxiv_records(['"' + " ".join(words) + '"']) if words else []
    if not hits and (longest := key_words(query)):
        hits = arxiv_records(longest)
    return by_title(query, [dict(h, versions=[h]) for h in hits])[:n]


def is_preprint(rec):
    return ("/journals/corr/" in rec["dblp"] or rec["venue"].lower() in ("corr", "arxiv")
            or rec["type"] == "Informal")


def norm(text):
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^\w]+", " ", text).split())


def doi_bib(doi):
    """BibTeX from the DOI registration agency (Crossref, DataCite) via doi.org."""
    _, text = fetch(f"https://doi.org/{doi}", headers={"Accept": "application/x-bibtex"},
                    check=bibtex.single)
    return bibtex.single(text)


# ---------- official sources ----------

def doi_of(raw):
    raw = urllib.parse.unquote(raw.strip().replace("{", "").replace("}", "").replace("\\_", "_"))
    raw = re.sub(r"^https?://(dx\.)?doi\.org/", "", raw)
    return raw if re.fullmatch(r"10\.\d{4,9}/[A-Za-z\d._;()/:+-]+", raw) else ""


def from_doi(doi):
    if re.match(r"10\.1007/", doi, re.I):
        return "Springer", doi
    if m := re.fullmatch(r"10\.24963/ijcai\.(20\d\d)/(\d+)", doi, re.I):
        if int(m[1]) >= 2017:
            return "IJCAI", f"https://www.ijcai.org/proceedings/{m[1]}/{int(m[2])}"
    if m := re.fullmatch(r"10\.1609/aaai\.v\d+i\d+\.(\d+)", doi, re.I):
        return "AAAI", f"https://ojs.aaai.org/index.php/AAAI/article/view/{int(m[1])}"
    if re.match(r"10\.18653/v1/", doi, re.I):
        return official(f"https://aclanthology.org/{doi.split('/', 2)[2]}/")
    return None


def official(raw):
    """Map a paper URL or DOI to (source, id) for a supported publisher, else None."""
    if doi := doi_of(raw):
        return from_doi(doi)
    u = urllib.parse.urlsplit(raw)
    host, path = u.hostname or "", u.path
    if host.endswith("openaccess.thecvf.com"):
        path = path.replace("/papers/", "/html/").replace("_paper.pdf", "_paper.html")
        if path.endswith("_paper.html"):
            return "CVF", "https://openaccess.thecvf.com" + path
    if host in ("proceedings.neurips.cc", "papers.nips.cc", "papers.neurips.cc", "proceedings.nips.cc"):
        path = re.sub(r"^/paper/(\d{4})/", r"/paper_files/paper/\1/", path)
        if re.fullmatch(r"/paper_files/paper/\d{4}/hash/[a-f\d]{32}-Abstract(-[A-Za-z_]+)?\.html", path):
            return "NeurIPS", "https://proceedings.neurips.cc" + path
    if host == "proceedings.mlr.press" and re.fullmatch(r"/v\d+/[\w-]+\.html", path):
        return "PMLR", "https://proceedings.mlr.press" + path
    if host in ("aclanthology.org", "www.aclweb.org", "aclweb.org"):
        aid = re.sub(r"^/(anthology/)?|/$|\.(bib|pdf)$", "", path)
        if re.fullmatch(r"[A-Z]\d{2}-\d{4,5}|\d{4}\.[a-z\d-]+\.\d+", aid, re.I):
            return "ACL", f"https://aclanthology.org/{aid}/"
    if host == "ojs.aaai.org":
        if m := re.fullmatch(r"/index\.php/([\w-]+)/article/(?:view|download)/(\d+)(/\d+)*/?", path):
            return "AAAI", f"https://ojs.aaai.org/index.php/{m[1]}/article/view/{m[2]}"
    if host in ("ijcai.org", "www.ijcai.org"):
        if m := re.fullmatch(r"/proceedings/(20\d\d)/(?:bibtex/)?(\d+)(\.pdf)?/?", path):
            return from_doi(f"10.24963/ijcai.{m[1]}/{m[2]}")
    if host == "link.springer.com":
        if doi := doi_of(re.sub(r"^/(chapter|article|book)/", "", path)):
            return from_doi(doi)
    return None


def best_official(rec):
    """The highest-priority supported edition of a published DBLP record."""
    if is_preprint(rec):
        return None
    found = [s for s in map(official, rec["ee"]) if s]
    return min(found, key=lambda s: PRIORITY.index(s[0]), default=None)


def check_doi(bib, doi, key_ok=False):
    got = doi_of(bibtex.field(bib, "doi")) or (doi_of(bibtex.key(bib)) if key_ok else "")
    if got.lower() != doi.lower():
        raise FetchError(f"official BibTeX DOI {got or '(none)'} does not match {doi}")
    return bib


def official_bib(source, cid):
    if source == "ACL":
        return bibtex.single(fetch(cid.rstrip("/") + ".bib")[1])
    if source == "PMLR":
        m = re.search(r'<code[^>]*\bid=["\']bibtex["\'][^>]*>(.*?)</code>', fetch(cid)[1], re.S)
        if not m:
            raise FetchError("PMLR page has no BibTeX block")
        return bibtex.single(html.unescape(m[1]))
    if source == "CVF":
        m = re.findall(r'<div class=["\']bibref[^"\']*["\']>(.*?)</div>', fetch(cid)[1], re.S)
        if len(m) != 1:
            raise FetchError("CVF page does not have exactly one BibTeX block")
        return bibtex.single(html.unescape(re.sub(r"<br\s*/?>", "\n", m[0])))
    if source == "NeurIPS":
        final, page = fetch(cid)
        m = re.search(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>\s*Bibtex\s*</a>', page, re.I)
        paper = re.search(r"/(\d{4})/hash/([a-f\d]{32})-", cid)
        href = urllib.parse.urljoin(final, m[1]) if m else ""
        # Older years link to <hash>-Bibtex.bib; 2023+ to /paper_files/paper/<n>-/bibtex.
        if not (m and paper and (f"/{paper[1]}/file/{paper[2]}-Bibtex" in href
                                 or re.search(r"/paper_files/paper/\d+-/bibtex$", href))):
            raise FetchError("NeurIPS page has no matching BibTeX link")
        return bibtex.single(fetch(href)[1])
    if source == "AAAI":
        final, page = fetch(cid)
        art = cid.rsplit("/", 1)[1]
        m = re.search(r'href=["\']([^"\']*/citationstylelanguage/download/bibtex\?[^"\']*)["\']', page)
        href = urllib.parse.urljoin(final, html.unescape(m[1])) if m else ""
        if f"submissionId={art}" not in href:
            raise FetchError("AAAI page has no matching BibTeX export")
        bib = bibtex.single(fetch(href)[1])
        if doi := doi_of(re.search(r'name="citation_doi" content="([^"]+)"', page)[1] if 'citation_doi' in page else ""):
            check_doi(bib, doi)
        return bib
    if source == "IJCAI":
        y, n = re.search(r"/(\d{4})/(\d+)$", cid).groups()
        bib = bibtex.single(fetch(f"https://www.ijcai.org/proceedings/{y}/bibtex/{n}")[1])
        return check_doi(bib, f"10.24963/ijcai.{y}/{n}")
    if source == "Springer":
        url = f"https://citation-needed.springer.com/v2/references/{cid}?format=bibtex&flavour=citation"
        # Springer uses the DOI as its citation key and may omit the doi field.
        return check_doi(bibtex.single(fetch(url)[1]), cid, key_ok=True)
    raise FetchError(f"unsupported source {source}")


# ---------- one entry point ----------

def lookup(rec, use_official=True):
    """Return (bib, source label) for a DBLP or arXiv record; fall back to its DOI on failure."""
    note = ""
    if use_official and (target := best_official(rec)):
        try:
            return official_bib(*target), target[0]
        except (FetchError, bibtex.BibError) as e:
            if e.__class__.__name__ == "RateLimited":
                raise
            note = f" (official {target[0]} failed: {e})"
    if rec.get("doi"):
        try:
            return doi_bib(rec["doi"]), "DOI" + note
        except (FetchError, bibtex.BibError) as e:
            if e.__class__.__name__ == "RateLimited":
                raise
            note += f" (DOI failed: {e})"
    if not rec["dblp"]:  # an arXiv record has no DBLP metadata to build from
        raise FetchError("no BibTeX" + note)
    return dblp_meta_bib(rec), "DBLP-metadata" + note


def dblp_meta_bib(rec):
    """Build an entry from DBLP's record, the same data dblp.org's own export uses."""
    fields = {
        "author": " and ".join(re.sub(r" \d{4}$", "", a) for a in rec["authors"]),
        "title": rec["title"],
        "booktitle": rec["booktitle"] if rec["bibtype"] == "inproceedings" else "",
        "journal": rec["journal"] if rec["bibtype"] == "article" else "",
        "volume": rec["volume"], "pages": rec["pages"].replace("-", "--"),
        "year": rec["year"], "doi": rec["doi"],
        "url": rec["url"] or (rec["ee"][0] if rec["ee"] else ""),
    }
    key = "DBLP:" + rec["dblp"].split("/rec/", 1)[1]
    body = ",\n".join(f"  {k:<9} = {{{v}}}" for k, v in fields.items() if v)
    return bibtex.single(f"@{rec['bibtype']}{{{key},\n{body}\n}}")
