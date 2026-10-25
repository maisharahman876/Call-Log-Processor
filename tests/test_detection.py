import csv
import io
import json
import unittest
from pathlib import Path
from urllib.parse import urlencode

from call_logs_processor import NormalisationError, detect_provider, normalise


FIXTURES = Path(__file__).parent / "fixtures"


class DetectionTests(unittest.TestCase):
    def test_detects_provider_fixtures(self):
        for provider, filename in [("asterisk", "asterisk.csv"), ("twilio", "twilio_rest.json"),
                                   ("twilio", "twilio_webhooks.json"), ("ringcentral", "ringcentral.json")]:
            raw = (FIXTURES / filename).read_text(encoding="utf-8")
            payloads = [raw, ("\ufeff" + raw).encode()]

            if filename.endswith(".json"):
                payloads.append(json.loads(raw))

            for payload in payloads:
                with self.subTest(filename=filename, input_type=type(payload).__name__):
                    self.assertEqual(detect_provider(payload), provider)

    def test_single_json_resources(self):
        for provider, payload, expected_id in [
            ("twilio", {"sid": "CA-example", "status": "queued"}, "CA-example"),
            ("twilio", {"CallSid": "CA-webhook", "CallStatus": "ringing"}, "CA-webhook"),
            ("ringcentral", {"id": "rc-example", "startTime": "2026-10-24T08:00:00Z"}, "rc-example"),
        ]:
            with self.subTest(provider=provider, payload=payload):
                self.assertEqual(detect_provider(payload), provider)
                self.assertEqual(normalise(json.dumps(payload))[0].id, expected_id)

    def test_urlencoded_webhooks(self):
        payload = {"CallSid": "CA-webhook", "CallStatus": "completed", "From": "+442079460001"}
        body = urlencode(payload)

        for value in [body, body.encode(), body.replace("CallSid", "%43allSid")]:
            with self.subTest(value=value):
                self.assertEqual(detect_provider(value), "twilio")
                self.assertEqual(normalise(value)[0].caller, "+442079460001")

    def test_headered_csv_with_custom_order_and_recording(self):
        payload = 'disposition,recordingfile,dst,start,src\nANSWERED,calls/example.wav,201,2026-10-24 09:00:00,+442079460001\n'

        call = normalise(payload)[0]

        self.assertEqual(call.to_dict()["start"], "2026-10-24T08:00:00Z")
        self.assertEqual(call.callee, "201")
        self.assertEqual(call.recording, "calls/example.wav")

    def test_rejects_unsupported_headerless_csv(self):
        payload = '+442079460001,201,2026-10-24 09:00:00,ANSWERED\n'

        with self.assertRaisesRegex(NormalisationError, "cannot detect"):
            detect_provider(payload)

    def test_normalises_a_text_stream(self):
        payload = (FIXTURES / "twilio_rest.json").read_text(encoding="utf-8")

        calls = normalise(io.StringIO(payload))

        self.assertEqual(calls[0].id, "CA00000000000000000000000000000001")

    def test_value_text_and_nested_metadata_do_not_select_another_provider(self):
        payload = {
            "sid": "CA-example", "status": "queued",
            "from": '"startTime": "2026-10-24T08:00:00Z", "records": []',
            "metadata": {"startTime": "2026-10-24T08:00:00Z", "records": []},
        }

        self.assertEqual(detect_provider(json.dumps(payload)), "twilio")
        self.assertEqual(normalise(payload)[0].caller, payload["from"])

        unknown = {"description": '"CallSid": "CA-example"', "metadata": {"calls": []}}

        with self.assertRaisesRegex(NormalisationError, "cannot detect"):
            normalise(unknown)

    def test_csv_userfield_cannot_look_like_a_webhook(self):
        raw = (FIXTURES / "asterisk.csv").read_text(encoding="utf-8")
        rows = list(csv.reader(io.StringIO(raw)))
        rows[0][-1] = "note&CallSid=CA-fake&CallStatus=completed"

        stream = io.StringIO()
        csv.writer(stream).writerows(rows)

        self.assertEqual(detect_provider(stream.getvalue()), "asterisk")

    def test_ambiguous_or_mixed_json_is_rejected(self):
        for payload in [
            {"calls": [], "records": []},
            [{"sid": "CA-example"}, {"id": "rc-example", "startTime": "2026-10-24T08:00:00Z"}],
            {"calls": [{"id": "rc-example", "startTime": "2026-10-24T08:00:00Z"}]},
        ]:
            with self.subTest(payload=payload), self.assertRaisesRegex(NormalisationError, "ambiguous"):
                normalise(payload)

    def test_unknown_empty_and_malformed_input(self):
        for payload in ["", "   ", "plain text", "a,b,c\n1,2,3\n", ",".join(["unrelated"] * 16),
                        {}, [], None, 42, b"\xff", "{broken", '"unclosed']:
            with self.subTest(payload=payload), self.assertRaises(NormalisationError):
                normalise(payload)

    def test_recognisable_empty_envelopes(self):
        self.assertEqual(detect_provider({"calls": []}), "twilio")
        self.assertEqual(detect_provider({"records": []}), "ringcentral")

        self.assertEqual(normalise({"calls": []}), [])
        self.assertEqual(normalise({"records": []}), [])

    def test_detected_adapter_still_validates_records(self):
        with self.assertRaisesRegex(NormalisationError, "twilio: record 1: missing sid"):
            normalise({"calls": [{}]})

        with self.assertRaisesRegex(NormalisationError, "ringcentral: record 1: missing startTime"):
            normalise({"records": [{"id": "rc-incomplete"}]})


if __name__ == "__main__":
    unittest.main()
