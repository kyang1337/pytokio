#!/usr/bin/env python
"""
Test the bin/summarize_h5lmt.py tool
"""

import os
import json
import datetime
import tokio
import tokiotest
import tokiobin.summarize_h5lmt

### For tokio.tools.hdf5, which is used by summarize_job.py
tokio.config.H5LMT_BASE_DIR = os.path.join(tokiotest.INPUT_DIR, "%Y-%m-%d")
START_TIME = datetime.datetime.strptime(tokiotest.SAMPLE_H5LMT_DATES[0],
                                        tokiobin.summarize_h5lmt.DATE_FMT)
END_TIME = START_TIME + datetime.timedelta(days=1)

INPUT_PARAMS = {
    'base, h5lmt': [tokiotest.SAMPLE_H5LMT_FILE],
    '--summary h5lmt': ['--summary', tokiotest.SAMPLE_H5LMT_FILE],
    '--json h5lmt': ['--json', tokiotest.SAMPLE_H5LMT_FILE],
    '--json --summary h5lmt': ['--json', '--summary', tokiotest.SAMPLE_H5LMT_FILE],
    'base, tts': [tokiotest.SAMPLE_LMTDB_TTS_HDF5],
    '--summary tts': ['--summary', tokiotest.SAMPLE_LMTDB_TTS_HDF5],
    '--json tts': ['--json', tokiotest.SAMPLE_LMTDB_TTS_HDF5],
    '--json --summary tts': ['--json', '--summary', tokiotest.SAMPLE_LMTDB_TTS_HDF5],
    'date range': [
        '--json',
        '--start', START_TIME.strftime(tokiobin.summarize_h5lmt.DATE_FMT),
        '--end', END_TIME.strftime(tokiobin.summarize_h5lmt.DATE_FMT),
        os.path.basename(tokiotest.SAMPLE_H5LMT_FILE)
    ],
}

def verify_json(output, expected_keys):
    """
    Verify the contents of summarize_h5lmt.py when encoded as json
    """
    deser = json.loads(output)
    assert deser
    for expected_key in expected_keys:
        assert expected_key in deser

def run_summarize_h5lmt(args):
    """
    Run an instance of summarize_h5lmt.py
    """
    print "Running %s %s" % ('bin/summarize_h5lmt.py', ' '.join(args))
    output_str = tokiotest.run_bin(tokiobin.summarize_h5lmt, args)
    assert output_str > 0

    if '--json' in args:
        if '--summary' in args:
            verify_json(output_str, ['bins', 'summary'])
        else:
            verify_json(output_str, ['bins'])

def test_all():
    """
    Run through the different test conditions
    """
    for descr, args in INPUT_PARAMS.iteritems():
        func = run_summarize_h5lmt
        func.description = 'bin/summarize_h5lmt.py ' + descr
        yield func, args
