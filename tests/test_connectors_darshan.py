#!/usr/bin/env python

import os
import tokio.connectors.darshan

SAMPLE_INPUT = os.path.join('inputs', 'sample.darshan')

def aux_darshan(darshan_data):
    assert darshan_data is not None
    # Make sure mount table parsing works
    assert 'mounts' in darshan_data 
    assert darshan_data['mounts']
    # Make sure header parsing works
    assert 'header' in darshan_data 
    assert darshan_data['header']
    # Ensure that counters were found
    assert 'counters' in darshan_data 
    assert darshan_data['counters']
    # Ensure the POSIX module and files were found (it should always be present)
    assert 'posix' in darshan_data['counters'] 
    assert darshan_data['counters']['posix']
 

def test_base():
    darshan = tokio.connectors.darshan.DARSHAN(SAMPLE_INPUT)
    darshan_data = darshan.darshan_parser_base()
    aux_darshan(darshan_data)
    # Examine the first POSIX file record contains counters
    posix_record = darshan_data['counters']['posix'].itervalues().next()
    assert posix_record
    # Ensure that it contains an OPENS counter
    assert 'OPENS' in posix_record.itervalues().next()
    # Ensure that multiple modules were found (STDIO should always exist too)
    assert 'stdio' in darshan_data['counters']

def test_total():
    
    darshan = tokio.connectors.darshan.DARSHAN(SAMPLE_INPUT)
    darshan_data = darshan.darshan_parser_total()
    aux_darshan(darshan_data)
    # Ensure that the total counters were extracted
    assert '_total' in darshan_data['counters']['posix']
    # Ensure that it contains an OPENS counter
    assert 'OPENS' in darshan_data['counters']['posix']['_total']
    # Ensure that multiple modules were found (STDIO should always exist too)
    assert 'stdio' in darshan_data['counters']
    # Ensure that it contains an OPENS counter
    assert 'OPENS' in darshan_data['counters']['stdio']['_total']


def test_perf():
    darshan = tokio.connectors.darshan.DARSHAN(SAMPLE_INPUT)
    darshan_data = darshan.darshan_parser_perf()
    aux_darshan(darshan_data)
    # Ensure that the perf counters were extracted
    assert '_perf' in darshan_data['counters']['posix']
    # Look for a few important counters
    assert 'total_bytes' in darshan_data['counters']['posix']['_perf']
    assert 'agg_perf_by_slowest' in darshan_data['counters']['posix']['_perf']
    # Make sure all counters appear in all modules
    for module in darshan_data['counters'].keys():
        for counter in darshan_data['counters']['posix']['_perf'].keys():
            assert counter in darshan_data['counters'][module]['_perf']
