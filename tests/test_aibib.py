"""Offline tests: every network call is patched out."""

import contextlib
import io
import json
import os
import tempfile
import unittest
import urllib.error
import urllib.parse
from unittest.mock import patch

from aibib import bibtex, cli, net, sources

BIB = """@inproceedings{Key_1,
  title = {A {Title}, with comma},
  author = "Doe, Jane and Roe, Rick",
  doi = {10.1007/978-3-030-58452-8_13},
  note = {x}
}"""


def rec(**kw):
    base = {"title": "A Paper", "authors": ["Jane Doe 0001", "Rick Roe"], "venue": "ICLR",
            "year": "2021", "type": "Inproceedings", "dblp": "https://dblp.org/rec/conf/iclr/Doe21",
            "doi": "", "ee": [], "bibtype": "inproceedings", "booktitle": "ICLR 2021",
            "journal": "", "volume": "", "pages": "", "url": "https://openreview.net/forum?id=x"}
    return base | kw


class BibTeX(unittest.TestCase):
    def test_fields_and_key(self):
        self.assertEqual(bibtex.field(BIB, "title"), "A {Title}, with comma")
        self.assertEqual(bibtex.field(BIB, "author"), "Doe, Jane and Roe, Rick")
        self.assertEqual(bibtex.field(BIB, "missing"), "")
        self.assertEqual(bibtex.key(BIB), "Key_1")
        self.assertEqual(bibtex.key(bibtex.set_key(BIB, "doe2021")), "doe2021")

    def test_rejects_html_and_multiple(self):
        for bad in ("<html>nope</html>", BIB + "\n" + BIB, "@article{k, title={x}", "@misc{k, note={x}}"):
            with self.assertRaises(bibtex.BibError):
                bibtex.single(bad)
        with self.assertRaises(bibtex.BibError):
            bibtex.set_key(BIB, "bad key")


class Routing(unittest.TestCase):
    def test_official(self):
        cases = {
            "https://proceedings.neurips.cc/paper/2017/hash/3f5ee243547dee91fbd053c1c4a845aa-Abstract.html":
                ("NeurIPS", "https://proceedings.neurips.cc/paper_files/paper/2017/hash/3f5ee243547dee91fbd053c1c4a845aa-Abstract.html"),
            "https://doi.org/10.18653/V1/N19-1423": ("ACL", "https://aclanthology.org/N19-1423/"),
            "https://aclanthology.org/2020.emnlp-main.317": ("ACL", "https://aclanthology.org/2020.emnlp-main.317/"),
            "https://proceedings.mlr.press/v139/radford21a.html": ("PMLR", "https://proceedings.mlr.press/v139/radford21a.html"),
            "https://openaccess.thecvf.com/content_cvpr_2016/papers/He_Deep_CVPR_2016_paper.pdf":
                ("CVF", "https://openaccess.thecvf.com/content_cvpr_2016/html/He_Deep_CVPR_2016_paper.html"),
            "https://doi.org/10.1007/978-3-030-58452-8_13": ("Springer", "10.1007/978-3-030-58452-8_13"),
            "https://doi.org/10.24963/ijcai.2019/12": ("IJCAI", "https://www.ijcai.org/proceedings/2019/12"),
            "https://doi.org/10.1609/AAAI.V34I07.6693": ("AAAI", "https://ojs.aaai.org/index.php/AAAI/article/view/6693"),
        }
        for url, want in cases.items():
            self.assertEqual(sources.official(url), want, url)
        for url in ("https://doi.org/10.1109/CVPR.2016.90", "https://openreview.net/forum?id=abc",
                    "https://example.org/x", "https://doi.org/10.48550/ARXIV.1706.03762"):
            self.assertIsNone(sources.official(url), url)

    def test_best_official_prefers_open_proceedings_and_skips_preprints(self):
        r = rec(ee=["https://doi.org/10.1007/978-3-030-58452-8_13",
                    "https://openaccess.thecvf.com/content/ICCV2021/html/X_paper.html"])
        self.assertEqual(sources.best_official(r)[0], "CVF")
        self.assertIsNone(sources.best_official(rec(type="Informal", ee=r["ee"])))


