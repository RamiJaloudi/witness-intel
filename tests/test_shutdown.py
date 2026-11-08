from sqlalchemy import select
from sqlalchemy.orm import Session
from app import main
from scripts.reconcile_shutdown import reconcile
from scripts.console_signal import send_break
from tests.test_workflow import client, create


def test_shutdown_preserves_full_recovery_and_completed_runs(client):
    mid=create(client)
    with Session(main.engine) as s:
        runs=[main.Run(matter_id=mid,provider='ollama',purpose=purpose,status=status)
              for purpose,status in [('questions','running'),('full_corpus','running'),
                                     ('questions','completed'),('full_corpus','paused')]]
        s.add_all(runs);s.commit();ids=[r.id for r in runs]
    assert reconcile(main)==1
    with Session(main.engine) as s:
        assert [s.get(main.Run,rid).status for rid in ids]==['interrupted','running','completed','paused']
        events=list(s.scalars(select(main.Audit).where(main.Audit.action=='launcher_shutdown_interrupted')))
        assert len(events)==1 and events[0].details['run_id']==ids[0]
    assert reconcile(main)==0


def test_console_signal_rejects_invalid_pid():
    assert send_break(0) is False
    assert send_break(-1) is False
