#!/usr/bin/env python
"""
Take a darshan log, extract the performance data from it, then use the
start/stop time from Darshan to extract the LMT data.  Relies on the
tokio.tool package, which uses H5LMT_HOME to determine where the LMT
HDF5 files are stored on the local system.
"""

import re
import os
import json
import time
import datetime
import argparse
import warnings
import pandas
import tokio
import tokio.tools
import tokio.connectors.darshan
import tokio.connectors.nersc_jobsdb

def _identify_fs_from_path(path, mounts):
    """
    Scan a list of mount points and try to identify the one that matches the
    given path

    """
    max_match = 0
    matching_mount = None
    for mount in mounts:
        if path.startswith(mount) and len(mount) > max_match:
            max_match = len(mount)
            matching_mount = mount
    return matching_mount

def summarize_darshan_posix(darshan_data):
    """
    Extract key metrics from the POSIX module in a Darshan log
    """
    # Extract POSIX performance counters if present
    results = {}
    if 'counters' in darshan_data \
    and 'posix' in darshan_data['counters'] \
    and '_perf' in darshan_data['counters']['posix']:
        d_perf = darshan_data['counters']['posix']['_perf']
        results['total_gibs_posix'] = d_perf.get('total_bytes')
        if results['total_gibs_posix']:
            results['total_gibs_posix'] /= 2.0**30
            results['agg_perf_by_slowest_posix'] = d_perf.get('agg_perf_by_slowest')
            results['io_time'] = d_perf.get('slowest_rank_io_time_unique_files')
            if results['io_time']:
                results['io_time'] += d_perf.get('time_by_slowest_shared_files')
    return results

def get_biggest_api(darshan_data):
    """
    Determine the most-used API and file system based on the Darshan log
    """
    if 'counters' not in darshan_data:
        return {}

    biggest_api = {}
    for api_name in darshan_data['counters'].keys():
        biggest_api[api_name] = {
            'write': 0,
            'read': 0,
        }
        for file_path in darshan_data['counters'][api_name]:
            if file_path in ('_perf', '_total'): # only consider file records
                continue
            for record in darshan_data['counters'][api_name][file_path].itervalues():
                bytes_read = record.get('BYTES_READ')
                if bytes_read is not None:
                    biggest_api[api_name]['read'] += bytes_read
                bytes_written = record.get('BYTES_WRITTEN')
                if bytes_written is not None:
                    biggest_api[api_name]['write'] += bytes_written

    results = {}
    for readwrite in 'read', 'write':
        key = 'biggest_%s_api' % readwrite
        results[key] = max(biggest_api, key=lambda k, rw=readwrite: biggest_api[k][rw])
        results['%s_bytes' % key] = biggest_api[results[key]][readwrite]

    return results

def get_biggest_fs(darshan_data):
    """
    Determine the most-used file system based on the Darshan log
    """
    if 'counters' not in darshan_data:
        return {}

    if 'biggest_read_api' not in darshan_data or 'biggest_write_api' not in darshan_data:
        biggest_api = get_biggest_api(darshan_data)
        biggest_read_api = biggest_api['biggest_read_api']
        biggest_write_api = biggest_api['biggest_write_api']
    else:
        biggest_read_api = darshan_data['biggest_read_api']
        biggest_write_api = darshan_data['biggest_write_api']

    biggest_fs = {}
    mounts = darshan_data['mounts'].keys()
    for api_name in biggest_read_api, biggest_write_api:
        for file_path in darshan_data['counters'][api_name]:
            if file_path in ('_perf', '_total'): # only consider file records
                continue
            for record in darshan_data['counters'][api_name][file_path].itervalues():
                key = _identify_fs_from_path(file_path, mounts)
                if key is None:
                    key = '_unknown' ### for stuff like STDIO
                if key not in biggest_fs:
                    biggest_fs[key] = {'write': 0, 'read': 0}
                bytes_read = record.get('BYTES_READ')
                if bytes_read is not None:
                    biggest_fs[key]['read'] += bytes_read
                bytes_written = record.get('BYTES_WRITTEN')
                if bytes_written is not None:
                    biggest_fs[key]['write'] += bytes_written

    results = {}
    for readwrite in 'read', 'write':
        key = 'biggest_%s_fs' % readwrite
        results[key] = max(biggest_fs, key=lambda k, rw=readwrite: biggest_fs[k][rw])
        results['%s_bytes' % key] = biggest_fs[results[key]][readwrite]

    return results

