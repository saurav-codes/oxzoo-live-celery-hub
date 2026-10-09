class LedgerRouter:
    """The ledger app lives in the private ledger database; everything else
    in the shared default one."""

    def db_for_read(self, model, **hints):
        return "ledger" if model._meta.app_label == "ledger" else "default"

    db_for_write = db_for_read

    def allow_relation(self, a, b, **hints):
        return (a._meta.app_label == "ledger") == (b._meta.app_label == "ledger")

    def allow_migrate(self, db, app_label, **hints):
        return (db == "ledger") == (app_label == "ledger")
