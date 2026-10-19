"""A fake JDIH OJK site (httpx MockTransport): listings, detail pages and files built from small specs.

The pages follow the structure of the real ones in ``fixtures/jdih`` (checked 2026-10-16).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlsplit

import httpx
import pymupdf


def pdf(*pages: str) -> bytes:
    """A PDF whose pages carry the given text layers."""
    document = pymupdf.open()
    for text in pages:
        document.new_page().insert_text((72, 72), text, fontsize=9)
    return document.tobytes()


@dataclass
class Regulation:
    guid: str
    title: str
    number: str
    sector: str = "09"
    kind: str = "06"
    status: str = "Berlaku"
    enacted: str = "12-06-2026"
    promulgated: str = "06-07-2026"
    history: list[tuple[str, str, str]] = field(default_factory=list)  # (label, target, articles)
    legal_basis: list[str] = field(default_factory=list)
    files: list[tuple[str, str, bytes]] = field(default_factory=list)  # (label, name, bytes)
    views: int = 1


class FakeJdih:
    def __init__(self, regulations: list[Regulation]) -> None:
        self.regulations = {r.guid: r for r in regulations}
        self.requests: list[str] = []
        self.firewall = False

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(str(request.url))
        if self.firewall:
            return httpx.Response(200, html="<html><body>The requested URL was rejected. Please consult …</body></html>")
        path = request.url.path
        if path.endswith("/ListDataPeraturan"):
            query = parse_qs(urlsplit(str(request.url)).query)
            return httpx.Response(200, json=self._listing(query["sektor"][0], query["jenisPeraturan"][0]))
        if "/Detail/" in path:
            regulation = self.regulations.get(path.split("/Detail/")[1].split("/")[0])
            if regulation is None:
                return httpx.Response(404)
            regulation.views += 1  # the real page counts views: its bytes change on every read
            return httpx.Response(200, html=self._detail(regulation))
        if "/DownloadDokumen/" in path:
            guid, index = path.split("/DownloadDokumen/")[1].rsplit("-", 1)
            return httpx.Response(200, content=self.regulations[guid].files[int(index)][2],
                                  headers={"Content-Type": "application/octet-stream"})
        return httpx.Response(404)

    def _listing(self, sector: str, kind: str) -> dict:
        rows = [
            [f"<a href='http://jdih.ojk.go.id/Web/ViewPeraturan/Detail/{r.guid}/{r.sector}/{r.kind}'>{r.title}</a>",
             r.number, "Lembaga Pembiayaan", None, None, "Peraturan OJK", "", r.status]
            for r in self.regulations.values() if (r.sector, r.kind) == (sector, kind)
        ]
        return {"sEcho": None, "iTotalRecords": [len(rows)], "iTotalDisplayRecords": [len(rows)], "aaData": rows}

    @staticmethod
    def _detail(r: Regulation) -> str:
        def row(label: str, value: str) -> str:
            return (f'<tr><th><h4>{label}</h4></th><td><label style="font-size:16px">:</label></td>'
                    f'<td><label style="font-size:16px">{value}</label></td></tr>')

        basis = " <br/> ".join(f"<a href='/peraturan/peraturan/downloadfilelandasan/{n}'>{n}. {law}</a>"
                               for n, law in enumerate(r.legal_basis, start=1))
        history, current = [], None
        for label, target, articles in r.history:
            if label != current:
                history.append(f'<div class="d-flex"><span class="text-primary fw-bolder fs-6">{label} :</span></div>')
                current = label
            history.append(f'<span class="text-muted fw-bold text-muted d-block fs-7">1. <a class="text-danger" '
                           f'href="/Web/ViewPeraturan/DownloadFileRiwayat/1">{target}</a>'
                           + (f"\n. Pasal Terkait {articles}" if articles else "") + "</span><br />")
        files = "".join(
            f'<tr><th><h4>{label}</h4></th><td><label>:</label></td><td>'
            f'<a href="/Web/ViewPeraturan/PreviewDokumen/{r.guid}-{n}">&nbsp; {name}</a>'
            f'<a download href="/Web/ViewPeraturan/DownloadDokumen/{r.guid}-{n}">&nbsp; Unduh</a></td></tr>'
            for n, (label, name, _data) in enumerate(r.files)
        )
        return f"""<html><body>
<section class="page-heading"><h1>DETAIL PERATURAN</h1></section>
<div class="box-holder"><table class="table table-striped"><tbody>
{row("Judul", r.title)}{row("Nomor Peraturan", r.number)}{row("Tanggal Penetapan", r.enacted)}
{row("Tanggal Pengundangan", r.promulgated)}{row("Status Peraturan", r.status)}{row("Landasan Hukum", basis)}
<tr><th colspan="3"><label>Peraturan ini telah diakses {r.views} kali dan diunduh 0 kali</label></th></tr>
</tbody></table></div>
<h2><span>Riwayat Peraturan</span></h2><div class="box-holder"><div class="row">{"".join(history)}</div></div>
<h2><span>Dokumen</span></h2><div class="box-holder"><table><tbody>{files}</tbody></table></div>
</body></html>"""
