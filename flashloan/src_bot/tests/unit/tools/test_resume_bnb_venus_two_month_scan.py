from tools import resume_bnb_venus_two_month_scan as resume
from tools.resume_bnb_venus_two_month_scan import _batch_markets


def test_resume_bnb_venus_two_month_scan_batches_market_emitters():
    batches = _batch_markets(["a", "b", "c", "d", "e"], 2)

    assert batches == [["a", "b"], ["c", "d"], ["e"]]


def test_resume_bnb_venus_two_month_scan_passes_each_batch_to_rebuild(monkeypatch, tmp_path):
    calls = []

    class FakeEth:
        block_number = 200

    class FakeW3:
        eth = FakeEth()

    monkeypatch.setattr(resume, "_load_markets", lambda _path: ["0x01", "0x02", "0x03"])
    monkeypatch.setattr(resume, "_connect_rpc_pool", lambda _urls: (FakeW3(), [FakeW3()], ["https://rpc.example"]))
    monkeypatch.setattr(resume, "_filter_log_capable_rpc_pool", lambda clients, urls, probe_vtoken: (clients, urls, []))
    monkeypatch.setattr(resume, "rebuild_borrowers_main", lambda child_args: calls.append(child_args) or 0)

    result = resume.main(
        [
            "--output-dir",
            str(tmp_path),
            "--progress-file",
            str(tmp_path / "progress.json"),
            "--events-jsonl",
            str(tmp_path / "events.jsonl"),
            "--from-block",
            "100",
            "--to-block",
            "200",
            "--max-markets",
            "3",
            "--address-batch-size",
            "2",
        ]
    )

    assert result == 0
    assert [args[args.index("--vtoken") + 1] for args in calls] == ["0x01", "0x03"]
    assert calls[0].count("--vtoken") == 2
    assert calls[1].count("--vtoken") == 1