class Search(unittest.TestCase):
    def test_groups_versions_and_ranks_by_title(self):
        hits = [
            rec(title="Attention Is All You Need for Speech", authors=["A B"], dblp="https://dblp.org/rec/conf/x/1"),
            rec(title="Attention Is All You Need", authors=["Ashish Vaswani"], venue="CoRR", type="Informal",
                dblp="https://dblp.org/rec/journals/corr/V17"),
            rec(title="Attention is All you Need", authors=["Ashish Vaswani"], venue="NIPS",
                dblp="https://dblp.org/rec/conf/nips/V17"),
        ]
        with patch.object(sources, "sparql_records", return_value=hits) as q:
            groups = sources.search("attention is all you need")
        q.assert_called_once_with(["attention is all you need"], ["attention ", "Attention "])
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0]["venue"], "NIPS")  # published before preprint
        self.assertEqual(len(groups[0]["versions"]), 2)

    def test_falls_back_to_words(self):
        with patch.object(sources, "sparql_records", side_effect=[[], [], []]) as q:
            self.assertEqual(sources.search("low rank adaptation of llms"), [])
        self.assertEqual([c.args for c in q.call_args_list], [
            (["low rank adaptation of llms"], ["low ", "Low "]),
            (["adaptation", "llms", "rank", "low"], ["L", "l"]),
            (["low rank adaptation of llms"], ()),
        ])

    def test_word_step_needs_only_the_first_letter(self):
        cases = {  # query -> (first-word prefixes, first-letter prefixes)
            "lora low-rank adaptation of large language models": (["lora ", "Lora "], ["L", "l"]),
            "ATTENTION is all you need": (["ATTENTION "], ["A", "a"]),
            '"Why should I trust you?"': (['"Why '], ['"']),
            "3D Gaussian splatting": (["3D "], ["3"]),
        }
        for query, (starts, letters) in cases.items():
            with patch.object(sources, "sparql_records", side_effect=[[], [rec()]]) as q:
                sources.search(query)
            self.assertEqual([c.args[1] for c in q.call_args_list], [starts, letters], query)
        # "lora" is not among the words, so the word step finds "LoRA: ..." by the others.
        self.assertEqual(sources.key_words("lora low-rank adaptation of large language models"),
                         ["adaptation", "language", "models", "large"])

    def test_prefix_filter_and_timeout(self):
        payload = json.dumps({"results": {"bindings": []}})
        with patch.object(sources, "fetch", return_value=("", payload)) as f:
            sources.sparql_records(["x"], ['A "b" ', "C"])
        query = urllib.parse.parse_qs(f.call_args.args[1])["query"][0]
        self.assertIn('FILTER(STRSTARTS(?title, "A \\"b\\" ") || STRSTARTS(?title, "C")) '
                      'FILTER(CONTAINS(LCASE(STR(?title)), "x"))', query)
        # QLever's 429 for a timed-out query stops the run too, with its own message.
        timeout = net.RateLimited("429", '{"exception": "Operation timed out.", "status": "ERROR"}')
        with patch.object(sources, "fetch", side_effect=timeout), \
             self.assertRaisesRegex(net.RateLimited, "timed out, so the server is busy"):
            sources.sparql_records(["x"])
        with patch.object(sources, "fetch", side_effect=net.RateLimited("429 plain")), \
             self.assertRaisesRegex(net.RateLimited, "429 plain"):
            sources.sparql_records(["x"])

    def test_sparql_parsing(self):
        payload = {"results": {"bindings": [{
            "p": {"value": "https://dblp.org/rec/conf/iclr/SongME21"},
            "title": {"value": "Denoising Diffusion Implicit Models."},
            "year": {"value": "2021"}, "venue": {"value": "ICLR"},
            "types": {"value": "https://dblp.org/rdf/schema#Publication https://dblp.org/rdf/schema#Inproceedings"},
            "ees": {"value": "https://openreview.net/forum?id=St1giarCHLP"},
            "authors": {"value": "2|Chenlin Meng\n1|Jiaming Song\n3|Stefano Ermon"},
            "bibtype": {"value": "http://purl.org/net/nknouf/ns/bibtex#Inproceedings"},
            "parent": {"value": "9th International Conference on Learning Representations, ICLR 2021."},
        }]}}
        with patch.object(sources, "fetch", return_value=("", json.dumps(payload))) as f:
            [r] = sources.sparql_records(['a "quoted" title'])
        self.assertIn('\\"quoted\\"', f.call_args.args[1].replace("%5C", "\\").replace("%22", '"'))
        self.assertEqual(r["authors"], ["Jiaming Song", "Chenlin Meng", "Stefano Ermon"])
        self.assertEqual((r["title"], r["bibtype"], r["type"]), ("Denoising Diffusion Implicit Models", "inproceedings", "Inproceedings"))


FEED = """<?xml version='1.0' encoding='UTF-8'?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2506.13734v3</id>
    <title>Instruction Following by Principled Boosting
  Attention of Large Language Models</title>
    <published>2025-06-16T17:42:35Z</published>
    <author><name>Vitoria Guardieiro</name></author>
    <author><name>Eric Wong</name></author>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/hep-th/9901001v2</id>
    <title>Another Paper</title>
    <published>1999-01-04T00:00:00Z</published>
  </entry>
</feed>"""


