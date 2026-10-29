import unittest
from datetime import datetime, timezone

import requests

from docpulse.config import ArxivSettings
from docpulse.ingest.arxiv_client import (
    ArxivAPIError,
    ArxivClient,
    ArxivParseError,
    ArxivRetriesExhausted,
    RateLimiter,
    build_search_query,
    parse_feed,
)
from fakes import ERROR_FEED, FakeClock, FakeResponse, FakeSession, feed_xml

T = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)


def good_page(start=0, total=1):
    return feed_xml([("2610.00001v2", T)], start=start, total=total)


def make_client(script, **settings_kw):
    clock = FakeClock()
    settings = ArxivSettings(**settings_kw)
    session = FakeSession(script, clock)
    limiter = RateLimiter(settings.min_interval_s, clock=clock.monotonic, sleep=clock.sleep)
    client = ArxivClient(settings, session=session, limiter=limiter, sleep=clock.sleep, jitter=lambda: 0.0)
    return client, session, clock


class ParseFeedTests(unittest.TestCase):
    def test_parses_counts_and_entries(self):
        page = parse_feed(feed_xml([("2610.00001v2", T), ("hep-th/9901001v1", T)], start=0, total=77))
        self.assertEqual(page.total_results, 77)
        self.assertEqual([e.arxiv_id for e in page.entries], ["2610.00001v2", "hep-th/9901001v1"])
        self.assertEqual(page.entries[0].base_id, "2610.00001")
        self.assertEqual(page.entries[1].updated, T)

    def test_error_entry_is_non_retryable_api_error(self):
        with self.assertRaises(ArxivAPIError):
            parse_feed(ERROR_FEED)

    def test_malformed_and_wrong_root_raise_parse_error(self):
        with self.assertRaises(ArxivParseError):
            parse_feed(b"<feed><unclosed>")
        with self.assertRaises(ArxivParseError):
            parse_feed(b"<html></html>")

    def test_missing_totalresults_raises(self):
        with self.assertRaises(ArxivParseError):
            parse_feed(b'<feed xmlns="http://www.w3.org/2005/Atom"></feed>')

    def test_xml_entity_bomb_is_rejected(self):
        bomb = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]><feed xmlns="http://www.w3.org/2005/Atom">&b;</feed>'
        with self.assertRaises(ArxivParseError):
            parse_feed(bomb)

    def test_build_search_query(self):
        self.assertEqual(build_search_query(("cs.LG", "cs.CL")), "cat:cs.LG OR cat:cs.CL")


class RateLimiterTests(unittest.TestCase):
    def test_spaces_request_starts(self):
        clock = FakeClock()
        limiter = RateLimiter(3.1, clock=clock.monotonic, sleep=clock.sleep)
        starts = []
        for _ in range(4):
            limiter.wait()
            starts.append(clock.now)
            clock.now += 0.2  # the "request" itself takes 0.2s
        gaps = [b - a for a, b in zip(starts, starts[1:])]
        self.assertTrue(all(g >= 3.1 - 1e-9 for g in gaps), gaps)

    def test_first_call_does_not_sleep(self):
        clock = FakeClock()
        RateLimiter(3.1, clock=clock.monotonic, sleep=clock.sleep).wait()
        self.assertEqual(clock.sleeps, [])


class ConfigGuardTests(unittest.TestCase):
    def test_interval_below_arxiv_rule_is_rejected(self):
        from docpulse.config import ConfigError

        with self.assertRaises(ConfigError):
            ArxivSettings(min_interval_s=2.0)

    def test_placeholder_contact_is_flagged(self):
        self.assertFalse(ArxivSettings().has_contact_email)
        self.assertFalse(ArxivSettings(contact_email="you@example.com").has_contact_email)
        self.assertTrue(ArxivSettings(contact_email="dev@mylab.org").has_contact_email)

    def test_from_env(self):
        s = ArxivSettings.from_env({"ARXIV_CATEGORIES": "cs.LG, stat.ML", "ARXIV_PAGE_SIZE": "50"})
        self.assertEqual(s.categories, ("cs.LG", "stat.ML"))
        self.assertEqual(s.page_size, 50)


