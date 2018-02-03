#/usr/bin/env python
"""
TimeSeries class to simplify updating and manipulating the in-memory
representation of time series data.
"""

import re
import math
import time
import datetime
import warnings
import numpy

TIMESTAMP_KEY = 'timestamps'
COLUMN_NAME_KEY = 'columns'

class TimeSeries(object):
    """
    In-memory representation of an HDF5 group in a TokioFile.  Can either
    initialize with no datasets, or initialize against an existing HDF5
    group.
    """
    def __init__(self, start=None, end=None, timestep=None, group=None,
                 timestamp_key=TIMESTAMP_KEY, sort_hex=False):
        self.timestamps = None
        self.time0 = None
        self.timestep = None

        self.dataset = None
        self.dataset_name = None
        self.columns = []
        self.num_columns = 0
        self.column_map = {}
        self.group_metadata = {}
        self.dataset_metadata = {}
        self.timestamp_key = timestamp_key
        self.sort_hex = sort_hex

        if group is not None:
            self.attach_group(group)
        else:
            if start is None or end is None or timestep is None:
                raise Exception("Must specify either ({start,end,timestep}|group)")
            else:
                self.init_group(start, end, timestep)

    def attach_group(self, group):
        """
        Attach to an existing h5py Group object
        """
        if self.timestamp_key not in group:
            raise Exception("Existing group contains no timestamps")

        self.timestamps = group[self.timestamp_key][:]
        print "Attached to existing group with timestamps of shape", self.timestamps.shape
        self.time0 = self.timestamps[0]
        self.timestep = self.timestamps[1] - self.timestamps[0]
        for key, value in group.attrs.iteritems():
            self.group_metadata[key] = value

    def init_group(self, start, end, timestep):
        """
        Initialize the object from scratch
        """
        if start is None or end is None or timestep is None:
            raise Exception("Must specify either ({start,end,timestep}|group)")
        self.time0 = long(time.mktime(start.timetuple()))
        self.timestep = timestep

        time_list = []
        timestamp = start
        while timestamp < end:
            time_list.append(long(time.mktime(timestamp.timetuple())))
            timestamp += datetime.timedelta(seconds=timestep)

        self.timestamps = numpy.array(time_list)

    def update_column_map(self):
        """
        Create the mapping of column names to column indices
        """
        self.column_map = {}
        for index, column_name in enumerate(self.columns):
            self.column_map[column_name] = index

    def init_dataset(self, *args, **kwargs):
        """
        Dimension-independent wrapper around init_dataset2d
        """
        self.init_dataset2d(*args, **kwargs)

    def attach_dataset(self, *args, **kwargs):
        """
        Dimension-independent wrapper around attach_dataset2d
        """
        self.attach_dataset2d(*args, **kwargs)

    def init_dataset2d(self, dataset_name, num_columns, default_value=-0.0):
        """
        Initialize the dataset from scratch
        """
        self.dataset_name = dataset_name
        self.columns = []
        self.num_columns = len(self.columns)
        self.dataset = numpy.full((len(self.timestamps), num_columns), default_value)
        self.update_column_map()

    def attach_dataset2d(self, dataset):
        """
        Initialize the dataset from an existing h5py Dataset object
        """
        self.dataset_name = dataset.name
        self.dataset = dataset[:, :]
        if COLUMN_NAME_KEY in dataset.attrs:
            columns = list(dataset.attrs[COLUMN_NAME_KEY])
            num_columns = len(columns)
            # handle case where HDF5 has more column names than dataset columns via truncation
            if num_columns > self.dataset.shape[1]:
                columns = columns[0:self.dataset.shape[1]]
                truncated = columns[self.dataset.shape[1]:]
                warnings.warn(
                    "Dataset has %d column names but %d columns; truncating columns %s"
                    % (num_columns, self.dataset.shape[1], ', '.join(truncated)))
            # add columns one by one
            for column in columns:
                self.add_column(column)
        else:
            warnings.warn("attaching to a columnless dataset (%s)" % self.dataset_name)

        for key, value in dataset.attrs.iteritems():
            self.dataset_metadata[key] = value

    def commit_dataset(self, *args, **kwargs):
        """
        Dimension-independent wrapper around commit_dataset2d
        """
        self.commit_dataset2d(*args, **kwargs)

    def commit_dataset2d(self, hdf5_file):
        """
        Write contents of this object into an HDF5 file group
        """
        # Create the timestamps dataset
        group_name = '/'.join(self.dataset_name.split('/')[0:-1])
        timestamps_dataset_name = group_name + '/' + self.timestamp_key

        # If we are creating a new group, first insert the new timestamps
        if timestamps_dataset_name not in hdf5_file:
            hdf5_file.create_dataset(name=timestamps_dataset_name,
                                     shape=self.timestamps.shape,
                                     dtype='i8')
            # Copy the in-memory timestamp dataset into the HDF5 file
            hdf5_file[timestamps_dataset_name][:] = self.timestamps[:]
        # Otherwise, verify that our dataset will fit into the existing timestamps
        else:
            if not numpy.array_equal(self.timestamps, hdf5_file[timestamps_dataset_name][:]):
                print 'we have shape', self.timestamps.shape
                print 'but we need to fit into shape', hdf5_file[timestamps_dataset_name].shape
                raise Exception("Attempting to commit to a group with different timestamps")

        # Insert/update group metadata
        for key, value in self.group_metadata.iteritems():
            hdf5_file[group_name].attrs[key] = value

        # Create the dataset in the HDF5 file
        if self.dataset_name not in hdf5_file:
            hdf5_file.create_dataset(name=self.dataset_name,
                                     shape=self.dataset.shape,
                                     dtype='f8',
                                     chunks=True,
                                     compression='gzip')

        # If we're updating an existing HDF5, use its column names and ordering.
        # Otherwise sort the columns before committing them.
        if COLUMN_NAME_KEY in hdf5_file[self.dataset_name].attrs:
            self.rearrange_columns(list(hdf5_file[self.dataset_name].attrs[COLUMN_NAME_KEY]))
        else:
            self.sort_columns()

        # Copy the in-memory dataset into the HDF5 file
        hdf5_file[self.dataset_name][:, :] = self.dataset[:, :]

        # Copy column names into metadata before committing metadata
        self.dataset_metadata[COLUMN_NAME_KEY] = self.columns
        self.dataset_metadata['updated'] = long(time.mktime(datetime.datetime.now().timetuple()))

        # Insert/update dataset metadata
        for key, value in self.dataset_metadata.iteritems():
            hdf5_file[self.dataset_name].attrs[key] = value

    def set_columns(self, column_names):
        """
        Set the list of column names
        """
        self.columns = column_names
        self.num_columns = len(self.columns)
        self.update_column_map()

    def add_column(self, column_name):
        """
        Add a new column and update the column map
        """
        index = self.num_columns
        if column_name in self.column_map:
            warnings.warn("Adding degenerate column '%s' at %d (exists at %d)"
                          % (column_name, index, self.column_map[column_name]))
        self.column_map[column_name] = index
        if index >= (self.num_columns + 1):
            raise IndexError("index %d exceeds length of columns %d" % (index, self.num_columns))
        self.columns.append(str(column_name)) # convert from unicode to str for numpy
        self.num_columns += 1
        return index

    def sort_columns(self):
        """
        Rearrange the dataset's column data by sorting them by their headings
        """
        self.rearrange_columns(sorted_nodenames(self.columns, sort_hex=self.sort_hex))

    def rearrange_columns(self, new_order):
        """
        Rearrange the dataset's columnar data by an arbitrary column order given
        as an enumerable list
        """
        # validate the new order - new_order must contain at least all of
        # the elements in self.columns, but may contain more than that
        for new_key in new_order:
            if new_key not in self.columns:
                raise Exception("key %s in new_order not in columns" % new_key)

        # walk the new column order
        for new_index, new_column in enumerate(new_order):
            # new_order can contain elements that don't exist; this happens when
            # re-ordering a small dataset to be inserted into an existing,
            # larger dataset
            if new_column not in self.columns:
                warnings.warn("Column '%s' in new order not present in TimeSeries" % new_column)
                continue

            old_index = self.column_map[new_column]
            self.swap_columns(old_index, new_index)

    def swap_columns(self, index1, index2):
        """
        Swap two columns of the dataset in-place
        """
        # save the data from the column we're about to swap
        saved_column_data = self.dataset[:, index2].copy()
        saved_column_name = self.columns[index2]

        # swap column data
        self.dataset[:, index2] = self.dataset[:, index1]
        self.dataset[:, index1] = saved_column_data[:]

        # swap column names too
        self.columns[index2] = self.columns[index1]
        self.columns[index1] = saved_column_name

        # update the column map
        self.column_map[self.columns[index2]] = index2
        self.column_map[self.columns[index1]] = index1

    def insert_element(self, timestamp, column_name, value, reducer=None):
        """
        Given a timestamp (datetime.datetime object) and a column name (string),
        update an element of the dataset.  If a reducer function is provided,
        use that function to reconcile any existing values in the element to be
        updated.
        """
        # calculate the timeseries index and check bounds
        timestamp_epoch = long(time.mktime(timestamp.timetuple()))
        t_index = (timestamp_epoch - self.time0) / self.timestep
        if t_index >= self.timestamps.shape[0]:
            return False

        # if this is a new hostname, create a new column for it
        c_index = self.column_map.get(column_name)
        if c_index is None:
            c_index = self.add_column(column_name)

        # actually copy the two data points into the datasets
        old_value = self.dataset[t_index, c_index]
        if self.dataset[t_index, c_index] == 0.0 \
        and math.copysign(1, old_value) < 0.0 \
        and reducer is not None:
            self.dataset[t_index, c_index] = reducer(old_value, value)
        else:
            self.dataset[t_index, c_index] = value
        return True

    def missing_matrix(self, inverse=False):
        """
        Because we initialize datasets with -0.0, we can scan the sign bit of every
        element of an array to determine how many data were never populated.  This
        converts negative zeros to ones and all other data into zeros then count up
        the number of missing elements in the array.
        """
        if inverse:
            converter = numpy.vectorize(lambda x: 0 if (x == 0.0 and math.copysign(1, x) < 0.0) else 1)
        else:
            converter = numpy.vectorize(lambda x: 1 if (x == 0.0 and math.copysign(1, x) < 0.0) else 0)
        return converter(self.dataset)

    def convert_to_deltas(self):
        """
        Convert a matrix of monotonically increasing rows into deltas.  Replaces
        self.dataset with a matrix with the same number of columns but one fewer
        row (taken off the bottom of the matrix).  Also adjusts the timestamps
        dataset.
        """
        self.dataset = timeseries_deltas(self.dataset)
        self.timestamps = self.timestamps[0:-1]

    def trim_rows(self, num_rows=1):
        """
        Trim some rows off the end of self.dataset and self.timestamps
        """
        self.dataset = self.dataset[0:-1*num_rows]
        self.timestamps = self.timestamps[0:-1*num_rows]

