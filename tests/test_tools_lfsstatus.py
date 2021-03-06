#!/usr/bin/env python
"""
Test the lfsstatus tool API
"""

import os
import json
import datetime
import tokiotest
from tokiotest import SAMPLE_OSTMAP_FILE, SAMPLE_OSTFULLNESS_FILE, SAMPLE_DARSHAN_SONEXION_ID
import tokio
import tokio.tools.lfsstatus as lfsstatus

# Point this script at our inputs directory instead of the site-specific default
tokio.config.LFSSTATUS_BASE_DIR = os.path.join(tokiotest.INPUT_DIR, "%Y-%m-%d")

# These should correspond to the first and last BEGIN in the sample ost-map.txt
# and ost-fullness.txt files.  If you change the contents of those files, you
# MUST update these as well.
SAMPLE_OSTFULLNESS_START = datetime.datetime.fromtimestamp(tokiotest.SAMPLE_OSTFULLNESS_START)
SAMPLE_OSTFULLNESS_END = datetime.datetime.fromtimestamp(tokiotest.SAMPLE_OSTFULLNESS_END)
SAMPLE_OSTFULLNESS_DELTA = (SAMPLE_OSTFULLNESS_END - SAMPLE_OSTFULLNESS_START).total_seconds() / 2.0
SAMPLE_OSTFULLNESS_DELTA = datetime.timedelta(seconds=SAMPLE_OSTFULLNESS_DELTA)
SAMPLE_OSTFULLNESS_HALFWAY = SAMPLE_OSTFULLNESS_START + SAMPLE_OSTFULLNESS_DELTA
SAMPLE_OSTFULLNESS_BEFORE = SAMPLE_OSTFULLNESS_START - datetime.timedelta(seconds=1)
SAMPLE_OSTFULLNESS_AFTER = SAMPLE_OSTFULLNESS_END + datetime.timedelta(seconds=1)

SAMPLE_OSTMAP_START = datetime.datetime.fromtimestamp(tokiotest.SAMPLE_OSTMAP_START)
SAMPLE_OSTMAP_END = datetime.datetime.fromtimestamp(tokiotest.SAMPLE_OSTMAP_END)
SAMPLE_OSTMAP_DELTA = (SAMPLE_OSTMAP_END - SAMPLE_OSTMAP_START).total_seconds() / 2.0
SAMPLE_OSTMAP_DELTA = datetime.timedelta(seconds=SAMPLE_OSTMAP_DELTA)
SAMPLE_OSTMAP_HALFWAY = SAMPLE_OSTMAP_START + SAMPLE_OSTMAP_DELTA
SAMPLE_OSTMAP_BEFORE = SAMPLE_OSTMAP_START - datetime.timedelta(seconds=1)
SAMPLE_OSTMAP_AFTER = SAMPLE_OSTMAP_END + datetime.timedelta(seconds=1)

def wrap_get_fullness_at_datetime(datetime_target, cache_file):
    """
    Encapsulate test and validation of lfsstatus.get_fullness_at_datetime into a
    single function
    """
    result = lfsstatus.get_fullness_at_datetime(
        SAMPLE_DARSHAN_SONEXION_ID,
        datetime_target,
        cache_file)
    verify_fullness(result)

def wrap_get_failures_at_datetime(datetime_target, cache_file):
    """
    Encapsulate test and validation of lfsstatus.get_failures_at_datetime into a
    single function
    """
    result = lfsstatus.get_failures_at_datetime(
        SAMPLE_DARSHAN_SONEXION_ID,
        datetime_target,
        cache_file)
    verify_failures(result)

CACHE_FILES = {
    wrap_get_fullness_at_datetime: SAMPLE_OSTFULLNESS_FILE,
    wrap_get_failures_at_datetime: SAMPLE_OSTMAP_FILE,
}

TEST_CONDITIONS = {
    wrap_get_fullness_at_datetime: [
        {
            'description': "lfsstatus.get_fullness_at_datetime() baseline functionality",
            'datetime_target': SAMPLE_OSTFULLNESS_HALFWAY,
        },
        {
            'description': "lfsstatus.get_fullness_at_datetime() first timestamp",
            'datetime_target': SAMPLE_OSTFULLNESS_START,
        },
        {
            'description': "lfsstatus.get_fullness_at_datetime() last timestamp",
            'datetime_target': SAMPLE_OSTFULLNESS_END,
        },
        {
            'description': "lfsstatus.get_fullness_at_datetime() before first timestamp",
            'datetime_target': SAMPLE_OSTFULLNESS_BEFORE,
        },
        {
            'description': "lfsstatus.get_fullness_at_datetime() after file",
            'datetime_target': SAMPLE_OSTFULLNESS_AFTER,
        },
    ],
    wrap_get_failures_at_datetime: [
        {
            'description': "lfsstatus.get_failures_at_datetime() baseline functionality",
            'datetime_target': SAMPLE_OSTMAP_HALFWAY,
        },
        {
            'description': "lfsstatus.get_failures_at_datetime() first timestamp",
            'datetime_target': SAMPLE_OSTMAP_START,
        },
        {
            'description': "lfsstatus.get_failures_at_datetime() last timestamp",
            'datetime_target': SAMPLE_OSTMAP_END,
        },
        {
            'description': "lfsstatus.get_failures_at_datetime() before file",
            'datetime_target': SAMPLE_OSTMAP_BEFORE,
        },
        {
            'description': "lfsstatus.get_failures_at_datetime() after file",
            'datetime_target': SAMPLE_OSTMAP_AFTER,
        },
    ],
}

def verify_fullness(result):
    """
    Verify correctness of get_fullness_at_datetime()
    """
    print json.dumps(result, indent=4, sort_keys=True)
    assert result['ost_avg_full_kib'] > 0
    assert 0.0 < result['ost_avg_full_pct'] < 100.0
    assert result['ost_count'] > 1
    assert result['ost_least_full_id'] != result['ost_most_full_id']
    assert result['ost_next_timestamp'] > result['ost_actual_timestamp']
    assert 'ost_requested_timestamp' in result

def verify_failures(result):
    """
    Verify correctness of get_failures_at_datetime()
    """
    print json.dumps(result, indent=4, sort_keys=True)
    assert result['ost_next_timestamp'] > result['ost_actual_timestamp']
    assert result['ost_overloaded_oss_count'] == tokiotest.SAMPLE_OSTMAP_OVERLOAD_OSS
    ### ensure that ost_avg_overloaded_ost_per_oss is calculated correctly
    assert int(result['ost_avg_overloaded_ost_per_oss']) == \
        int(float(result['ost_overloaded_ost_count']) / result['ost_overloaded_oss_count'])
    assert 'ost_requested_timestamp' in result

def test_get_functions():
    """
    Iterate over all test cases
    """
    for func, tests in TEST_CONDITIONS.iteritems():
        for config in tests:
            test_func = func
            test_func.description = config['description'] + ", no cache"
            yield test_func, config['datetime_target'], None

            test_func = func
            test_func.description = config['description'] + ", cache"
            yield test_func, config['datetime_target'], CACHE_FILES[func]