class FetchPageTests(unittest.TestCase):
    def test_success_sends_user_agent_and_params(self):
        client, session, _ = make_client([FakeResponse(200, good_page())], contact_email="me@example.org")
        out = client.fetch_page(search_query="cat:cs.LG", start=0, max_results=10)
        self.assertEqual(out.attempts, 1)
        req = session.requests[0]
        self.assertIn("me@example.org", req["headers"]["User-Agent"])
        self.assertEqual(req["params"]["sortBy"], "lastUpdatedDate")
        self.assertEqual(req["params"]["max_results"], 10)

    def test_retries_503_then_429_with_retry_after_then_succeeds(self):
        script = [FakeResponse(503), FakeResponse(429, headers={"Retry-After": "40"}), FakeResponse(200, good_page())]
        client, _, clock = make_client(script, backoff_base_s=5.0)
        out = client.fetch_page(search_query="q", start=0, max_results=10)
        self.assertEqual(out.attempts, 3)
        # first backoff 5s; second would be 10s but Retry-After says 40s
        self.assertEqual(clock.sleeps[0], 5.0)
        self.assertGreaterEqual(clock.sleeps[1], 40.0)

    def test_requests_remain_spaced_even_across_retries(self):
        script = [requests.ConnectionError("boom"), FakeResponse(200, good_page())]
        client, session, clock = make_client(script)
        client.fetch_page(search_query="q", start=0, max_results=10)
        self.assertEqual(len(session.requests), 2)
        self.assertGreaterEqual(session.times[1] - session.times[0], 3.0)  # arXiv ToU floor

    def test_consecutive_pages_are_spaced(self):
        client, session, _ = make_client([FakeResponse(200, good_page()), FakeResponse(200, good_page())])
        client.fetch_page(search_query="q", start=0, max_results=1)
        client.fetch_page(search_query="q", start=1, max_results=1)
        self.assertGreaterEqual(session.times[1] - session.times[0], 3.0)

    def test_network_error_retried(self):
        client, _, _ = make_client([requests.Timeout("slow"), FakeResponse(200, good_page())])
        self.assertEqual(client.fetch_page(search_query="q", start=0, max_results=1).attempts, 2)

    def test_client_error_not_retried(self):
        client, session, _ = make_client([FakeResponse(400, b"bad query")])
        with self.assertRaises(ArxivAPIError):
            client.fetch_page(search_query="q", start=0, max_results=1)
        self.assertEqual(len(session.requests), 1)

    def test_error_entry_not_retried(self):
        client, session, _ = make_client([FakeResponse(200, ERROR_FEED)])
        with self.assertRaises(ArxivAPIError):
            client.fetch_page(search_query="q", start=0, max_results=1)
        self.assertEqual(len(session.requests), 1)

    def test_empty_page_despite_total_is_retried(self):
        empty = feed_xml([], start=0, total=500)
        client, _, _ = make_client([FakeResponse(200, empty), FakeResponse(200, good_page(total=500))])
        self.assertEqual(client.fetch_page(search_query="q", start=0, max_results=1).attempts, 2)

    def test_empty_page_past_the_end_is_legitimate(self):
        past_end = feed_xml([], start=500, total=500)
        client, session, _ = make_client([FakeResponse(200, past_end)])
        out = client.fetch_page(search_query="q", start=500, max_results=10)
        self.assertEqual(out.attempts, 1)
        self.assertEqual(len(session.requests), 1)

    def test_gives_up_after_max_attempts(self):
        client, session, _ = make_client([FakeResponse(503)] * 3, max_attempts=3)
        with self.assertRaises(ArxivRetriesExhausted):
            client.fetch_page(search_query="q", start=0, max_results=1)
        self.assertEqual(len(session.requests), 3)

    def test_truncated_xml_is_retried(self):
        client, _, _ = make_client([FakeResponse(200, good_page()[:120]), FakeResponse(200, good_page())])
        self.assertEqual(client.fetch_page(search_query="q", start=0, max_results=1).attempts, 2)


if __name__ == "__main__":
    unittest.main()