def summarize_darshan(darshan_data):
    """
    Synthesize new Darshan summary metrics based on the contents of a
    connectors.darshan.Darshan object that is partially or fully populated

    """

    results = {}

    if 'header' in darshan_data:
        d_header = darshan_data['header']
        for key in 'walltime', 'end_time', 'start_time', 'jobid':
            results[key] = d_header.get(key)
        if 'exe' in d_header:
            results['app'] = d_header['exe'][0]
        else:
            results['app'] = None

    results.update(summarize_darshan_posix(darshan_data))
    results.update(get_biggest_api(darshan_data))
    results.update(get_biggest_fs(darshan_data))

    return results

def summarize_byterate_df(dataframe, readwrite, timestep=None):
    """
    Calculate some interesting statistics from a dataframe containing byte rate
    data.

    """
    assert readwrite in ['read', 'written']
    if timestep is None:
        if dataframe.shape[0] < 2:
            raise Exception("must specify timestep for single-row dataframe")
        timestep = (dataframe.index[1].to_pydatetime() \
                    - dataframe.index[0].to_pydatetime()).total_seconds()
    results = {}
    results['tot_bytes_%s' % readwrite] = dataframe.sum().sum() * timestep
    results['tot_gibs_%s' % readwrite] = results['tot_bytes_%s' % readwrite] / 2.0**30
    results['ave_bytes_%s_per_timestep' % readwrite] = \
        (dataframe.sum(axis=1) / dataframe.columns.shape[0]).mean() * timestep
    results['ave_gibs_%s_per_timestep' % readwrite] = \
        results['ave_bytes_%s_per_timestep' % readwrite] / 2.0**30
    results['frac_zero_%s' % readwrite] = \
        float((dataframe == 0.0).sum().sum()) / float((dataframe.shape[0]*dataframe.shape[1]))
    return results

def summarize_cpu_df(dataframe, servertype):
    """
    Calculate some interesting statistics from a dataframe containing CPU load
    data.

    """
    assert servertype in ['oss', 'mds']
    results = {}
    # df content depends on the servertype
    results['ave_%s_cpu' % servertype] = dataframe.mean().mean()
    results['max_%s_cpu' % servertype] = dataframe.max().max()
    return results

def summarize_missing_df(dataframe):
    """
    Populate the fraction missing counter from a given DataFrame

    """
    results = {
        'frac_missing': float((dataframe != 0.0).sum().sum()) \
                        / float((dataframe.shape[0]*dataframe.shape[1]))
    }
    return results

def merge_dicts(dict1, dict2, assertion=True, prefix=None):
    """
    Take two dictionaries and merge their keys.  Optionally raise an exception
    if a duplicate key is found, and optionally merge the new dict into the old
    after adding a prefix to every key.

    """
    for key, value in dict2.iteritems():
        if prefix is not None:
            new_key = prefix + key
        else:
            new_key = key
        if assertion:
            if new_key in dict1:
                raise Exception("duplicate key %s found" % new_key)
        dict1[new_key] = value
    # wprefix = ''
    # if prefix is None:
    #     wprefix = prefix
    # for key, value in dict2.iteritems():
    #     new_key = wprefix + key
    #     if assertion and (new_key in dict1):
    #         raise Exception("duplicate key %s found" % new_key)
    #     dict1[new_key] = value

def serialize_datetime(obj):
    """
    Special serializer function that converts datetime into something that can
    be encoded in json

    """
    if isinstance(obj, (datetime.datetime, datetime.date)):
        return (obj - datetime.datetime.utcfromtimestamp(0)).total_seconds()
    raise TypeError("Type %s not serializable" % type(obj))

