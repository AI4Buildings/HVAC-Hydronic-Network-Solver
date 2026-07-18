#!/usr/bin/env python3
"""MATLAB find() / min() index helpers, ported to NumPy 0-based indexing.

The Energy control branch of heat_rec_wheel_calc_V5.m relies heavily on MATLAB's
find() with 'first'/'last' semantics and on min(array) over index sets. To keep
the port bit-faithful these helpers reproduce MATLAB's exact behaviour while
returning 0-based indices (or None when MATLAB would return []).

MATLAB recap:
    find(mask)                 -> all 1-based indices where mask is true
    find(mask, 1)              -> first such index (1-based)
    find(mask, 1, 'last')      -> last such index
    min(find(mask))            -> first such index
    [] when no match           -> here represented as None
"""

from __future__ import annotations
import numpy as np


def find_first(mask):
    """MATLAB find(mask, 1) -> 0-based first True index, or None."""
    idx = np.flatnonzero(np.asarray(mask, dtype=bool))
    return int(idx[0]) if idx.size else None


def find_last(mask):
    """MATLAB find(mask, 1, 'last') -> 0-based last True index, or None."""
    idx = np.flatnonzero(np.asarray(mask, dtype=bool))
    return int(idx[-1]) if idx.size else None


def find_all(mask):
    """MATLAB find(mask) -> array of 0-based indices (possibly empty)."""
    return np.flatnonzero(np.asarray(mask, dtype=bool))


def argmax_last(arr):
    """MATLAB find(a==max(a), 1, 'last') -> 0-based last index of the max."""
    a = np.asarray(arr, dtype=float)
    m = np.max(a)
    return find_last(a == m)


def argmax_first(arr):
    """MATLAB find(a==max(a), 1) -> 0-based first index of the max."""
    a = np.asarray(arr, dtype=float)
    return find_first(a == np.max(a))


def argmin_last(arr):
    """MATLAB find(a==min(a), 1, 'last') -> 0-based last index of the min."""
    a = np.asarray(arr, dtype=float)
    return find_last(a == np.min(a))


def argmin_first(arr):
    """MATLAB find(a==min(a), 1) -> 0-based first index of the min."""
    a = np.asarray(arr, dtype=float)
    return find_first(a == np.min(a))
