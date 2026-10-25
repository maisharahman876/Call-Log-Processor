import copy
import csv
import io
import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

from call_logs_processor import (
    AsteriskCDRAdapter, Call, Direction, NormalisationError,
    RingCentralAdapter, Status, TwilioAdapter, normalise,
)
from call_logs_processor.adapters.asterisk import BASE_FIELDS, DEFAULT_FIELDS


FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def csv_text(rows):
    stream = io.StringIO(newline="")
    csv.writer(stream).writerows(rows)

    return stream.getvalue()


class AsteriskTests(unittest.TestCase):
    def setUp(self):
        self.payload = fixture("asterisk.csv")
        self.rows = list(csv.reader(io.StringIO(self.payload)))

        self.adapter = AsteriskCDRAdapter(
            timezone="Europe/London", extensions=["201", "202", "203"],
            inbound_contexts=["from-trunk"],
        )

    def test_provider_fixture(self):
        calls = self.adapter.parse(self.payload)

        self.assertEqual(len(calls), 4)
        self.assertEqual(calls[0].to_dict(), {
            "id": "1792832400.1", "start": "2026-10-24T08:00:00Z", "end": "2026-10-24T08:01:05Z",
            "direction": "inbound", "caller": "+442079460001", "callee": "201",
            "extension": "201", "status": "completed", "recording": None,
        })

        self.assertEqual([call.direction for call in calls], [Direction.INBOUND, Direction.OUTBOUND, Direction.INTERNAL, Direction.INBOUND])
        self.assertEqual(calls[1].extension, "202")
        self.assertEqual(calls[1].status, Status.BUSY)
        self.assertEqual(calls[3].status, Status.NO_ANSWER)

    def test_default_uk_timezone_handles_winter_and_summer(self):
        for day, utc_hour in [("2026-01-15", "09"), ("2026-07-15", "08")]:
            row = self.rows[0].copy()
            row[9], row[11] = f"{day} 09:00:00", f"{day} 09:01:00"

            with self.subTest(day=day):
                call = normalise(csv_text([row]))[0]

                self.assertEqual(call.to_dict()["start"], f"{day}T{utc_hour}:00:00Z")
                self.assertEqual(call.to_dict()["end"], f"{day}T{utc_hour}:01:00Z")

    def test_explicit_timezone_overrides_uk_default(self):
        row = self.rows[0].copy()
        row[9], row[11] = "2026-07-15 09:00:00", "2026-07-15 09:01:00"

        call = AsteriskCDRAdapter(timezone="UTC").parse(csv_text([row]))[0]

        self.assertEqual(call.to_dict()["start"], "2026-07-15T09:00:00Z")
        self.assertEqual(call.to_dict()["end"], "2026-07-15T09:01:00Z")

    def test_calls_across_clock_changes_have_correct_elapsed_time(self):
        for day, utc_start, utc_end, elapsed in [
            ("2026-03-29", "2026-03-29T00:59:30Z", "2026-03-29T01:00:30Z", 60),
            ("2026-10-25", "2026-10-24T23:59:30Z", "2026-10-25T02:00:30Z", 7260),
        ]:
            row = self.rows[0].copy()
            row[9], row[11] = f"{day} 00:59:30", f"{day} 02:00:30"

            with self.subTest(day=day):
                call = normalise(csv_text([row]))[0]

                self.assertEqual(call.to_dict()["start"], utc_start)
                self.assertEqual(call.to_dict()["end"], utc_end)
                self.assertEqual((call.end - call.start).total_seconds(), elapsed)

    def test_explicit_offsets_disambiguate_the_repeated_hour(self):
        row = self.rows[0].copy()
        row[9] = "2026-10-25T01:30:00+01:00"
        row[11] = "2026-10-25T01:30:00+00:00"

        call = normalise(csv_text([row]))[0]

        self.assertEqual(call.to_dict()["start"], "2026-10-25T00:30:00Z")
        self.assertEqual(call.to_dict()["end"], "2026-10-25T01:30:00Z")
        self.assertEqual((call.end - call.start).total_seconds(), 3600)

    def test_does_not_guess_direction_from_phone_length_or_dialplan_names(self):
        call = AsteriskCDRAdapter().parse(self.payload)[0]

        self.assertEqual(call.direction, Direction.UNKNOWN)
        self.assertIsNone(call.extension)

    def test_headers_recording_and_sequence(self):
        header = list(DEFAULT_FIELDS) + ["direction", "extension", "recordingfile", "sequence"]
        row = self.rows[0] + ["Inbound", "0201", "calls/example.wav", "7"]

        call = self.adapter.parse(csv_text([header, row]))[0]

        self.assertEqual(call.id, "1792832400.1:7")
        self.assertEqual(call.extension, "0201")
        self.assertEqual(call.recording, "calls/example.wav")

    def test_newcdrcolumns(self):
        row = self.rows[0] + ["peer", "linked-1", "2"]

        call = normalise(csv_text([row]))[0]

        self.assertEqual(call.id, "1792832400.1:2")

    def test_optional_uniqueid_and_stable_fallback(self):
        for count in (16, 17, 18):
            with self.subTest(columns=count):
                payload = csv_text([self.rows[0][:count]])
                first = self.adapter.parse(payload)[0]

                if count == 16:
                    self.assertEqual(first.id, self.adapter.parse(payload)[0].id)
                    self.assertTrue(first.id.startswith("asterisk-"))

                    altered = self.rows[0][:count]
                    altered[2] = "202"
                    self.assertNotEqual(first.id, self.adapter.parse(csv_text([altered]))[0].id)
                else:
                    self.assertEqual(first.id, "1792832400.1")

    def test_custom_layout_with_userfield_but_no_uniqueid(self):
        fields = BASE_FIELDS + ("userfield",)
        row = self.rows[0][:16] + ["not-a-call-id"]

        call = AsteriskCDRAdapter(fieldnames=fields).parse(csv_text([row]))[0]

        self.assertTrue(call.id.startswith("asterisk-"))

    def test_bom_bytes_stream_and_empty(self):
        expected = self.adapter.parse(self.payload)

        self.assertEqual(self.adapter.parse(("\ufeff" + self.payload).encode()), expected)
        self.assertEqual(self.adapter.parse(io.StringIO("\ufeff" + self.payload)), expected)
        self.assertEqual(self.adapter.parse("\n\n"), [])

    def test_explicit_contexts_and_custom_recording_column(self):
        adapter = AsteriskCDRAdapter(outbound_contexts=["from-internal"], recording_field="audio")
        header = list(DEFAULT_FIELDS) + ["audio"]

        call = adapter.parse(csv_text([header, self.rows[1] + ["outbound.wav"]]))[0]

        self.assertEqual(call.direction, Direction.OUTBOUND)
        self.assertIsNone(call.extension)
        self.assertEqual(call.recording, "outbound.wav")

    def test_dispositions(self):
        for disposition, expected in [("CONGESTION", Status.FAILED), ("FAILED", Status.FAILED), ("FUTURE", Status.UNKNOWN)]:
            row = self.rows[0].copy()
            row[14] = disposition

            self.assertEqual(self.adapter.parse(csv_text([row]))[0].status, expected)

    def test_rejects_invalid_csv_layout_and_timestamps(self):
        for payload in ["a,b,c\n", '"unclosed', csv_text([list(DEFAULT_FIELDS), self.rows[0][:-1]])]:
            with self.subTest(payload=payload), self.assertRaises(NormalisationError):
                self.adapter.parse(payload)

        for value in ("not-a-date", "", "2026-10-24 09:02:00"):
            row = self.rows[0].copy()
            row[9] = value

            with self.subTest(start=value), self.assertRaisesRegex(NormalisationError, "asterisk: row"):
                self.adapter.parse(csv_text([row]))

    def test_rejects_ambiguous_and_nonexistent_local_time(self):
        for field, index in [("start", 9), ("end", 11)]:
            for value, reason in [("2026-10-25 01:30:00", "ambiguous"), ("2026-03-29 01:30:00", "nonexistent")]:
                row = self.rows[0].copy()
                row[9], row[11] = value[:10] + " 00:30:00", ""
                row[index] = value

                with self.subTest(field=field, value=value), self.assertRaisesRegex(
                    NormalisationError, f"{field} is an? {reason} local time in Europe/London"
                ):
                    normalise(csv_text([row]))

    def test_rejects_invalid_configuration(self):
        for kwargs in [{"timezone": "Invalid/Zone"}, {"fieldnames": ["src", "dst"]},
                       {"fieldnames": list(DEFAULT_FIELDS) + ["src"]},
                       {"inbound_contexts": ["same"], "outbound_contexts": ["same"]}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(NormalisationError):
                AsteriskCDRAdapter(**kwargs)


class TwilioTests(unittest.TestCase):
    def setUp(self):
        self.adapter = TwilioAdapter()
        self.rest = json.loads(fixture("twilio_rest.json"))
        self.webhooks = json.loads(fixture("twilio_webhooks.json"))

    def test_rest_fixture(self):
        calls = self.adapter.parse(fixture("twilio_rest.json"))

        self.assertEqual(len(calls), 4)
        self.assertEqual(calls[0].to_dict(), {
            "id": "CA00000000000000000000000000000001", "start": "2026-10-24T08:00:00Z",
            "end": "2026-10-24T08:01:00Z", "direction": "inbound",
            "caller": "+442079460001", "callee": "+442079460002", "extension": None,
            "status": "completed", "recording": None,
        })

        self.assertEqual(calls[1].caller, "client:agent-201")
        self.assertEqual(calls[1].callee, "sip:desk@example.test")
        self.assertEqual(calls[1].direction, Direction.OUTBOUND)
        self.assertIsNone(calls[1].end)
        self.assertEqual(calls[2].status, Status.NO_ANSWER)
        self.assertIsNone(calls[2].end)
        self.assertIsNone(calls[3].start)

    def test_webhook_fixture_is_a_set_of_snapshots(self):
        calls = self.adapter.parse(fixture("twilio_webhooks.json"))

        self.assertEqual([call.status for call in calls], [
            Status.INITIATED, Status.RINGING, Status.IN_PROGRESS, Status.COMPLETED,
        ])
        self.assertEqual(len({call.id for call in calls}), 1)

        self.assertEqual(calls[0].start.isoformat(), "2026-10-24T08:20:00+00:00")
        self.assertIsNone(calls[0].end)
        self.assertIsNone(calls[1].start)
        self.assertIsNone(calls[1].end)
        self.assertIsNone(calls[2].start)
        self.assertIsNone(calls[2].end)
        self.assertIsNone(calls[3].start)
        self.assertEqual(calls[3].end.isoformat(), "2026-10-24T08:21:05+00:00")
        self.assertIn("/Recordings/RE", calls[3].recording)

    def test_urlencoded_status_callback(self):
        row = self.webhooks[-1]

        self.assertEqual(self.adapter.parse(urlencode(row).encode()), self.adapter.parse(row))
        self.assertEqual(self.adapter.parse(urlencode(row))[0].caller, "+442079460001")

    def test_terminal_statuses_use_timestamp_as_end_only(self):
        for status in ("completed", "busy", "failed", "no-answer", "canceled"):
            row = dict(self.webhooks[-1], CallStatus=status, CallDuration="0")

            with self.subTest(status=status):
                call = self.adapter.parse(row)[0]

                self.assertEqual(call.status.value, status)
                self.assertIsNotNone(call.end)
                self.assertIsNone(call.start)

    def test_basic_webhook_without_progress_timestamp(self):
        row = {"CallSid": "CA-basic", "CallStatus": "completed", "From": "anonymous"}

        call = self.adapter.parse(row)[0]

        self.assertIsNone(call.start)
        self.assertIsNone(call.end)
        self.assertEqual(call.direction, Direction.UNKNOWN)
        self.assertIsNone(call.callee)

    def test_unknown_status_and_direction(self):
        row = {"sid": "CA-future", "status": "new-provider-status", "direction": "new-direction"}

        call = self.adapter.parse(row)[0]

        self.assertEqual(call.status, Status.UNKNOWN)
        self.assertEqual(call.direction, Direction.UNKNOWN)

    def test_all_rest_statuses(self):
        for status in ("queued", "ringing", "in-progress", "completed", "busy", "failed", "no-answer", "canceled"):
            with self.subTest(status=status):
                call = self.adapter.parse({"sid": "CA-status", "status": status})[0]

                self.assertEqual(call.status.value, status)

    def test_single_resource_list_and_json_bytes(self):
        resource = self.rest["calls"][0]
        expected = self.adapter.parse([resource])

        self.assertEqual(self.adapter.parse(resource), expected)
        self.assertEqual(self.adapter.parse(json.dumps(resource).encode()), expected)
        self.assertEqual(self.adapter.parse({"calls": []}), [])

    def test_invalid_records_and_payloads(self):
        for payload in [{}, {"CallSid": ""}, {"sid": "CA-x", "start_time": "bad"},
                        {"sid": "CA-x", "start_time": "2026-10-24 08:00:00"},
                        {"calls": None}, {"calls": [42]}, "{broken", "null", b"\xff",
                        "CallSid=CA1&CallSid=CA2"]:
            with self.subTest(payload=payload), self.assertRaises(NormalisationError):
                self.adapter.parse(payload)


class RingCentralTests(unittest.TestCase):
    def setUp(self):
        self.adapter = RingCentralAdapter()
        self.pages = json.loads(fixture("ringcentral.json"))

    def test_paginated_fixture(self):
        calls = self.adapter.parse(fixture("ringcentral.json"))

        self.assertEqual([call.id for call in calls], [
            "rc-call-001", "rc-call-002", "rc-call-003", "rc-call-004",
        ])
        self.assertEqual(calls[0].to_dict(), {
            "id": "rc-call-001", "start": "2026-10-24T08:00:00.125000Z",
            "end": "2026-10-24T08:01:05.375000Z", "direction": "inbound",
            "caller": "+442079460001", "callee": "+442079460002", "extension": "0201",
            "status": "completed",
            "recording": "https://media.ringcentral.com/restapi/v1.0/account/~/recording/recording-001/content",
        })

        self.assertEqual(calls[1].direction, Direction.OUTBOUND)
        self.assertEqual(calls[1].extension, "202")
        self.assertEqual(calls[1].start.hour, 8)
        self.assertEqual(calls[1].status, Status.BUSY)
        self.assertEqual(calls[2].callee, "203")
        self.assertEqual(calls[2].status, Status.NO_ANSWER)
        self.assertEqual(calls[3].status, Status.COMPLETED)
        self.assertEqual(calls[3].extension, "202")

    def test_follows_next_page_using_fetcher(self):
        requested = []

        def fetch(uri):
            requested.append(uri)
            self.assertEqual(uri, self.pages[1]["uri"])
            return json.dumps(self.pages[1])

        calls = list(self.adapter.iter_calls(self.pages[0], fetch))

        self.assertEqual(calls, self.adapter.parse_pages(self.pages))
        self.assertEqual(requested, [self.pages[1]["uri"]])

    def test_pagination_cycle_is_rejected(self):
        pages = copy.deepcopy(self.pages)
        pages[1]["navigation"]["nextPage"] = {"uri": pages[0]["uri"]}

        with self.assertRaisesRegex(NormalisationError, "cycle"):
            list(self.adapter.iter_calls(pages[0], lambda uri: pages[1]))

    def test_pagination_cycle_without_response_uri_is_rejected(self):
        page = {"records": [], "navigation": {"nextPage": {"uri": "same"}}}

        with self.assertRaisesRegex(NormalisationError, "cycle"):
            list(self.adapter.iter_calls(page, lambda uri: page))

    def test_missing_next_page_is_not_silently_truncated_when_fetching(self):
        page = copy.deepcopy(self.pages[0])
        del page["navigation"]["nextPage"]

        with self.assertRaisesRegex(NormalisationError, "more pages"):
            list(self.adapter.iter_calls(page, lambda uri: self.fail("must not fetch")))

    def test_active_call_has_no_end(self):
        row = dict(self.pages[0]["records"][0], result="In Progress")

        call = self.adapter.parse(row)[0]

        self.assertEqual(call.status, Status.IN_PROGRESS)
        self.assertIsNone(call.end)

    def test_zero_duration_and_null_optional_fields(self):
        row = {"id": 123, "type": "Voice", "startTime": "2026-10-24T08:00:00Z", "duration": 0,
               "from": None, "to": None, "recording": None, "extension": None}

        call = self.adapter.parse(row)[0]

        self.assertEqual(call.id, "123")
        self.assertEqual(call.end, call.start)
        self.assertEqual(call.status, Status.UNKNOWN)
        self.assertEqual(call.direction, Direction.UNKNOWN)
        self.assertIsNone(call.caller)
        self.assertIsNone(call.recording)

    def test_missing_duration_leaves_end_unknown(self):
        row = dict(self.pages[0]["records"][0])
        del row["duration"]
        del row["durationMs"]

        self.assertIsNone(self.adapter.parse(row)[0].end)

    def test_extension_id_and_recording_metadata_uri_are_not_output_values(self):
        row = dict(self.pages[0]["records"][0], to={"phoneNumber": "+442079460002"},
                   extension={"id": 900000001}, recording={"id": "rec-1", "uri": "metadata-url"})

        call = self.adapter.parse(row)[0]

        self.assertIsNone(call.extension)
        self.assertIsNone(call.recording)

    def test_other_results(self):
        for result, expected in [("Voicemail", Status.VOICEMAIL), ("Rejected", Status.REJECTED),
                                 ("Hang Up", Status.CANCELED), ("Failed", Status.FAILED), ("Future", Status.UNKNOWN)]:
            row = dict(self.pages[0]["records"][0], result=result)
            with self.subTest(result=result):
                self.assertEqual(self.adapter.parse(row)[0].status, expected)

    def test_skips_nonvoice_records(self):
        records = self.pages[0]["records"] + [{"id": "fax-1", "type": "Fax"}]

        calls = self.adapter.parse({"records": records})

        self.assertEqual([call.id for call in calls], ["rc-call-001", "rc-call-002"])

    def test_invalid_records_and_pages(self):
        base = self.pages[0]["records"][0]
        invalid = [{}, {"id": "missing-start"}, {"records": None}, {"records": [42]},
                   dict(base, durationMs=-1), dict(base, durationMs="NaN"), dict(base, durationMs=True),
                   dict(base, durationMs={}, duration=1), dict(base, to="not-an-object"),
                   dict(base, startTime="bad"), dict(base, recording=[])]

        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(NormalisationError):
                self.adapter.parse(payload)

        with self.assertRaisesRegex(NormalisationError, "page 2"):
            self.adapter.parse_pages([self.pages[0], {"records": [{}]}])

        with self.assertRaises(NormalisationError):
            self.adapter.parse_pages([{"not-records": []}])

        with self.assertRaises(NormalisationError):
            list(self.adapter.iter_calls({"records": [], "navigation": []}, lambda uri: {}))


class ContractTests(unittest.TestCase):
    def test_every_provider_produces_exact_shared_fields_and_utc(self):
        fields = {"id", "start", "end", "direction", "caller", "callee", "extension", "status", "recording"}
        for provider, filename in [("asterisk", "asterisk.csv"), ("twilio", "twilio_rest.json"),
                                   ("twilio", "twilio_webhooks.json"), ("ringcentral", "ringcentral.json")]:
            with self.subTest(provider=provider, filename=filename):
                for call in normalise(fixture(filename)):
                    self.assertIsInstance(call, Call)
                    result = call.to_dict()

                    self.assertEqual(set(result), fields)
                    json.dumps(result)

                    for value in (call.start, call.end):
                        if value is not None:
                            self.assertEqual(value.utcoffset(), timedelta(0))

    def test_public_model_invariants(self):
        base = dict(id="call", start=None, end=None, direction=Direction.UNKNOWN,
                    caller=None, callee=None, extension=None, status=Status.UNKNOWN, recording=None)

        for changes in [{"id": ""}, {"start": datetime(2026, 10, 24)}, {"status": "completed"},
                        {"direction": "inbound"}, {"caller": 123},
                        {"start": datetime(2026, 10, 24, 9, tzinfo=timezone.utc),
                         "end": datetime(2026, 10, 24, 8, tzinfo=timezone.utc)}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                Call(**(base | changes))

        call = Call(**(base | {"start": datetime(2026, 10, 24, 9, tzinfo=timezone(timedelta(hours=1)))}))

        self.assertEqual(call.to_dict()["start"], "2026-10-24T08:00:00Z")


if __name__ == "__main__":
    unittest.main()
