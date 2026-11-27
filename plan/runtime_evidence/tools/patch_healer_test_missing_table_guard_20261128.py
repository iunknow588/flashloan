"""Add the regression test for the candidate-scope guard of the healer.

The report that looks for replacement pools read ``dex_candidate_pools``
without a guard.  While the pool scope was rebuilt the table was absent for
a moment and the whole healing cycle ended with an OperationalError.
"""

from pathlib import Path

TESTS = Path("scripts/tests/test_mainnet_arbitrage_healer.py")

NEW_TEST = '''
    def test_replacement_report_survives_a_store_without_the_candidate_scope(self):
        """The candidate scope is rebuilt while the healer runs.

        The replacement report read ``dex_candidate_pools`` without a guard,
        so the table's absence for a moment ended the whole healing cycle.
        A store that carries no candidate pools is a gap of the store: the
        report says so, and the rest of the cycle still runs.
        """
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "pools.sqlite3"
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    "CREATE TABLE v4_exchange_pools (network TEXT, chain_id INTEGER, "
                    "protocol TEXT, pool_id TEXT, token0 TEXT, token1 TEXT, fee INTEGER, "
                    "dynamic_fee INTEGER, special_fee INTEGER, hooks TEXT)"
                )
                connection.execute(
                    "CREATE TABLE v4_exchange_pool_liquidity_snapshots (network TEXT, "
                    "chain_id INTEGER, protocol TEXT, pool_id TEXT, has_liquidity INTEGER, "
                    "liquidity_index INTEGER, depth_status TEXT, estimated_tvl_usd REAL, "
                    "capacity_binding_raw TEXT)"
                )
                connection.commit()
            finally:
                connection.close()

            report = HEALER.pool_depth_replacement_report(database)
            self.assertEqual(report["status"], "candidate_pool_table_missing", report)
            self.assertEqual(report["code"], "candidate_pool_table_missing", report)
            self.assertEqual(report["repairPath"], "materialize_candidate_pools", report)
            self.assertEqual(report["replacementKeyCount"], 0, report)
            self.assertEqual(report["keys"], [], report)
'''


def patch() -> None:
    raw = TESTS.read_bytes()
    text = raw.decode("utf-8")
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(newline)
    if any("test_replacement_report_survives_a_store_without_the_candidate_scope" in line
           for line in lines):
        raise SystemExit("the regression test is already present")
    anchor = next((i for i, line in enumerate(lines)
                   if line.strip() == "def test_replacement_report_states_no_depth_qualified_pool_exists(self):"),
                  None)
    if anchor is None:
        raise SystemExit("anchor not found: the replacement report test")
    end = next((i for i in range(anchor, len(lines))
                if lines[i].strip() == 'self.assertEqual(report["code"], "pool_depth_insufficient")'),
               None)
    if end is None:
        raise SystemExit("anchor not found: the replacement report test body")
    lines[end + 1:end + 1] = NEW_TEST.rstrip("\n").split("\n")
    TESTS.write_bytes(newline.join(lines).encode("utf-8"))
    print(f"patched {TESTS}")


if __name__ == "__main__":
    patch()
