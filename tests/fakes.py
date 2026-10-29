"""Test doubles: synthetic arXiv Atom feeds, a fake arXiv server, a fake clock, a fake S3 client."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from docpulse.ingest.arxiv_client import FetchedPage, parse_feed

UTC = timezone.utc


def entry_xml(arxiv_id: str, updated: datetime) -> str:
    ts = updated.strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"""<entry>
  <id>http://arxiv.org/abs/{arxiv_id}</id>
  <updated>{ts}</updated>
  <published>{ts}</published>
  <title>Title {arxiv_id}</title>
  <summary>Abstract of {arxiv_id}</summary>
  <author><name>A. Author</name></author>
  <category term="cs.LG" scheme="http://arxiv.org/schemas/atom"/>
</entry>"""


def feed_xml(entries: list[tuple[str, datetime]], *, start: int, total: int, per_page: int = 200) -> bytes:
    body = "\n".join(entry_xml(i, u) for i, u in entries)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
  <title>ArXiv Query</title>
  <id>http://arxiv.org/api/test</id>
  <updated>2026-10-27T00:00:00-04:00</updated>
  <opensearch:totalResults>{total}</opensearch:totalResults>
  <opensearch:startIndex>{start}</opensearch:startIndex>
  <opensearch:itemsPerPage>{per_page}</opensearch:itemsPerPage>
{body}
</feed>""".encode()


ERROR_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
  <opensearch:totalResults>1</opensearch:totalResults>
  <opensearch:startIndex>0</opensearch:startIndex>
  <opensearch:itemsPerPage>1</opensearch:itemsPerPage>
  <entry><id>http://arxiv.org/api/errors#incorrect_id_format</id>
  <title>Error</title><summary>incorrect id format</summary></entry>
</feed>"""


def make_dataset(n: int, newest: datetime, step_minutes: int = 10) -> list[tuple[str, datetime]]:
    """n entries sorted by updated descending, one every ``step_minutes``."""
    return [(f"2610.{i:05d}v1", newest - timedelta(minutes=step_minutes * i)) for i in range(n)]


class FakeArxiv:
    """Stands in for ArxivClient: serves slices of a fixed dataset like the real API."""

    def __init__(self, dataset: list[tuple[str, datetime]], fail_on_call: int | None = None) -> None:
        self.dataset = dataset
        self.fail_on_call = fail_on_call
        self.calls = 0

    def fetch_page(self, *, search_query, start, max_results, sort_by, sort_order) -> FetchedPage:
        self.calls += 1
        if self.fail_on_call == self.calls:
            raise RuntimeError("simulated arXiv outage")
        chunk = self.dataset[start : start + max_results]
        raw = feed_xml(chunk, start=start, total=len(self.dataset), per_page=max_results)
        return FetchedPage(raw=raw, page=parse_feed(raw), attempts=1)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FakeResponse:
    def __init__(self, status: int = 200, content: bytes = b"", headers: dict | None = None) -> None:
        self.status_code = status
        self.content = content
        self.text = content.decode("utf-8", "replace")
        self.headers = headers or {}


class FakeSession:
    """Returns scripted responses (or raises scripted exceptions) in order."""

    def __init__(self, script: list, clock: "FakeClock | None" = None) -> None:
        self.script = list(script)
        self.requests: list[dict] = []
        self.clock = clock
        self.times: list[float] = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.requests.append({"url": url, "params": params, "headers": headers, "timeout": timeout})
        if self.clock is not None:
            self.times.append(self.clock.now)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeClientError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class FakeS3Client:
    """Just enough of the boto3 S3 client for S3Store."""

    def __init__(self, page_size: int = 2) -> None:
        self.objects: dict[str, bytes] = {}
        self.buckets: set[str] = set()
        self.delete_calls: list[int] = []
        self.page_size = page_size
        self.head_failures: list[Exception] = []

    def head_bucket(self, Bucket):  # noqa: N803
        if self.head_failures:
            raise self.head_failures.pop(0)
        if Bucket not in self.buckets:
            raise FakeClientError("404")

    def create_bucket(self, **kwargs):
        self.buckets.add(kwargs["Bucket"])

    def put_object(self, Bucket, Key, Body, ContentType):  # noqa: N803
        self.objects[Key] = Body

    def get_object(self, Bucket, Key):  # noqa: N803
        if Key not in self.objects:
            raise FakeClientError("NoSuchKey")
        data = self.objects[Key]

        class _Body:
            def read(self_inner):  # noqa: N805
                return data

        return {"Body": _Body()}

    def get_paginator(self, name):
        client = self

        class _Paginator:
            def paginate(self_inner, Bucket, Prefix):  # noqa: N803, N805
                keys = sorted(k for k in client.objects if k.startswith(Prefix))
                for i in range(0, max(len(keys), 1), client.page_size):
                    chunk = keys[i : i + client.page_size]
                    yield {"Contents": [{"Key": k} for k in chunk]} if chunk else {}

        return _Paginator()

    def delete_objects(self, Bucket, Delete):  # noqa: N803
        self.delete_calls.append(len(Delete["Objects"]))
        for obj in Delete["Objects"]:
            self.objects.pop(obj["Key"], None)
        return {}
