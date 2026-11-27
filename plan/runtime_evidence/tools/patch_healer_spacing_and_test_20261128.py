"""Add the blank line the rewrite lost, and the regression test.

The test states the rule the merge now follows: a stored measurement that
can show the ceiling it was searched inside is never replaced by a summary
of one.
"""

from pathlib import Path

HEALER = Path("scripts/mainnet_arbitrage_healer.py")
TESTS = Path("scripts/tests/test_mainnet_arbitrage_healer.py")

LF = chr(10)
CR = chr(13)

NEW_TEST = '''
    def test_a_complete_measurement_is_not_replaced_by_a_summary(self):
        """The runtime's measurement is the complete one.

        The runtime publishes every review with the amount ladder, the
        ceiling the ladder searched inside and the route it priced.  The
        orchestrator's manual review only carries a summary of the same
        measurement, because the record table holds no ladder.  A
        submission that prefers the summary replaces a complete measurement
        with a summary of it, and the review library loses the ceiling that
        says the amount was inside the bound the review enforces.
        """
        loop_key = '0x0000000000000000000000000000000000000000:0x11:0x22'
        complete = {
            'loopKey': loop_key,
            'staticPreview': {
                'source': 'native_rust_review',
                'searchCeilingRaw': '0x1',
                'searchCeilingBasis': 'single_tick_capacity',
                'bestAmountCalculation': {'bestAmountMeasured': True, 'probeCount': 6},
            },
            'runArbitrage': {'ok': False, 'executable': True,
                             'signedPnl': {'raw': '-1 USDG', 'amount': -1.0,
                                           'currency': 'USDG', 'unit': 'token',
                                           'source': 'runArbitrage'}},
        }
        summary = {
            'loopKey': loop_key,
            'staticPreview': {
                'bestAmountCalculation': {'bestAmountMeasured': True, 'probeCount': 6},
            },
            'runArbitrage': {'ok': False, 'executable': True,
                             'signedPnl': {'raw': '-2 USDG', 'amount': -2.0,
                                           'currency': 'USDG', 'unit': 'token',
                                           'source': 'runArbitrage'}},
        }
        with mock.patch.object(HEALER, 'stored_review_evidence', return_value=[complete]):
            with mock.patch.object(HEALER, 'manager_loop_rows', return_value=[]):
                merged, retired, kept = HEALER.merge_with_stored_evidence(
                    Path('unused.sqlite3'), None, [summary])
        self.assertEqual(len(merged), 1, merged)
        self.assertEqual(retired, 0)
        self.assertEqual(kept, [loop_key])
        # The stored measurement is the one that is carried: the summary
        # of it is not submitted.
        self.assertEqual(merged[0]['staticPreview']['searchCeilingRaw'], '0x1')

    def test_a_fresh_measurement_replaces_a_stored_summary(self):
        """A gap is still filled.

        The stored row cannot show the ceiling, so the fresh measurement
        that can is the one that is carried.
        """
        loop_key = '0x0000000000000000000000000000000000000000:0x11:0x22'
        stored = {
            'loopKey': loop_key,
            'staticPreview': {
                'bestAmountCalculation': {'bestAmountMeasured': True, 'probeCount': 6},
            },
            'runArbitrage': {'ok': False, 'executable': True,
                             'signedPnl': {'raw': '-1 USDG', 'amount': -1.0,
                                           'currency': 'USDG', 'unit': 'token',
                                           'source': 'runArbitrage'}},
        }
        fresh = {
            'loopKey': loop_key,
            'staticPreview': {
                'source': 'native_rust_review',
                'searchCeilingRaw': '0x1',
                'searchCeilingBasis': 'single_tick_capacity',
                'bestAmountCalculation': {'bestAmountMeasured': True, 'probeCount': 6},
            },
            'runArbitrage': {'ok': False, 'executable': True,
                             'signedPnl': {'raw': '-2 USDG', 'amount': -2.0,
                                           'currency': 'USDG', 'unit': 'token',
                                           'source': 'runArbitrage'}},
        }
        with mock.patch.object(HEALER, 'stored_review_evidence', return_value=[stored]):
            with mock.patch.object(HEALER, 'manager_loop_rows', return_value=[]):
                merged, retired, kept = HEALER.merge_with_stored_evidence(
                    Path('unused.sqlite3'), None, [fresh])
        self.assertEqual(len(merged), 1, merged)
        self.assertEqual(retired, 0)
        self.assertEqual(kept, [])
        self.assertEqual(merged[0]['staticPreview']['searchCeilingRaw'], '0x1')
'''


def patch() -> None:
    text = HEALER.read_bytes().decode("utf-8")
    crlf = CR + LF in text
    text = text.replace(CR + LF, LF)
    anchor = ('    return "searchCeilingRaw" in preview'
              ' and "searchCeilingBasis" in preview' + LF)
    if anchor not in text:
        raise SystemExit("anchor not found: the ceiling predicate")
    text = text.replace(anchor, anchor + LF, 1)
    if crlf:
        text = text.replace(LF, CR + LF)
    HEALER.write_bytes(text.encode("utf-8"))
    print(f"patched {HEALER}")

    body = TESTS.read_bytes().decode("utf-8")
    crlf_tests = CR + LF in body
    body = body.replace(CR + LF, LF)
    if "test_a_complete_measurement_is_not_replaced_by_a_summary" in body:
        raise SystemExit("the regression test is already present")
    marker = ("    def test_replacement_report_survives_a_store_without_"
              "the_candidate_scope(self):")
    if marker not in body:
        raise SystemExit("anchor not found: the candidate scope test")
    body = body.replace(marker, NEW_TEST.strip(LF) + LF + LF + marker, 1)
    if crlf_tests:
        body = body.replace(LF, CR + LF)
    TESTS.write_bytes(body.encode("utf-8"))
    print(f"patched {TESTS}")


if __name__ == "__main__":
    patch()
