from tools import watch_target_liquidations as watcher


def test_target_liquidation_watcher_defaults_to_two_requested_accounts():
    args = watcher.parse_args([])

    assert args.accounts is None
    assert args.interval == 5.0
    assert args.hf_stop == 1.01
    assert args.realtime is False
    assert watcher.DEFAULT_ACCOUNTS == [
        "0x5D96768D0D551C1b2CE7CFC9a5293c24a6C8229E",
        "0x5831Fb2AFCD7a79831Eb5f49929dC95046e959e2",
    ]


def test_target_liquidation_watcher_accepts_explicit_account_allow_list():
    args = watcher.parse_args(["--account", "0x1", "--account", "0x2", "--max-rounds", "3"])

    assert args.accounts == ["0x1", "0x2"]
    assert args.max_rounds == 3


def test_target_liquidation_watcher_can_enable_realtime_chain_checks():
    args = watcher.parse_args(["--realtime"])

    assert args.realtime is True