class Arxiv(unittest.TestCase):
    def search_query(self, call):
        return urllib.parse.parse_qs(urllib.parse.urlsplit(call.args[0]).query)["search_query"][0]

    def test_records_and_lookup(self):
        with patch.object(sources, "fetch", return_value=("", FEED)) as f:
            g, other = sources.arxiv_search("Instruction Following by Principled Attention Boosting (of LLMs)")
        self.assertEqual(self.search_query(f.call_args),
                         'ti:"Instruction Following by Principled Attention Boosting of LLMs"')
        self.assertEqual((g["title"], g["year"], g["doi"], g["url"], g["authors"]), (
            "Instruction Following by Principled Boosting Attention of Large Language Models", "2025",
            "10.48550/arXiv.2506.13734", "https://arxiv.org/abs/2506.13734", ["Vitoria Guardieiro", "Eric Wong"]))
        self.assertTrue(sources.is_preprint(g))
        self.assertEqual(other["doi"], "10.48550/arXiv.hep-th/9901001")
        with patch.object(sources, "doi_bib", return_value=BIB) as d:
            self.assertEqual(sources.lookup(g), (BIB, "DOI"))
        d.assert_called_once_with("10.48550/arXiv.2506.13734")
        # No DBLP record to build an entry from when DataCite fails.
        with patch.object(sources, "doi_bib", side_effect=net.FetchError("down")), \
             self.assertRaisesRegex(net.FetchError, "no BibTeX"):
            sources.lookup(g)

    def test_falls_back_to_words_and_skips_errors(self):
        error = ('<feed xmlns="http://www.w3.org/2005/Atom"><entry>'
                 '<id>http://arxiv.org/api/errors#bad</id><title>Error</title></entry></feed>')
        with patch.object(sources, "fetch", side_effect=[("", error), ("", FEED)]) as f:
            groups = sources.arxiv_search("principled attention boosting")
        self.assertEqual(self.search_query(f.call_args), "ti:principled AND ti:attention AND ti:boosting")
        self.assertEqual(len(groups), 2)

    def test_check_passes_a_feed(self):
        with patch.object(sources, "fetch", side_effect=lambda url, check: (check(FEED), ("", FEED))[1]):
            self.assertEqual(len(sources.arxiv_records(["x"])), 2)

    def test_rejects_non_feed(self):
        for page in ("<html><body>Down for maintenance</body></html", "<html/>", ""):
            with patch.object(sources, "fetch", side_effect=lambda url, check: check(page)), \
                 self.assertRaisesRegex(net.FetchError, "no Atom feed"):
                sources.arxiv_records(["x"])


class Lookup(unittest.TestCase):
    def test_metadata_entry(self):
        bib = sources.dblp_meta_bib(rec(pages="1-10"))
        self.assertEqual(bibtex.key(bib), "DBLP:conf/iclr/Doe21")
        self.assertEqual(bibtex.field(bib, "author"), "Jane Doe and Rick Roe")
        self.assertEqual(bibtex.field(bib, "booktitle"), "ICLR 2021")
        self.assertEqual(bibtex.field(bib, "pages"), "1--10")
        self.assertEqual(bibtex.field(bib, "journal"), "")

    def test_fallback_chain(self):
        r = rec(ee=["https://proceedings.mlr.press/v1/a.html"], doi="10.1/x")
        with patch.object(sources, "official_bib", side_effect=net.FetchError("boom")), \
             patch.object(sources, "doi_bib", return_value=BIB):
            bib, src = sources.lookup(r)
        self.assertEqual(bib, BIB)
        self.assertTrue(src.startswith("DOI (official PMLR failed: boom)"))
        with patch.object(sources, "official_bib", side_effect=net.RateLimited("429")):
            with self.assertRaises(net.RateLimited):
                sources.lookup(r)
        self.assertEqual(sources.lookup(rec())[1], "DBLP-metadata")

    def test_springer_doi_check(self):
        good = BIB.replace("Key_1", "10.1007/978-3-030-58452-8_13").replace("  doi = {10.1007/978-3-030-58452-8_13},\n", "")
        with patch.object(sources, "fetch", return_value=("", good)):
            self.assertEqual(sources.official_bib("Springer", "10.1007/978-3-030-58452-8_13"), good)
        with patch.object(sources, "fetch", return_value=("", BIB)):
            with self.assertRaises(net.FetchError):
                sources.official_bib("Springer", "10.1007/other")

    def test_neurips_new_link(self):
        page = "<a href='/paper_files/paper/22372-/bibtex' class='btn'>Bibtex</a>"
        cid = "https://proceedings.neurips.cc/paper_files/paper/2023/hash/" + "a" * 32 + "-Abstract-Conference.html"
        with patch.object(sources, "fetch", side_effect=[(cid, page), ("", BIB)]) as f:
            self.assertEqual(sources.official_bib("NeurIPS", cid), BIB)
        self.assertEqual(f.call_args.args[0], "https://proceedings.neurips.cc/paper_files/paper/22372-/bibtex")


