"""Drop deprovision_policy values 'deallocate_instance' and 'delete_instance'.

Both were stubs in dynamic_runner: the DB/pool bookkeeping was real, but the
actual VM stop / delete was deferred to a hypothetical runbook that the UI
never asked the admin to configure. That left admins with a false sense of
action: selecting "Pause instance" freed the pool slot but the VM kept
running on the hypervisor.

Custom_runbook already covers the "do hypervisor work on deprovision" case
cleanly — authoring an XenServer/VMware stop or destroy runbook and wiring
it via the runbook_revoke_id slot. Remap any existing rows from the
deprecated values to 'custom_runbook' so they stop silently falling back
to access_only when the runtime branches are deleted.

The column is VARCHAR(30), not a PG enum, so no type-swap is required.

Revision ID: 0047
Revises: 0046
Create Date: 2026-04-24
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0047"
down_revision: Union[str, None] = "0046"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "UPDATE asset_types "
        "SET deprovision_policy = 'custom_runbook' "
        "WHERE deprovision_policy IN ('deallocate_instance', 'delete_instance')"
    )


def downgrade() -> None:
    # Can't reliably reconstruct which direction an asset type was remapped
    # from (deallocate vs delete), so leave custom_runbook in place.
    pass
