"""Each session sees only the current company's rows (src/tenancy.py).

Every ORM query (select, and update / delete by criteria) on a `TenantScoped` table gets `tenant_id = <current>`
added, also in joins and relationship loads, and new rows take the current company as their `tenant_id` (the column
default). So the code of the screens keeps querying as if there were one company; a row of another company is
simply not found (404). `execution_options(all_tenants=True)` lifts the filter (not used by the screens).
"""
from __future__ import annotations

from sqlalchemy import event
from sqlalchemy.orm import ORMExecuteState, Session, with_loader_criteria

from src.db.models import TenantScoped
from src.tenancy import current_tenant


@event.listens_for(Session, "do_orm_execute")
def _only_the_current_company(state: ORMExecuteState) -> None:
    if not (state.is_select or state.is_update or state.is_delete) or state.execution_options.get("all_tenants"):
        return
    tenant = current_tenant()
    state.statement = state.statement.options(
        with_loader_criteria(TenantScoped, lambda cls: cls.tenant_id == tenant, include_aliases=True))


def key(model, value) -> dict:
    """The primary key of a keyed table (masters, counters, ...) for `Session.get`: the company and the code."""
    column = next(c for c in model.__table__.primary_key.columns if c.name != "tenant_id")
    return {"tenant_id": current_tenant(), model.__mapper__.get_property_by_column(column).key: value}
