import sys

import pytest

from devradar.ingestion import worker
from devradar.ingestion.worker import RunSummary


@pytest.mark.parametrize(
    ("status", "argv", "exit_code", "trigger"),
    [
        ("partial", [], None, "manual"),
        ("succeeded", ["--scheduled"], None, "schedule"),
        ("failed", ["--scheduled"], 1, "schedule"),
    ],
)
def test_cli_records_trigger_and_fails_when_the_run_fails(
    monkeypatch, status, argv, exit_code, trigger
):
    seen = []

    async def fake_main(limit, full, trigger):
        seen.append(trigger)
        return RunSummary(1, status, [], 0.0)

    monkeypatch.setattr(worker, "_main", fake_main)
    monkeypatch.setattr(sys, "argv", ["devradar-ingest", *argv])
    if exit_code is None:
        worker.main()
    else:
        with pytest.raises(SystemExit) as error:
            worker.main()
        assert error.value.code == exit_code
    assert seen == [trigger]
