"""The whole workflow loop, run for a moment with the game stubbed out."""
import os
import threading
import time



def test_the_loop_respawns_when_idle_logs_resources_stops_and_writes_the_report(macro, tmp_path, monkeypatch):
    cfg = macro.cfg
    monkeypatch.setattr(cfg, "RUN_COUNTER_PATH", str(tmp_path / "run_counter.txt"))
    monkeypatch.setattr(cfg, "GAME_CONFIG_PATH", str(tmp_path / ".config"))
    monkeypatch.setattr(cfg, "NEED_CHECK_RETRY_DELAY", 0.05)
    monkeypatch.setattr(cfg, "LOOP_DELAY", 0)
    monkeypatch.setattr(cfg, "NO_DETECTION_RESPAWN_INTERVAL", 0.4)
    monkeypatch.setattr(cfg, "RESOURCE_LOG_INTERVAL", 0.2)
    monkeypatch.setattr(cfg, "QUIET_LOG_INTERVAL", 0.5)
    respawns = []
    monkeypatch.setattr(macro, "respawn_character", lambda: respawns.append(time.time()))
    monkeypatch.setattr(macro, "check_focus", lambda: None)
    monkeypatch.setattr(macro, "unscrew", lambda *a, **k: None)
    monkeypatch.setattr(macro, "side_quest", lambda: None)
    monkeypatch.setattr(macro, "process_needs", lambda: False)        # nothing on screen, ever

    outcome = []

    def run():
        try:
            macro.run_workflow_loop()
        except BaseException as error:                               # noqa: BLE001 - what the GUI worker would see
            outcome.append(error)

    worker = threading.Thread(target=run)
    worker.start()
    # Run until it has done what is checked below (not for a fixed time: a slow machine takes longer, that's all).
    deadline = time.time() + 60
    while time.time() < deadline:
        text = open(cfg.RUN_LOG_PATH).read() if os.path.exists(cfg.RUN_LOG_PATH) else ""
        if text.count("no need detected for") >= 2 and "resources: cpu" in text and macro.run_stats.cycles > 5:
            break
        time.sleep(0.05)
    macro.STOP_EVENT.set()
    worker.join(30)
    assert not worker.is_alive()
    assert len(outcome) == 1 and isinstance(outcome[0], macro.StopRequested)

    log = open(cfg.RUN_LOG_PATH).read()
    assert "LOOP started" in log and "LOOP stopped" in log
    assert log.count("no need detected for") >= 2                    # idle respawns, one per interval
    assert len(respawns) >= 3                                        # the one at the start, and those
    assert "resources: cpu" in log                                   # the monitor ran, and was stopped with the loop
    assert macro.run_stats.cycles > 5
    assert macro.state.last_progress is not None
    assert time.time() - macro.state.last_progress > cfg.NO_DETECTION_RESPAWN_INTERVAL   # nothing resolved: the respawns left the progress clock alone
    assert os.path.exists(os.path.join(cfg.REPORTS_DIR, f"run{macro.state.run_number}.png"))
    time.sleep(0.5)
    lines_after = open(cfg.RUN_LOG_PATH).read().count("resources: cpu")
    time.sleep(0.5)
    assert open(cfg.RUN_LOG_PATH).read().count("resources: cpu") == lines_after   # the monitor thread has stopped
