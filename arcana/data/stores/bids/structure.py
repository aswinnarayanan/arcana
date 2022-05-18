import typing as ty
import json
import re
from pathlib import Path
from arcana import __version__
from ..common import FileSystem
from arcana.core.data.format import FileGroup
from arcana.exceptions import ArcanaUsageError, ArcanaEmptyDatasetError


class Bids(FileSystem):
    """Repository for working with data stored on the file-system in BIDS format 
    """

    alias = "bids"

    def find_nodes(self, dataset):
        """
        Find all nodes within the dataset stored in the store and
        construct the data tree within the dataset

        Parameters
        ----------
        dataset : Dataset
            The dataset to construct the tree dimensions for
        """

        try:
            dataset.load_metadata()
        except ArcanaEmptyDatasetError:
            return

        for subject_id, participant in dataset.participants.items():
            try:
                explicit_ids = {'group': participant['group']}
            except KeyError:
                explicit_ids = {}
            if dataset.is_multi_session():
                for sess_id in (dataset.root_dir / subject_id).iterdir():
                    dataset.add_leaf_node([subject_id, sess_id],
                                          explicit_ids=explicit_ids)
            else:
                dataset.add_leaf_node([subject_id],
                                      explicit_ids=explicit_ids)

    def find_items(self, data_node):
        rel_session_path = self.node_path(data_node)
        root_dir = data_node.dataset.root_dir
        session_path = (root_dir / rel_session_path)
        session_path.mkdir(exist_ok=True)
        for modality_dir in session_path.iterdir():
            self.find_items_in_dir(modality_dir, data_node)
        deriv_dir = (root_dir / 'derivatives')
        if deriv_dir.exists():
            for pipeline_dir in deriv_dir.iterdir():
                self.find_items_in_dir(pipeline_dir / rel_session_path,
                                       data_node)        

    def file_group_stem_path(self, file_group):
        dn = file_group.data_node
        fs_path = self.root_dir(dn)
        parts = file_group.path.split('/')
        if parts[-1] == '':
            parts = parts[:-1]
        if is_derivative:= (parts[0] == 'derivatives'):
            if len(parts) < 2:
                raise ArcanaUsageError(
                    f"Derivative paths should have at least 3 parts ({file_group.path}")
            elif len(parts) == 2 and not file_group.is_dir:
                raise ArcanaUsageError(
                    "Derivative paths with 2 parts must be of type directory "
                    f"({file_group.path}")
            fs_path /= parts[0]
            fs_path /= parts[1]
            parts = parts[2:]
        if parts:  # The whole derivatives directories can be the output for a BIDS app
            fs_path /= self.node_path(dn)
            for part in parts[:-1]:
                fs_path /= part
            fname = '_'.join(dn.ids[h] for h in dn.dataset.hierarchy) + '_' + parts[-1]
            fs_path /= fname
        return fs_path

    def fields_json_path(self, field):
        parts = field.path.split('/')
        if parts[0] != 'derivatives':
            assert False, "Non-derivative fields should be taken from participants.tsv"
        return (field.data_node.dataset.root_dir.joinpath(parts[:2])
                / self.node_path(field.data_node) / self.FIELDS_FNAME)

    def get_field_val(self, field):
        data_node = field.data_node
        dataset = data_node.dataset
        if field.name in dataset.participant_attrs:
            val = dataset.participants[data_node.ids['subject']]
        else:
            val = super().get_field_val(field)
        return val

    def put_file_group_paths(self, file_group: FileGroup, fs_paths: ty.Iterable[Path]):
        # Ensure TaskName field is present in the JSON side-car if task is in
        # the filename
        stored_paths = super().put_file_group_paths(file_group, fs_paths)
        for fs_path in stored_paths:
            if fs_path.suffix == '.json':
                if match:= re.match(r'.*task-([a-zA-Z]+).*', fs_path.stem):
                    with open(fs_path) as f:
                        dct = json.load(f)
                    if 'TaskName' not in dct:
                        dct['TaskName'] = match.group(1)
                    with open(fs_path, 'w') as f:
                        json.dump(dct, f)
        return stored_paths


def outputs_converter(outputs):
    """Sets the path of an output to '' if not provided or None"""
    return [o[:2] + ('',) if len(o) < 3 or o[2] is None else o for o in outputs]

