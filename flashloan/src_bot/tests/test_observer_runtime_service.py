from web.observer_runtime_service import ObserverRuntimeService


def test_runtime_service_tracks_start_progress_and_control_status():
    service = ObserverRuntimeService()

    service.set_observer_progress("initializing", "启动机会观察", 12)
    service.set_control_status("initializing", "启动机会观察", "准备中", 18, ttl_seconds=5)
    service.mark_supervisor_heartbeat(now=100.0)

    assert service.observer_start_progress["state"] == "initializing"
    assert service.observer_start_progress["percent"] == 12
    assert service.control_status["message"] == "准备中"
    assert service.observer_supervisor_payload()["heartbeat_age_seconds"] == 0.0


def test_runtime_service_can_reset_observer_state():
    service = ObserverRuntimeService()
    service.set_observer_progress("running", "机会观察运行中", 100)
    service.set_control_status("running", "机会观察运行中", "已运行", 100, ttl_seconds=10)
    service.mark_supervisor_heartbeat(now=200.0)

    service.reset_runtime_state()

    assert service.observer_start_progress["state"] == "stopped"
    assert service.observer_start_progress["percent"] == 0
    assert service.control_status["state"] == "stopped"
    assert service.supervisor_state["enabled"] is False
    assert service.supervisor_state["heartbeat_at"] == 0.0