class Net(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [patch.object(net, "CACHE", net.Path(self.tmp.name)), patch.object(net, "INTERVAL", 0)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def respond(self, text):
        r = io.BytesIO(text.encode())
        r.geturl = lambda: "https://final"
        return contextlib.nullcontext(r)

    def test_cache_and_check(self):
        with patch("urllib.request.urlopen", return_value=self.respond("ok")) as u:
            self.assertEqual(net.fetch("https://a", interval=0), ("https://final", "ok"))
            self.assertEqual(net.fetch("https://a", interval=0), ("https://final", "ok"))
        self.assertEqual(u.call_count, 1)
        self.assertIn("AI-Bib-Helper", u.call_args.args[0].get_header("User-agent"))

        def reject(_):
            raise net.FetchError("bad")
        for _ in range(2):  # a rejected response is not cached, so it is fetched again
            with patch("urllib.request.urlopen", return_value=self.respond("x")) as u, \
                 self.assertRaises(net.FetchError):
                net.fetch("https://b", check=reject, interval=0)
            self.assertEqual(u.call_count, 1)

    def test_bot_check_and_rate_limit(self):
        page = "<title>Making sure you&#39;re not a bot!</title>"
        with patch("urllib.request.urlopen", return_value=self.respond(page)), \
             self.assertRaisesRegex(net.FetchError, "bot check"):
            net.fetch("https://c", interval=0)
        err = urllib.error.HTTPError("https://d", 429, "Too Many", {}, io.BytesIO(b"Operation timed out."))
        with patch("urllib.request.urlopen", side_effect=err), self.assertRaises(net.RateLimited) as cm:
            net.fetch("https://d", interval=0)
        self.assertEqual(cm.exception.body, "Operation timed out.")

        class Dropped(io.BytesIO):  # the server closes the connection after the status line
            def read(self, *a):
                raise ConnectionResetError
        err = urllib.error.HTTPError("https://e", 503, "Unavailable", {}, Dropped())
        with patch("urllib.request.urlopen", side_effect=err), self.assertRaises(net.RateLimited) as cm:
            net.fetch("https://e", interval=0)
        self.assertEqual(cm.exception.body, "")
        self.assertFalse([f for f in os.listdir(self.tmp.name) if f.endswith(".json")])


class CLI(unittest.TestCase):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_get_with_key(self):
        with patch.object(sources, "search", return_value=[rec(versions=[rec()])]), \
             patch.object(sources, "lookup", return_value=(BIB, "PMLR")):
            code, out, err = self.run_cli("get", "a paper", "--key", "doe21")
        self.assertEqual(code, 0)
        self.assertEqual(bibtex.key(out), "doe21")
        self.assertIn("% PMLR | 2021 ICLR [published] A Paper", err)

    def test_get_doi_input_and_failure(self):
        with patch.object(sources, "doi_bib", return_value=BIB) as d:
            code, out, _ = self.run_cli("get", "https://doi.org/10.48550/arXiv.1706.03762")
        d.assert_called_once_with("10.48550/arXiv.1706.03762")
        self.assertEqual(code, 0)
        with patch.object(sources, "search", return_value=[]):
            code, out, err = self.run_cli("get", "nothing")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("no DBLP match", err)

    def test_arxiv_flag(self):
        g = rec(venue="arXiv", type="Informal", dblp="", year="2025", url="https://arxiv.org/abs/2506.13734")
        g["versions"] = [g]
        with patch.object(sources, "arxiv_search", return_value=[g]) as a, \
             patch.object(sources, "search") as s, \
             patch.object(sources, "lookup", return_value=(BIB, "DOI")):
            code, _, err = self.run_cli("get", "--arxiv", "a paper")
            self.assertEqual(self.run_cli("search", "--arxiv", "a paper")[:2],
                             (0, "1. " + cli.describe(g) + "\n   https://arxiv.org/abs/2506.13734\n"))
            [j] = json.loads(self.run_cli("search", "--arxiv", "--json", "a paper")[1])
        self.assertEqual((j["url"], j["versions"]), (g["url"], [g["url"]]))
        self.assertEqual((code, a.call_count, s.call_count), (0, 3, 0))
        self.assertIn("% DOI | 2025 arXiv [preprint] A Paper", err)

    def test_rate_limit_stops_batch(self):
        with patch.object(sources, "search", side_effect=net.RateLimited("429")) as s:
            code, _, err = self.run_cli("get", "a", "b")
        self.assertEqual((code, s.call_count), (2, 1))
        self.assertIn("stopped", err)


if __name__ == "__main__":
    unittest.main()