def sorted_nodenames(nodenames, sort_hex=False):
    """
    Gnarly routine to sort nodenames naturally.  Required for nodes named things
    like 'bb23' and 'bb231'.
    """
    def extract_int(string):
        """
        Convert input into an int if possible; otherwise return unmodified
        """
        try:
            if sort_hex:
                return int(string, 16)
            else:
                return int(string)
        except ValueError:
            return string

    def natural_compare(string):
        """
        Tokenize string into alternating strings/ints if possible
        """
        return map(extract_int, re.findall(r'(\d+|\D+)', string))

    def natural_hex_compare(string):
        """
        Tokenize string into alternating strings/ints if possible.  Also
        recognizes hex, so be careful with ambiguous nodenames like "bb234",
        which is valid hex.
        """
        return map(extract_int, re.findall(r'([0-9a-fA-F]+|[^0-9a-fA-F]+)', string))

    def natural_comp(arg1, arg2):
        """
        Cast the parts of a string that look like integers into integers, then
        sort based on strings and integers rather than only strings
        """
        return cmp(natural_compare(arg1), natural_compare(arg2))

    def natural_hex_comp(arg1, arg2):
        """
        Cast the parts of a string that look like hex into integers, then
        sort based on strings and integers rather than only strings.
        """
        return cmp(natural_hex_compare(arg1), natural_hex_compare(arg2))

    if sort_hex:
        return sorted(nodenames, natural_hex_comp)
    else:
        return sorted(nodenames, natural_comp)

