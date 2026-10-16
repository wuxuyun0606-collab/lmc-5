"""Offline evidence visibility after neighbor merging, not relevance-ranked recall.

Rows contain an opaque source id and the exact text as stored by the service,
including its timestamp prefix. Partial or truncated neighbors are not full hits.
"""
import re


def visible_sources(content, rows):
    return sorted({row['id'] for row in rows
                   if re.search(r'(?:^|\n)' + re.escape(row['stored']) + r'(?=\n|$)', content)})