def retrieve_darshan_data(results, darshan_log_file):
    """
    Extract the performance data from the Darshan log
    """
    darshan_data = tokio.connectors.darshan.Darshan(darshan_log_file)
    darshan_data.darshan_parser_perf()
    darshan_data.darshan_parser_base()

    if 'header' not in darshan_data:
        warnings.warn("%s is not a valid darshan log" % darshan_log_file)
        return results

    # Define start/end time from darshan log.  Add an extra LMT_TIMESTEP on
    # based on empirical observation that LMT is still flushing data for this
    # long after the job concludes.
    results['_datetime_start'] = datetime.datetime.fromtimestamp(
        int(darshan_data['header']['start_time']) + tokio.config.LMT_TIMESTEP)
    results['_datetime_end'] = datetime.datetime.fromtimestamp(
        int(darshan_data['header']['end_time']) + tokio.config.LMT_TIMESTEP)

    if '_jobid' not in results:
        results['_jobid'] = darshan_data['header']['jobid']

    # Get the summary of the Darshan log
    module_results = summarize_darshan(darshan_data)
    merge_dicts(results, module_results, prefix='darshan_')
    return results

def retrieve_lmt_data(results, file_system):
    """
    Figure out the H5LMT file corresponding to this run
    """
    if file_system is None:
        if 'darshan_biggest_write_fs_bytes' not in results.keys() \
        or 'darshan_biggest_read_fs_bytes' not in results.keys():
            return results

        # Attempt to divine file system from Darshan log
        results['_file_system'] = None
        if results['darshan_biggest_write_fs_bytes'] > results['darshan_biggest_read_fs_bytes']:
            fs_key = 'darshan_biggest_write_fs'
        else:
            fs_key = 'darshan_biggest_read_fs'
        for fs_path, fs_name in tokio.config.MOUNT_TO_FSNAME.iteritems():
            if re.search(fs_path, results[fs_key]) is not None:
                results['_file_system'] = fs_name
                break
    else:
        results['_file_system'] = file_system
    h5lmt_file = tokio.config.FSNAME_TO_H5LMT_FILE.get(results['_file_system'])
    if h5lmt_file is None:
        return results

    module_results = {}
    # Read rates
    module_results.update(summarize_byterate_df(
        tokio.tools.hdf5.get_dataframe_from_time_range(
            h5lmt_file,
            '/OSTReadGroup/OSTBulkReadDataSet',
            results['_datetime_start'],
            results['_datetime_end']),
        'read'
    ))

    # Write rates
    module_results.update(summarize_byterate_df(
        tokio.tools.hdf5.get_dataframe_from_time_range(
            h5lmt_file, '/OSTWriteGroup/OSTBulkWriteDataSet',
            results['_datetime_start'],
            results['_datetime_end']),
        'written'
    ))
    # Oss cpu loads
    module_results.update(summarize_cpu_df(
        tokio.tools.hdf5.get_dataframe_from_time_range(
            h5lmt_file,
            '/OSSCPUGroup/OSSCPUDataSet',
            results['_datetime_start'],
            results['_datetime_end']),
        'oss'
    ))
    # Mds cpu loads
    module_results.update(summarize_cpu_df(
        tokio.tools.hdf5.get_dataframe_from_time_range(
            h5lmt_file,
            '/MDSCPUGroup/MDSCPUDataSet',
            results['_datetime_start'],
            results['_datetime_end']),
        'mds'
    ))
    # Missing data
    module_results.update(summarize_missing_df(
        tokio.tools.hdf5.get_dataframe_from_time_range(
            h5lmt_file,
            '/FSMissingGroup/FSMissingDataSet',
            results['_datetime_start'],
            results['_datetime_end'])))
    merge_dicts(results, module_results, prefix='lmt_')

    return results

