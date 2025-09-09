from typing import Any

from reemote.construct_host_ops import construct_host_ops
from reemote.get_printable_aggrid import get_printable_aggrid
from reemote.summarize_data_for_aggrid import summarize_data_for_aggrid


def grid(operations: list[Any], responses: list[Any]):
    host_ops = construct_host_ops(operations, responses)
    dgrid = summarize_data_for_aggrid(host_ops)
    return dgrid.get("columnDefs"),dgrid.get("rowData")

