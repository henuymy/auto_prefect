"""Placeholder flow for manually rerunning a notification instance."""

from prefect import flow


@flow(name="manual-rerun-flow")
def manual_rerun_flow():
    raise NotImplementedError("manual_rerun_flow will be implemented after service migration.")

