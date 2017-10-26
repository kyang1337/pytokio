#!/usr/bin/env python
"""
WORK IN PROGRESS

Currently a proof-of-concept to show how an HDF5 file can be populated with the
outputs of collectd's ElasticSearch integration.  Currently does NOT use the
collectd_es plugin, but rather ingests the raw ElasticSearch json output and
parses it.

Needs a lot of work to integrate connectors.hdf5 and connectors.collectdes.

Syntax:
    ./cache_collectdes.py 2017-08-27T00:00:00 2017-08-28T00:00:00 \
                          ../tests/inputs/sample_collectd_es.?.json.gz

Presently you must specify a whole day's worth of time range manually to get an
HDF5 file that encompasses a whole day.
"""

import os
import gzip
import json
import time
import datetime
import argparse
import warnings

import dateutil.parser # because of how ElasticSearch returns time data
import h5py
import numpy

COLLECTD_TIMESTEP = 10 ### ten second polling interval

EPOCH = dateutil.parser.parse('1970-01-01T00:00:00.000Z')

def cache_collectdes():
    """
    Parse cached json[.gz] files from ElasticSearch and convert them into HDF5
    files.
    """
    warnings.simplefilter('always', UserWarning) # One warning per invalid file
    parser = argparse.ArgumentParser()
    parser.add_argument("start", type=str, help="start time in YYYY-MM-DDTHH:MM:SS format")
    parser.add_argument("end", type=str, help="end time in YYYY-MM-DDTHH:MM:SS format")
    parser.add_argument("file", nargs="+", default=None, type=str,
                        help="collectd elasticsearch outputs to process")
    #parser.add_argument("darshanlogs", nargs="*", default=None, help="darshan logs to process")
    parser.add_argument("-o", "--output", type=str, default='output.hdf5', help="output file")
    args = parser.parse_args()

    ### Decode the first and second ISDCT dumps
    t_start = datetime.datetime.strptime(args.start, "%Y-%m-%dT%H:%M:%S")
    t_end = datetime.datetime.strptime(args.end, "%Y-%m-%dT%H:%M:%S")

    if t_start >= t_end:
        raise Exception('t_start >= t_end')

    ### Currently hard-coded knowledge of the number of Burst Buffer servers in
    ### Cori; in the future, we should leave this blank and choose the correct
    ### array size inside of commit_datasets after we know the total number of
    ### servers.  Alternatively, we can run a separate ElasticSearch query ahead
    ### of time to find out how many BB servers to expect before we start
    ### scrolling through the raw documents.
    rows, read_dataset = init_datasets(t_start, t_end, num_servers=288) # TODO fix hard-coded 288
    rows, write_dataset = init_datasets(t_start, t_end, num_servers=288) # TODO: fix hard-coded 288

    ### Iterate over every cached page.  In practice this should loop over
    ### scrolled pages coming out of ElasticSearch.
    column_map = {}
    progress = 0
    num_files = len(args.file)
    for input_filename in args.file:
        progress += 1
        print "Processing file %d of %d" % (progress, num_files)
        if input_filename.endswith('.gz'):
            input_file = gzip.open(input_filename, 'r')
        else:
            input_file = open(input_filename, 'r')

        try:
            page = json.load(input_file)
        except ValueError as error:
            # don't barf on invalid json
            warnings.warn(str(error))
            continue
        update_datasets(page, rows, column_map, read_dataset, write_dataset)

    ### Build the list of column names from the retrieved data
    columns = [None] * len(column_map)
    for column_name, index in column_map.iteritems():
        columns[index] = str(column_name) # can't keep it in unicode; numpy doesn't support this

    ### Write out the final HDF5 file after everything is loaded into memory.
    commit_datasets(args.output, rows, columns, datasets={'/bytes/readrates': read_dataset, '/bytes/writerates': write_dataset})