def retrieve_topology_data(results, slurm_cache_file, craysdb_cache_file):
    """
    Get the diameter of the job (Cray XC)
    """
    if craysdb_cache_file is not None:
        if '_jobid' not in results:
            # bail out
            return results

        # verify craysdb cache file
        if craysdb_cache_file == "":
            craysdb_cache_file = None
        else:
            craysdb_cache_file = craysdb_cache_file

        # verify slurm cache file
        if slurm_cache_file == "" \
        or slurm_cache_file is None \
        or not os.path.isfile(slurm_cache_file):
            slurm_cache_file = None

        module_results = tokio.tools.topology.get_job_diameter(
            results['_jobid'],
            slurm_cache_file=slurm_cache_file,
            craysdb_cache_file=craysdb_cache_file)
        merge_dicts(results, module_results, prefix='topology_')
    return results

def retrieve_jobid(results, jobid, file_count):
    """
    Get JobId from either Slurm or the CLI argument
    """
    if jobid is not None:
        if file_count > 1:
            raise Exception("Behavior of --jobid when files > 1 is undefined")
        if os.path.isfile(jobid):
            slurm_data = tokio.connectors.slurm.Slurm(cache_file=jobid)
            results['_jobid'] = slurm_data.get_job_ids()[0]
        else:
            results['_jobid'] = jobid
    return results


def retrieve_ost_data(results, ost, ost_fullness=None, ost_map=None):
    """
    Get Lustre server status via lfsstatus tool
    """
    if ost:
        # Divine the sonexion name from the file system map
        fs_key = results.get('_file_system')
        if fs_key is None or fs_key not in tokio.config.FSNAME_TO_H5LMT_FILE:
            return results
        snx_name = tokio.config.FSNAME_TO_H5LMT_FILE[fs_key].split('_')[-1].split('.')[0]

        # Get the OST fullness summary
        module_results = tokio.tools.lfsstatus.get_fullness_at_datetime(snx_name,
                                                                        results['_datetime_start'],
                                                                        cache_file=ost_fullness)
        merge_dicts(results, module_results, prefix='fshealth_')

        # Get the OST failure status
        # Note that get_failures_at_datetime will clobber the
        # ost_timestamp_* keys from get_fullness_at_datetime above;
        # these aren't used for correlation analysis and should be
        # pretty close anyway.
        module_results = tokio.tools.lfsstatus.get_failures_at_datetime(snx_name,
                                                                        results['_datetime_start'],
                                                                        cache_file=ost_map)
        merge_dicts(results, module_results, False, prefix='fshealth_')

        # A measure, in sec, expressing how far before the job our OST fullness data was measured
        results['fshealth_ost_fullness_lead_secs'] = \
            (results['_datetime_start'] \
            - datetime.datetime.fromtimestamp(
                results['fshealth_ost_actual_timestamp'])).total_seconds()

        # Ost_overloaded_pct becomes the percent of OSTs in file system which are
        # in an abnormal state
        results["fshealth_ost_overloaded_pct"] = \
            100.0 * float(results["fshealth_ost_overloaded_ost_count"]) \
            / float(results["fshealth_ost_count"])

        # A measure, in sec, expressing how far before the job our OST failure data was measured
        results['fshealth_ost_failures_lead_secs'] = \
            (results['_datetime_start'] \
            - datetime.datetime.fromtimestamp(
                results['fshealth_ost_actual_timestamp'])).total_seconds()

    return results

def retrieve_concurrent_job_data(results, jobhost, concurrentjobs):
    """
    Get information about all jobs that were running during a time period
    """

    if concurrentjobs is not None \
    and results['_datetime_start'] is not None \
    and results['_datetime_end'] is not None \
    and jobhost is not None:
        if concurrentjobs == "":
            cache_file = None
        else:
            cache_file = concurrentjobs

        start_stamp = long(time.mktime(results['_datetime_start'].timetuple()))
        end_stamp = long(time.mktime(results['_datetime_end'].timetuple()))
        nerscjobsdb = tokio.connectors.nersc_jobsdb.NerscJobsDb(cache_file=cache_file)
        concurrent_job_info = nerscjobsdb.get_concurrent_jobs(start_stamp, end_stamp, jobhost)
        results['jobsdb_concurrent_jobs'] = concurrent_job_info['numjobs']
        results['jobsdb_concurrent_nodes'] = concurrent_job_info['numnodes']
        results['jobsdb_concurrent_nodehrs'] = concurrent_job_info['nodehrs']
    return results

