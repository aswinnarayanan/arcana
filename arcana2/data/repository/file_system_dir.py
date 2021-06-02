import os
import os.path as op
import errno
from typing import List
from itertools import chain, zip_longest
from collections.abc import Iterable
from collections import defaultdict
import stat
import shutil
import logging
import json
from copy import copy
from fasteners import InterProcessLock
from arcana2.data import FileGroup, Field
from arcana2.data.item import Provenance
from arcana2.exceptions import (
    ArcanaError, ArcanaUsageError,
    ArcanaRepositoryError,
    ArcanaMissingDataException,
    ArcanaInsufficientRepoDepthError)
from arcana2.utils import get_class_info, HOSTNAME, split_extension
from ..dataset import Dataset
from ..enum import Clinical, DataFrequency
from .base import Repository


logger = logging.getLogger('arcana')


class FileSystemDir(Repository):
    """
    A Repository class for data stored hierarchically within sub-directories
    of a file-system directory. The depth and which layer in the data tree
    the sub-directories correspond to is defined by the `layers` argument.

    Parameters
    ----------
    base_dir : str
        Path to the base directory of the "repository", i.e. datasets are
        arranged by name as sub-directories of the base dir.
    frequencies : List[DataFrequency]
        The frequencies that each sub-directory layers corresponds to.
        Each frequency in the list should contain the layers of the previous
        frequencies [Clinical.dataset, Clinical.group,
        Clinical.subject, Clinical.session] would specify a 3-level
        directory structure, with the first level sorting by study group, the
        second by member ID (position within group) and then the study
        timepoint. Alternatively, [Clinical.dataset, Clinical.member,
        Clinical.subject] would specify a 2-level structure where the data
        is organised into directories for matching members between groups and
        then the groups in sub-directories containing sub-directories.
    """

    type = 'file_system_dir'
    NODE_DIR = '__node__'
    PROV_SUFFIX = '.__prov__.json'
    FIELDS_FNAME = '__fields__.json'
    LOCK_SUFFIX = '.lock'
    PROV_KEY = 'provenance'
    VALUE_KEY = 'value'

    def __init__(self, base_dir, frequencies):
        self.base_dir = base_dir
        self.frequencies = list(frequencies)
        if not len(self.frequencies):
            raise ArcanaUsageError(
                "At least one layer must be provided to FileSystemDir")
        for prev_freq, freq in zip(self.frequencies[:-1],
                                   self.frequencies[1:]):
            if not isinstance(freq, type(prev_freq)):
                raise ArcanaUsageError(
                    "Mismatching data frequencies provided to FileSystemDir. "
                    "The must all be of the same Enum class "
                    f"({freq} and {prev_freq})")
            if (freq.value | prev_freq.value) != freq.value:
                raise ArcanaUsageError(
                    "Subsequent frequencies in list provided to FileSystemDir "
                    "must have a superset of layers to previous frequencies "
                    f"({freq}: {freq.layers} and "
                    f"{prev_freq}: {prev_freq.layers})")


    def __repr__(self):
        return (f"{type(self).__name__}(base_dir={self.base_dir}, "
                f"layers={self.layers})")

    def __eq__(self, other):
        try:
            return (self.layers == other.layers
                    and self.base_dir == other.base_dir)
        except AttributeError:
            return False

    @property
    def prov(self):
        return {
            'type': get_class_info(type(self)),
            'host': HOSTNAME,
            'base_dir': self.base_dir,
            'layers': [str(l) for l in self.layers]}

    def __hash__(self):
        return hash(self.type)

    def standardise_name(self, name):
        return op.abspath(name)

    def get_file_group(self, file_group):
        """
        Set the path of the file_group from the repository
        """
        # Don't need to cache file_group as it is already local as long
        # as the path is set
        if file_group.local_path is None:
            primary_path = self.file_group_path(file_group)
            aux_files = file_group.format.default_aux_file_paths(primary_path)
            if not op.exists(primary_path):
                raise ArcanaMissingDataException(
                    "{} does not exist in {}"
                    .format(file_group, self))
            for aux_name, aux_path in aux_files.items():
                if not op.exists(aux_path):
                    raise ArcanaMissingDataException(
                        "{} is missing '{}' side car in {}"
                        .format(file_group, aux_name, self))
        else:
            primary_path = file_group.local_path
            aux_files = file_group.aux_files
        return primary_path, aux_files

    def get_file_group_provenance(self, file_group):
        if file_group.local_path is not None:
            prov = Provenance.load(self.prov_json_path(file_group))
        else:
            prov = None
        return prov

    def get_field(self, field):
        """
        Update the value of the field from the repository
        """
        val = self._get_field_val(field)
        if isinstance(val, dict):
            val = val[self.VALUE_KEY]
        if field.array:
            val = [field.dtype(v) for v in val]
        else:
            val = field.dtype(val)
        return val

    def get_field_provenance(self, field):
        """
        Loads the fields provenance from the JSON dictionary
        """
        val_dct = self._get_field_val(field)
        if isinstance(val_dct, dict):
            prov = val_dct.get(self.PROV_KEY)
        else:
            prov = None
        return prov

    def _get_field_val(self, field):
        """
        Load fields JSON, locking to prevent read/write conflicts
        Would be better if only checked if locked to allow
        concurrent reads but not possible with multi-process
        locks (in my understanding at least).
        """
        fpath = self.fields_json_path(field)
        try:
            with InterProcessLock(fpath + self.LOCK_SUFFIX,
                                  logger=logger), open(fpath, 'r') as f:
                dct = json.load(f)
            val_dct = dct[field.name]
            return val_dct
        except (KeyError, IOError) as e:
            try:
                # Check to see if the IOError wasn't just because of a
                # missing file
                if e.errno != errno.ENOENT:
                    raise
            except AttributeError:
                pass
            raise ArcanaMissingDataException(
                "{} does not exist in the local repository {}"
                .format(field.name, self))

    def put_file_group(self, file_group):
        """
        Inserts or updates a file_group in the repository
        """
        target_path = self.file_group_path(file_group)
        if op.isfile(file_group.path):
            shutil.copyfile(file_group.path, target_path)
            # Copy side car files into repository
            for aux_name, aux_path in file_group.format.default_aux_file_paths(
                    target_path).items():
                shutil.copyfile(file_group.format.aux_files[aux_name],aux_path)
        elif op.isdir(file_group.path):
            if op.exists(target_path):
                shutil.rmtree(target_path)
            shutil.copytree(file_group.path, target_path)
        else:
            assert False
        if file_group.provenance is not None:
            file_group.provenance.save(self.prov_json_path(file_group))

    def put_field(self, field):
        """
        Inserts or updates a field in the repository
        """
        fpath = self.fields_json_path(field)
        # Open fields JSON, locking to prevent other processes
        # reading or writing
        with InterProcessLock(fpath + self.LOCK_SUFFIX, logger=logger):
            try:
                with open(fpath, 'r') as f:
                    dct = json.load(f)
            except IOError as e:
                if e.errno == errno.ENOENT:
                    dct = {}
                else:
                    raise
            if field.array:
                value = list(field.value)
            else:
                value = field.value
            if field.provenance is not None:
                value = {self.VALUE_KEY: value,
                         self.PROV_KEY: field.provenance.dct}
            with open(fpath, 'w') as f:
                json.dump(dct, f, indent=2)

    # root_dir=None, all_namespace=None,
    def construct_dataset(self, dataset: Dataset, **kwargs):
        """
        Find all data within a repository, registering file_groups, fields and
        provenance with the found_file_group, found_field and found_provenance
        methods, respectively

        Parameters
        ----------
        dataset : Dataset
            The dataset to construct the tree structure for
        """

        def load_prov(dpath, bname):
            prov_path = op.join(dpath, bname + self.PROV_SUFFIX)
            if op.exists(prov_path):
                prov = Provenance.load(prov_path)
            else:
                prov = None
            return prov

        def construct_node(dpath, ids=[], dname=None):
            if dname is not None:
                dpath = op.join(dpath, dname)
                ids += [dname]
            # First ID can be omitted
            node_freq = self.frequencies[len(ids)]  # last freq
            ids_dict = dict(zip(self.frequencies, ids))
            node = dataset.add_node(node_freq, ids_dict)
            # Check if node is a leaf (i.e. lowest level in directory
            # structure)
            is_leaf_node = node_freq == self.frequencies[-1]
            filtered, has_fields = self._list_node_dir_contents(
                dpath, is_leaf=is_leaf_node)
            # Group files and sub-dirs that match except for extensions
            matching = defaultdict(set)
            for fname in filtered:
                basename = fname.split('.')[0]
                matching[basename].add(fname)
            # Add file groups
            for bname, fnames in matching.items():
                node.add_file_group(
                    path=bname,
                    local_paths=[op.join(dpath, f) for f in fnames],
                    provenance=load_prov(dpath, bname))
            # Add fields
            if has_fields:
                with open(op.join(dpath, self.FIELDS_FNAME), 'r') as f:
                    dct = json.load(f)
                for name, value in dct.items():
                    if isinstance(value, dict):
                        prov = value[self.PROV_KEY]
                        value = value[self.VALUE_KEY]
                    else:
                        prov = None
                    node.add_field(path=name, value=value, provenance=prov)
            # Add sub-directory nodes
            if not is_leaf_node:
                for sub_dir in os.listdir(dpath):
                    if (not sub_dir.startswith('.')
                            and sub_dir != self.NODE_DIR):
                        construct_node(dpath, ids=ids, dname=sub_dir)
                
        construct_node(op.join(self.base_dir, dataset.name))

    @classmethod
    def _list_node_dir_contents(cls, path, is_leaf):
        # Matcher out hidden files (i.e. starting with '.')
        if not is_leaf:
            path += cls.NODE_DIR
        filtered = []
        has_fields = False
        if op.exists(path):
            contents = os.listdir(path)
            for item in contents:
                if (item.startswith('.') or item == cls.FIELDS_FNAME
                        or item.endswith(cls.PROV_SUFFIX)):
                    continue
                filtered.append(item)
            has_fields = cls.FIELDS_FNAME in contents
        return filtered, has_fields

    @classmethod
    def _provenance_file_path(cls, path):
        return path.split('.')[0] + cls.PROV_SUFFIX

    def node_path(self, data_node):
        return op.join(self.base_dir,
                       *(data_node.ids[f] for f in self.frequencies))

    def fields_json_path(self, field):
        return op.join(self.node_path(field.data_node), self.FIELDS_FNAME)

    def prov_json_path(self, file_group):
        return (op.join(self.node_path(file_group.data_node)
                        *(file_group.path.split('/')))
                + self.PROV_SUFFX)
                                 



def single_dataset(path: str, frequencies: Iterable[DataFrequency]=(
        Clinical.dataset,
        Clinical.subject,
        Clinical.session), **kwargs) -> Dataset:
    """
    Creates a Dataset from a file system path to a directory

    Parameters
    ----------
    path : str
        Path to directory containing the dataset
    frequencies : List[DataFrequency] | DataFrequency
        Defines the hierarchy of the dataset by the frequency of each of the
        layers of the tree. By default expects a 2 levels of sub-directories:
        outer directory->dataset, first-level->subject, second-level->session
    """
    if not isinstance(frequencies, Iterable):
        frequencies = [frequencies]
    return FileSystemDir(op.abspath(op.join(path, '..')),
                         frequencies, **kwargs).dataset(op.basename(path))