def commit_datasets(hdf5_filename, rows, columns, datasets):
    """
    Convert numpy arrays, a list of timestamps, and a list of column headers
    into groups and datasets within an HDF5 file.
    """
    # Create/open the HDF5 file
    hdf5_file = h5py.File(hdf5_filename)

    # Create the read rate dataset
    for dataset_name, dataset in datasets.iteritems():
        check_create_dataset(hdf5_file=hdf5_file,
                             name=dataset_name,
                             shape=dataset.shape,
                             chunks=True,
                             compression='gzip',
                             dtype='f8')

        ### TODO: sort the columns at this point

        ### TODO: should only update the slice of the hdf5 file that needs to be
        ###       changed; for example, maybe calculate the min/max row and
        ###       column touched, then use these as slice indices

        # Populate the read rate dataset and set basic metadata
        hdf5_file[dataset_name][:, :] = dataset
        hdf5_file[dataset_name].attrs['columns'] = columns
        print "Committed %s of shape %s" % (dataset_name, dataset.shape)

    # Create the dataset that contains the timestamps which correspond to each
    # row in the read/write datasets
    dataset_name = os.path.join(os.path.dirname(dataset_name), 'timestamps')
    check_create_dataset(hdf5_file=hdf5_file,
                         name=dataset_name,
                         shape=rows.shape,
                         dtype='i8')
    hdf5_file[dataset_name][:] = rows
    print "Committed %s of shape %s" % (dataset_name, dataset.shape)

def check_create_dataset(hdf5_file, name, shape, dtype, **kwargs):
    """
    Create a dataset if it does not exist.  If it does exist and has the correct
    shape, do nothing.
    """
    if name in hdf5_file:
        if hdf5_file[name].shape != shape:
            raise Exception('Dataset %s of shape %s already exists with shape %s' %
                            name,
                            shape,
                            hdf5_file[name].shape)
    else:
        hdf5_file.create_dataset(name=name,
                             shape=shape,
                             dtype=dtype,
                             **kwargs)


def init_datasets(start_time, end_time, num_servers=10000):
    """
    Initialize an HDF5 object.  Takes a start and end time and returns a numpy
    array and a row of timestamps that can be used to initialize an HDF5
    dataset.  Keeps everything in-memory as a numpy array to minimize overhead
    of interacting with h5py.
    """

    date_range = (end_time - start_time).total_seconds()
    num_bins = int(date_range / COLLECTD_TIMESTEP)
    data_dataset = numpy.full((num_bins, num_servers), -0.0)
    time_list = []
    timestamp = start_time
    while timestamp < end_time:
        time_list.append(long(time.mktime(timestamp.timetuple())))
        timestamp += datetime.timedelta(seconds=COLLECTD_TIMESTEP)
    rows = numpy.array(time_list)

    return rows, data_dataset

def update_datasets(pages, rows, column_map, read_dataset, write_dataset):
    """
    Go through a list of pages and insert their data into a numpy matrix.  In
    the future this should be a flush function attached to the CollectdEs
    connector class.
    """
    time0 = rows[0] # in seconds since 1/1/1970
    timestep = rows[1] - rows[0] # in seconds

    data_volume = [0, 0]
    for page in pages:
        for doc in page:
            # basic validity checking
            if '_source' not in doc:
                warnings.warn("No _source in doc %s" % doc['_id'])
                continue
            source = doc['_source']
            # check to see if this is from plugin:disk
            if source['plugin'] != 'disk' or 'read' not in source:
                continue
            timestamp = long((dateutil.parser.parse(source['@timestamp']) - EPOCH).total_seconds())
            t_index = (timestamp - time0) / timestep
            s_index = column_map.get(source['hostname'])
            # if this is a new hostname, create a new column for it
            if s_index is None:
                s_index = len(column_map)
                column_map[source['hostname']] = s_index
            # bounds checking
            if t_index >= read_dataset.shape[0] \
            or t_index >= write_dataset.shape[0]:
                warnings.warn("Index %d out of bounds (0:%d) for timestamp %s" % (t_index, read_dataset.shape[0], timestamp))
                continue
            read_dataset[t_index, s_index] = source['read']
            write_dataset[t_index, s_index] = source['write']

            data_volume[0] += source['read'] * timestep
            data_volume[1] += source['write'] * timestep
        print "Added %d bytes of read, %d bytes of write" % (data_volume[0], data_volume[1])

if __name__ == "__main__":
    cache_collectdes()