def timeseries_deltas(dataset):
    """
    Subtract every row of the dataset from the row that precedes it to
    convert a matrix of monotonically increasing rows into deltas.  This is a
    lossy process because the deltas for the final measurement of the time
    series cannot be calculated.
    """
    diff_matrix = numpy.full((dataset.shape[0] - 1, dataset.shape[1]), -0.0)

    prev_nonzero = [None] * dataset.shape[1] # the last known valid measurement
    searching = [True] * dataset.shape[1] # are we spanning a gap in data?
    for irow in range(dataset.shape[0]):
        for icol in range(dataset.shape[1]):
            this_element = dataset[irow, icol]

            if irow == 0:
                if this_element != 0.0:
                    prev_nonzero[icol] = this_element
            elif searching[icol]:
                if this_element != 0.0:
                    if prev_nonzero[icol] is not None:
                        diff_matrix[irow - 1, icol] = this_element - prev_nonzero[icol]
                        searching[icol] = False
                    prev_nonzero[icol] = this_element
            else:
                if this_element < dataset[irow - 1, icol]: # found a missing data point
                    searching[icol] = True
                else:
                    diff_matrix[irow - 1, icol] = this_element - dataset[irow - 1, icol]
                    prev_nonzero[icol] = this_element

    return diff_matrix
