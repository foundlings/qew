"""`qew restore`: recreate a forge project from the ledger alone."""


from qew.forge import ForgeError, Unsupported
from qew.ledger_config import Config
from qew.ledger_store import Ledger
from qew.sync import Target, TargetReport, sync_target


def restore_target(ledger: Ledger, target: Target, config: Config, deadline: float | None = None) -> TargetReport:
    """Forget mirror files whose remote issue is gone, then run the ordinary sync (create, adopt, link, board)."""
    try:
        remote = {issue.number for issue in target.forge.list_issues()}
    except Unsupported:
        remote = None  # cannot verify: trust the mirror files and let sync decide
    except ForgeError as error:
        report = TargetReport(target.slug)
        report.failures.append(error.detail)
        return report
    if remote is not None:
        for item_id in ledger.item_ids():
            for record in ledger.load_mirrors(item_id):
                if record["_target"] == target.slug and record.get("kind") == "issue" and record["number"] not in remote:
                    ledger.remove_mirror(item_id, target.slug)
    return sync_target(ledger, target, config, deadline=deadline)