def summarize_job():
    """
    CLI wrapper around process that pulls in data from a variety of connectors
    and reports a summary of data from all connectors for a time range of
    interest.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("-t", "--topology", nargs='?', const="", type=str,
                        help="include job diameter (Cray XC only);"
                        + " can specify optional path to cached xtprocadmin output")
    parser.add_argument("-c", "--concurrentjobs", nargs='?', const="", type=str,
                        help="add number of jobs concurrently running from jobsdb;"
                        + " can specify optional path to cache db")
    parser.add_argument("-o", "--ost", action='store_true',
                        help="add information about OST fullness/failover")
    parser.add_argument("-j", "--json", help="output in json", action="store_true")
    parser.add_argument("-f", "--file-system", type=str, default=None,
                        help="file system name (e.g., cscratch, bb-private)")
    parser.add_argument("--start-time", type=str, default=None,
                        help="start time of job, in YYYY-MM-DD HH:MM:SS format")
    parser.add_argument("--end-time", type=str, default=None,
                        help="end time of job, in YYYY-MM-DD HH:MM:SS format")
    parser.add_argument("--slurm-jobid", type=str, default=None,
                        help="job id or path to slurm cache file"
                        + " (req'd w/ --topology, --concurrentjobs, or if no darshan log provided)")
    parser.add_argument("--jobhost", type=str, default=None,
                        help="host on which job ran (used with --concurrentjobs)")
    parser.add_argument("--ost-fullness", type=str, default=None, nargs="?",
                        help="path to an ost fullness file (lfs df)")
    parser.add_argument("--ost-map", type=str, default=None, nargs="?",
                        help="path to an ost map file (lctl dl -t)")
    parser.add_argument("files", nargs='*', default=None,
                        help="darshan logs to process")
    args = parser.parse_args()
    json_rows = []
    records_to_process = 0

    # If --start-time is specified, --end-time MUST be specified, and
    # files CANNOT be specified
    if (args.start_time is not None and args.end_time is None) \
    or (args.start_time is None and args.end_time is not None):
        raise Exception("--start-time and --end-time must be specified together")
    elif args.start_time is not None:
        # If files are specified, --start-time becomes ambiguous
        if len(args.files) > 1:
            raise Exception("--start-time and files cannot be specified together")
        records_to_process = 1
        results = {
            '_datetime_start': datetime.datetime.strptime(args.start_time, "%Y-%m-%d %H:%M:%S"),
            '_datetime_end': datetime.datetime.strptime(args.end_time, "%Y-%m-%d %H:%M:%S"),
        }
    else:
        records_to_process = len(args.files)
        # Let Darshan log define datetime_start and datetime_end
        results = {}

    # If --jobid is specified, override whatever is in the Darshan log
    results = retrieve_jobid(results, args.slurm_jobid, len(args.files))
    for i in range(records_to_process):
        try:
            # records_to_process == 1 but len(args.files) == 0 when no darshan log is given
            if len(args.files) > 0:
                results = retrieve_darshan_data(results, args.files[i])
            results = retrieve_lmt_data(results, args.file_system)
            results = retrieve_topology_data(results,
                                             slurm_cache_file=args.slurm_jobid,
                                             craysdb_cache_file=args.topology)
            results = retrieve_ost_data(results, args.ost, args.ost_fullness, args.ost_map)
            results = retrieve_concurrent_job_data(results, args.jobhost, args.concurrentjobs)
        except:
            # print out file name to aid debugging when processing multiple logs
            warnings.warn("Unhandled exception while processing %s" % args.files[i])
            raise

        # don't append empty rows
        if len(results) > 0:
            json_rows.append(results)
        results = {}

    if args.json:
        print json.dumps(json_rows, indent=4, sort_keys=True, default=serialize_datetime)
    else:
        tmp_df = pandas.DataFrame.from_records(json_rows)
        tmp_df.index.name = "index"
        print tmp_df.to_csv()

if __name__ == "__main__":
    summarize_job()
