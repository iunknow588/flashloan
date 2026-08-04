import threading

from execution.nonce_manager import NonceManager


class FakeEth:
    def __init__(self, nonce):
        self.nonce = nonce
        self.calls = []

    def get_transaction_count(self, sender, block_identifier):
        self.calls.append((sender, block_identifier))
        return self.nonce


class FakeWeb3:
    def __init__(self, nonce=7):
        self.eth = FakeEth(nonce)


def test_nonce_manager_acquires_100_unique_sequential_nonces():
    sender = "0x0000000000000000000000000000000000000001"
    manager = NonceManager(FakeWeb3(nonce=7), sender)

    assert manager.initialize() == 7
    nonces = [manager.acquire() for _ in range(100)]

    assert nonces == list(range(7, 107))
    assert len(set(nonces)) == 100
    assert manager.current() == 107


def test_nonce_manager_reuses_released_nonce_before_new_nonce():
    sender = "0x0000000000000000000000000000000000000001"
    manager = NonceManager(FakeWeb3(nonce=10), sender)
    manager.initialize()

    first = manager.acquire()
    second = manager.acquire()
    manager.release(second)
    manager.release(first)

    assert manager.acquire() == first
    assert manager.acquire() == second
    assert manager.acquire() == 12


def test_nonce_manager_threaded_acquire_has_no_conflicts():
    sender = "0x0000000000000000000000000000000000000001"
    manager = NonceManager(FakeWeb3(nonce=20), sender)
    manager.initialize()
    acquired = []
    lock = threading.Lock()

    def worker():
        local = [manager.acquire() for _ in range(10)]
        with lock:
            acquired.extend(local)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(acquired) == 100
    assert sorted(acquired) == list(range(20, 120))
