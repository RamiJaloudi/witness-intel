"""Record interrupted jobs only after stop.ps1 verifies the server has exited."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def reconcile(db):
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    with Session(db.engine) as session:
        interrupted = 0
        for run in session.scalars(select(db.Run).where(db.Run.status == 'running')):
            if run.purpose == 'full_corpus':
                continue  # Persistent jobs resume through the recovery worker.
            run.status = 'interrupted'
            run.error = 'Interrupted by Stop-WitnessAI. Saved outputs are retained; retry explicitly.'
            db.audit(session, run.matter_id, 'launcher_shutdown_interrupted', {'run_id': run.id, 'purpose': run.purpose})
            interrupted += 1
        session.commit()
    return interrupted


if __name__ == '__main__':
    try:
        from scripts.launch import configuration
        configuration()
        from app import main
        print(f'Recorded {reconcile(main)} interrupted workflows; full-corpus checkpoints retained.')
    except Exception:
        print('Unable to record interrupted workflows. Inspect the database; credentials were not printed.')
        sys.exit(1)
